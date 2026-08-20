"""Talemetry / Jobvite Career Sites listing monitor.

Talemetry career sites render authoritative job links in a
``.jobs-section__list`` container and expose a textual result range such as
``Showing 1-25 of 85 results`` (or TTC Portals' ``Viewing`` variant).
Pagination uses ``?page=N``.

The strict count/range checks are intentional.  Returning a partial URL set
from a failed later page would make gone detection retire still-live jobs.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import structlog

from src.core.monitors import fetch_page_text, register
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import fetch_with_retry

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_URLS = 50_000
_MAX_PAGES = 5_000
_DEFAULT_PAGE_CHARS = 5_000_000
_MAX_PAGE_CHARS = 25_000_000
_SNAPSHOT_ATTEMPTS = 2
_SNAPSHOT_RETRY_DELAY = 1.0
_PROVIDER_MARKERS = ("window.talemetry", "talemetry_careersites")
_RESULT_COUNT_RE = re.compile(
    r"\b(?:Showing|Viewing)\s+([\d,]+)\s*-\s*([\d,]+)\s+of\s+([\d,]+)\s+results\b",
    re.IGNORECASE,
)
_JOB_PATH_RE = re.compile(r"^/jobs/\d+(?:-[^/?#]+)?/?$", re.IGNORECASE)
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)


class _SnapshotChanged(ValueError):
    """The board inventory changed while its paginated snapshot was collected."""


def _to_int(value: str) -> int:
    return int(value.replace(",", ""))


def _classes(attrs: dict[str, str]) -> frozenset[str]:
    return frozenset(attrs.get("class", "").split())


@dataclass(slots=True)
class _ParsedPage:
    urls: set[str] = field(default_factory=set)
    range_start: int | None = None
    range_end: int | None = None
    total_jobs: int | None = None
    provider_marked: bool = False
    saw_results_list: bool = False


class _TalemetryParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.base = urlparse(base_url)
        self.urls: set[str] = set()
        self.text: list[str] = []
        self._results_depth = 0
        self.saw_results_list = False
        self.provider_marked = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        classes = _classes(attr)
        if "jobs-section__list" in classes:
            self.saw_results_list = True
            self._results_depth = 1
        elif self._results_depth and tag.lower() not in _VOID_TAGS:
            self._results_depth += 1

        if tag.lower() != "a" or not self._results_depth:
            return
        href = attr.get("href")
        if not href:
            return
        absolute = urljoin(self.base_url, href)
        parsed = urlparse(absolute)
        if (
            parsed.scheme == self.base.scheme
            and parsed.netloc.casefold() == self.base.netloc.casefold()
            and _JOB_PATH_RE.fullmatch(parsed.path)
        ):
            self.urls.add(urlunparse(parsed._replace(query="", fragment="")))

    def handle_endtag(self, tag: str) -> None:
        if self._results_depth and tag.lower() not in _VOID_TAGS:
            self._results_depth -= 1

    def handle_data(self, data: str) -> None:
        self.text.append(data)
        folded = data.casefold()
        if any(marker in folded for marker in _PROVIDER_MARKERS):
            self.provider_marked = True

    def parsed(self) -> _ParsedPage:
        joined = " ".join(self.text)
        match = _RESULT_COUNT_RE.search(joined)
        if match is None:
            return _ParsedPage(
                urls=self.urls,
                provider_marked=self.provider_marked,
                saw_results_list=self.saw_results_list,
            )
        return _ParsedPage(
            urls=self.urls,
            range_start=_to_int(match.group(1)),
            range_end=_to_int(match.group(2)),
            total_jobs=_to_int(match.group(3)),
            provider_marked=self.provider_marked,
            saw_results_list=self.saw_results_list,
        )


def _parse_page(html: str, base_url: str) -> _ParsedPage:
    parser = _TalemetryParser(base_url)
    parser.feed(html)
    parser.close()
    return parser.parsed()


def _page_url(board_url: str, page: int) -> str:
    if page <= 1:
        return board_url
    parsed = urlparse(board_url)
    params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "page"
    ]
    params.append(("page", str(page)))
    return urlunparse(parsed._replace(query=urlencode(params), fragment=""))


def _page_max_chars(metadata: dict) -> int:
    raw = metadata.get("page_max_chars")
    if raw is None:
        return _DEFAULT_PAGE_CHARS
    try:
        configured = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Talemetry page_max_chars must be an integer") from exc
    return max(1, min(configured, _MAX_PAGE_CHARS))


def _max_pages(metadata: dict) -> int:
    raw = metadata.get("max_pages")
    try:
        return _MAX_PAGES if raw is None else min(int(raw), _MAX_PAGES)
    except (TypeError, ValueError) as exc:
        raise ValueError("Talemetry max_pages must be an integer") from exc


def _validate_page(
    parsed: _ParsedPage,
    *,
    board_url: str,
    page: int,
    expected_total: int | None = None,
    expected_start: int | None = None,
    expected_end: int | None = None,
) -> None:
    if not parsed.provider_marked or not parsed.saw_results_list:
        raise ValueError(f"Talemetry listing markers missing on page {page} at {board_url}")
    if parsed.range_start is None or parsed.range_end is None or parsed.total_jobs is None:
        raise ValueError(f"Talemetry result count missing on page {page} at {board_url}")

    start, end, total = parsed.range_start, parsed.range_end, parsed.total_jobs
    if total == 0:
        if page != 1 or start != 0 or end != 0 or parsed.urls:
            raise ValueError(f"Talemetry zero-result range is inconsistent at {board_url}")
        return
    if start < 1 or end < start or end > total:
        raise ValueError(f"Talemetry result range {start}-{end} of {total} is invalid")
    if len(parsed.urls) != end - start + 1:
        count = len(parsed.urls)
        raise ValueError(f"Talemetry page {page} exposed {count} URLs for range {start}-{end}")
    if expected_total is not None and total != expected_total:
        raise _SnapshotChanged(f"Talemetry total changed: {expected_total} -> {total}")
    if expected_start is not None and start != expected_start:
        raise _SnapshotChanged(
            f"Talemetry page {page} starts at {start}, expected {expected_start}"
        )
    if expected_end is not None and end != expected_end:
        raise _SnapshotChanged(f"Talemetry page {page} ends at {end}, expected {expected_end}")


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect a server-rendered Talemetry Career Sites result page."""
    _ = pw
    if client is None:
        return None
    html = await fetch_page_text(url, client, max_chars=_DEFAULT_PAGE_CHARS)
    if not html:
        return None
    parsed = _parse_page(html, url)
    try:
        _validate_page(parsed, board_url=url, page=1)
    except ValueError:
        return None

    total = parsed.total_jobs or 0
    page_size = parsed.range_end or 0
    pages = math.ceil(total / page_size) if total else 1
    return {"urls": len(parsed.urls), "jobs": total, "pages": pages}


