"""Candidatus / WinDev career-site monitor.

Candidatus listings render complete job cards but expose their detail pages
only through WinDev JavaScript postbacks.  The card anchors therefore have
``javascript:`` hrefs rather than crawlable URLs.  This adapter clicks each
advertised card in one browser session and records the stable
``/annonce-emploi,...`` URL reached by the postback.
"""

from __future__ import annotations

import contextlib
import re
from urllib.parse import urlsplit, urlunsplit

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import fetch_page_text, register
from src.core.monitors.dom import _raise_if_bot_challenge
from src.shared.browser import BROWSER_KEYS, navigate, open_page, safe_content

log = structlog.get_logger()

_HOST = "carrieres.candidatus.com"
_LISTING_PATH_RE = re.compile(r"^/site-emploi,[A-Za-z0-9_-]+(?:;[A-Za-z0-9_-]+)*/?$")
_DETAIL_PATH_RE = re.compile(r"^/annonce-emploi,[^/?#]+$")
_CARD_ID_RE = re.compile(r"^c-(\d+)-A20$")
_CARD_VALUE_RE = re.compile(r"\b_PAGE_\.A18\.value=(\d+)\b")
_LISTING_MARKERS = ("RECRUTEMENT_LISTEANNONCES_XMOD10", "_JSL(_PAGE_")
_MAX_JOBS = 1_000


def _is_listing_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == _HOST
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
        and not parsed.query
        and not parsed.fragment
        and _LISTING_PATH_RE.fullmatch(parsed.path) is not None
    )


def _canonical_detail_url(url: str) -> str | None:
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() != _HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or _DETAIL_PATH_RE.fullmatch(parsed.path) is None
    ):
        return None
    return urlunsplit(("https", _HOST, parsed.path, "", ""))


def _card_indexes(html: str, *, strict: bool = False) -> list[int]:
    """Return WinDev row indexes in document order after validating cards.

    Probes may inspect a partially matching page leniently, but discovery must
    reject any malformed title control.  Silently skipping one would turn an
    upstream markup change into an authoritative partial inventory.
    """
    if not all(marker in html for marker in _LISTING_MARKERS):
        return []
    document = LexborHTMLParser(html)
    indexes: list[int] = []
    for anchor in document.css('a[id$="-A20"]'):
        identifier = anchor.attributes.get("id", "")
        href = anchor.attributes.get("href", "")
        id_match = _CARD_ID_RE.fullmatch(identifier)
        value_match = _CARD_VALUE_RE.search(href)
        if id_match is None or value_match is None or id_match.group(1) != value_match.group(1):
            if strict:
                raise RuntimeError("Candidatus listing has a malformed WinDev job-title control")
            continue
        indexes.append(int(id_match.group(1)))
    return indexes


async def _listing_indexes(page, board_url: str, config: dict) -> list[int]:
    await navigate(page, board_url, config)
    html = await safe_content(page)
    _raise_if_bot_challenge(page.url or board_url, html)
    indexes = _card_indexes(html, strict=True)
    if not indexes:
        raise RuntimeError("Candidatus listing returned no WinDev job cards")
    if len(indexes) != len(set(indexes)):
        raise RuntimeError("Candidatus listing returned duplicate WinDev row indexes")
    return indexes


async def _discover_page(page, board_url: str, config: dict) -> set[str]:
    expected_indexes = await _listing_indexes(page, board_url, config)
    configured_cap = int(config.get("max_jobs", _MAX_JOBS))
    if configured_cap < 1 or configured_cap > _MAX_JOBS:
        raise ValueError(f"Candidatus max_jobs must be between 1 and {_MAX_JOBS}")
    if len(expected_indexes) > configured_cap:
        raise RuntimeError(
            f"Candidatus listing has {len(expected_indexes)} jobs, above max_jobs={configured_cap}"
        )

    timeout = min(60_000, max(1_000, int(config.get("timeout", 30_000))))
    urls: set[str] = set()
    for position, index in enumerate(expected_indexes):
        if position:
            current_indexes = await _listing_indexes(page, board_url, config)
            if current_indexes != expected_indexes:
                raise RuntimeError("Candidatus listing changed while resolving WinDev postbacks")

        locator = page.locator(f"#c-{index}-A20").first
        if await locator.count() != 1:
            raise RuntimeError(f"Candidatus card {index} is missing its job-title control")
        await locator.click()
        await page.wait_for_url(
            lambda target: _canonical_detail_url(str(target)) is not None,
            wait_until="domcontentloaded",
            timeout=timeout,
        )
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=min(timeout, 10_000))
        canonical = _canonical_detail_url(page.url)
        if canonical is None:
            raise RuntimeError(f"Candidatus card {index} did not reach a stable detail URL")
        if canonical in urls:
            raise RuntimeError(f"Candidatus card {index} resolved to duplicate URL {canonical!r}")
        urls.add(canonical)

    if len(urls) != len(expected_indexes):
        raise RuntimeError(
            f"Candidatus count mismatch: resolved {len(urls)} of {len(expected_indexes)} jobs"
        )
    log.info("candidatus.complete", board_url=board_url, jobs=len(urls))
    return urls


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Resolve every live Candidatus card to its stable detail URL."""
    _ = client
    board_url = board["board_url"]
    if not _is_listing_url(board_url):
        raise ValueError(f"Unsupported Candidatus listing URL: {board_url!r}")

    metadata = board.get("metadata") or {}
    browser_config = {key: value for key, value in metadata.items() if key in BROWSER_KEYS}
    browser_config.setdefault("wait", "domcontentloaded")
    browser_config.setdefault("timeout", 30_000)

    async def _run(playwright) -> set[str]:
        async with open_page(
            playwright,
            browser_config,
            use_proxy=bool(metadata.get("proxy")),
            target_url=board_url,
        ) as page:
            return await _discover_page(page, board_url, metadata | browser_config)

    if pw is not None:
        return await _run(pw)

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError("playwright is required for the Candidatus monitor") from exc

    async with async_playwright() as playwright:
        return await _run(playwright)


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Recognize a public Candidatus WinDev listing and count its cards."""
    _ = pw
    if not _is_listing_url(url):
        return None
    html = await fetch_page_text(url, client, max_chars=2_000_000)
    if html is None:
        return None
    indexes = _card_indexes(html)
    if not indexes:
        return None
    return {
        "wait": "domcontentloaded",
        "timeout": 30_000,
        "jobs": len(indexes),
    }


register("candidatus", discover, cost=60, can_handle=can_handle)