async def _discover_once(
    board: dict,
    client: httpx.AsyncClient,
) -> set[str]:
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}
    max_chars = _page_max_chars(metadata)
    first_html = await fetch_with_retry(
        client,
        board_url,
        max_chars=max_chars,
        transient_403=True,
    )
    if first_html is None:
        raise ValueError(f"Talemetry listing fetch returned no page at {board_url}")

    first = _parse_page(first_html, board_url)
    _validate_page(first, board_url=board_url, page=1)
    total = first.total_jobs or 0
    if total == 0:
        return set()
    if total > MAX_URLS:
        raise ValueError(f"Talemetry inventory {total} exceeds URL cap {MAX_URLS}")

    page_size = first.range_end or 0
    pages = math.ceil(total / page_size)
    max_pages = _max_pages(metadata)
    if pages > max_pages:
        raise ValueError(f"Talemetry inventory needs {pages} pages, above max_pages={max_pages}")

    urls = set(first.urls)
    for page in range(2, pages + 1):
        page_url = _page_url(board_url, page)
        html = await fetch_with_retry(
            client,
            page_url,
            max_chars=max_chars,
            transient_403=True,
        )
        if html is None:
            raise ValueError(f"Talemetry pagination page {page} returned no content")
        parsed = _parse_page(html, page_url)
        expected_start = (page - 1) * page_size + 1
        expected_end = min(page * page_size, total)
        _validate_page(
            parsed,
            board_url=board_url,
            page=page,
            expected_total=total,
            expected_start=expected_start,
            expected_end=expected_end,
        )
        overlap = urls & parsed.urls
        if overlap:
            raise _SnapshotChanged(
                f"Talemetry page {page} repeated {len(overlap)} earlier job URLs"
            )
        urls.update(parsed.urls)

    if len(urls) != total:
        raise _SnapshotChanged(f"Talemetry discovered {len(urls)} URLs, expected {total}")
    log.info("talemetry.complete", board_url=board_url, urls_found=len(urls), pages=pages)
    return urls


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> set[str]:
    """Discover a complete inventory, retrying one internally inconsistent snapshot."""
    _ = pw
    board_url = board["board_url"]
    for attempt in range(1, _SNAPSHOT_ATTEMPTS + 1):
        try:
            return await _discover_once(board, client)
        except _SnapshotChanged as exc:
            if attempt == _SNAPSHOT_ATTEMPTS:
                raise
            log.warning(
                "talemetry.snapshot_changed",
                board_url=board_url,
                attempt=attempt,
                error=str(exc),
            )
            await asyncio.sleep(_SNAPSHOT_RETRY_DELAY)
    raise AssertionError("unreachable")


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    _ = metadata
    await save_text_response(
        artifact_dir,
        client,
        board_url,
        filename="page.html",
        follow_redirects=True,
    )


register("talemetry", discover, cost=45, can_handle=can_handle, save_raw=save_raw)
