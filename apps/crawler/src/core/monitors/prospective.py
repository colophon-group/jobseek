"""Prospective CareerCenter server-rendered listing monitor.

Prospective's branded CareerCenter pages paginate through a regular HTML
``POST`` form.  Some tenants also expose a public JSON ``medium`` endpoint,
which is handled by :mod:`api_sniffer`, but that endpoint is not available for
every medium.  This monitor uses the canonical form as a fail-closed fallback.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import register
from src.shared.http_retry import fetch_text_page_with_retry

if TYPE_CHECKING:
    import httpx


_MEDIUM_RE = re.compile(r"/careercenter/(?P<medium_id>\d+)/assets/")
_PAGINATION_RE = re.compile(r"\bsendPagination\((?P<offset>\d+)\)")
_FILTER_NAME_RE = re.compile(r"filter_\d+")
_MAX_PAGES = 200
_MAX_HTML_BYTES = 2 * 1024 * 1024


def _single_input_value(tree: LexborHTMLParser, name: str, default: str = "") -> str:
    nodes = tree.css(f'input[name="{name}"]')
    if len(nodes) > 1:
        raise ValueError(f"Prospective form contains duplicate {name!r} inputs")
    if not nodes:
        return default
    return nodes[0].attributes.get("value", default)


def _medium_id(html: str) -> str:
    match = _MEDIUM_RE.search(html)
    if match is None:
        raise ValueError("Prospective CareerCenter medium marker was not found")
    return match.group("medium_id")


def _validated_filters(value: object) -> dict[str, tuple[str, ...]]:
    if value is None:
        return {}
    if not isinstance(value, dict) or len(value) > 8:
        raise ValueError("Prospective filters must be a mapping with at most 8 fields")

    result: dict[str, tuple[str, ...]] = {}
    for name, raw_values in value.items():
        if not isinstance(name, str) or _FILTER_NAME_RE.fullmatch(name) is None:
            raise ValueError("Prospective filter names must match filter_<number>")
        values: Iterable[object]
        if isinstance(raw_values, str):
            values = (raw_values,)
        elif isinstance(raw_values, list):
            values = raw_values
        else:
            raise ValueError("Prospective filter values must be strings or lists of strings")
        normalized = tuple(values)
        if (
            not normalized
            or len(normalized) > 100
            or any(
                not isinstance(item, str) or not item or len(item) > 128 or "\x00" in item
                for item in normalized
            )
        ):
            raise ValueError("Prospective filter values must be 1-100 bounded strings")
        result[name] = normalized  # type: ignore[assignment]
    return result


def _validate_filter_options(
    tree: LexborHTMLParser,
    filters: dict[str, tuple[str, ...]],
) -> None:
    """Fail closed when a configured ownership/category allowlist drifts."""
    for name, selected in filters.items():
        selects = tree.css(f'select[name="{name}"]')
        if len(selects) != 1:
            raise ValueError(f"Prospective configured filter {name!r} was not found exactly once")
        available = {
            option.attributes.get("value", "") for option in selects[0].css("option[value]")
        }
        missing = set(selected) - available
        if missing:
            raise ValueError(
                f"Prospective configured filter {name!r} contains unavailable values: "
                f"{sorted(missing)!r}"
            )


def _parse_page(
    html: str,
    board_url: str,
    *,
    expected_medium_id: str | None,
    filters: dict[str, tuple[str, ...]],
) -> tuple[set[str], int, str, str]:
    medium_id = _medium_id(html)
    if expected_medium_id is not None and medium_id != expected_medium_id:
        raise ValueError(f"Prospective medium changed from {expected_medium_id!r} to {medium_id!r}")

    tree = LexborHTMLParser(html)
    form = tree.css_first("form#careercenter-form")
    if form is None or form.attributes.get("method", "").casefold() != "post":
        raise ValueError("Prospective CareerCenter POST form was not found")
    _validate_filter_options(tree, filters)

    parsed_board = urlparse(board_url)
    board_origin = (parsed_board.scheme.casefold(), parsed_board.netloc.casefold())
    urls: set[str] = set()
    for link in tree.css("#jobs-list a.job-title[href]"):
        candidate = urljoin(board_url, link.attributes["href"])
        parsed = urlparse(candidate)
        if (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
        ) != board_origin or not parsed.path.startswith("/offene-stellen/"):
            raise ValueError(f"Prospective listing returned an unexpected job URL: {candidate}")
        urls.add(candidate)

    current_offset_raw = _single_input_value(tree, "offset", "0")
    active_page = tree.css_first("#pagination .page.active[onclick]")
    if active_page is not None:
        active_match = _PAGINATION_RE.search(active_page.attributes.get("onclick", ""))
        if active_match is None:
            raise ValueError("Prospective active pagination control omitted its offset")
        current_offset_raw = active_match.group("offset")
    limit = _single_input_value(tree, "limit", "10")
    lang = _single_input_value(tree, "lang", "de")
    if not current_offset_raw.isdigit() or not limit.isdigit() or int(limit) < 1:
        raise ValueError("Prospective form returned invalid offset/limit values")
    current_offset = int(current_offset_raw)

    next_offsets = {
        int(match.group("offset"))
        for node in tree.css("#pagination [onclick]")
        if (match := _PAGINATION_RE.search(node.attributes.get("onclick", "")))
        and int(match.group("offset")) > current_offset
    }
    next_offset = min(next_offsets) if next_offsets else -1
    return urls, next_offset, limit, lang


def _form_data(
    *,
    offset: int,
    limit: str,
    lang: str,
    filters: dict[str, tuple[str, ...]],
) -> bytes:
    data: list[tuple[str, str]] = [
        ("offset", str(offset)),
        ("limit", limit),
        ("lang", lang),
        ("query", ""),
    ]
    for name, values in filters.items():
        data.extend((name, item) for item in values)
    return urlencode(data).encode()


async def _fetch_listing(
    client: httpx.AsyncClient,
    board_url: str,
    content: bytes | None = None,
) -> str:
    parsed = urlparse(board_url)
    headers = {"Accept": "text/html,application/xhtml+xml"}
    method = "GET"
    if content is not None:
        method = "POST"
        headers.update(
            {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": f"{parsed.scheme}://{parsed.netloc}",
                "Referer": board_url,
            }
        )
    html = await fetch_text_page_with_retry(
        client,
        board_url,
        method=method,
        content=content,
        headers=headers,
        end_of_pagination_statuses=(),
        retryable_statuses={403},
        require_nonempty=True,
        max_bytes=_MAX_HTML_BYTES,
        log_event="prospective.page_backoff",
    )
    if html is None:  # Strict status handling above makes this unreachable.
        raise RuntimeError("Prospective CareerCenter returned no document")
    return html


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Collect all filtered job URLs from a Prospective CareerCenter form."""
    _ = pw
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}
    filters = _validated_filters(metadata.get("filters"))
    expected_medium_id = metadata.get("medium_id")
    if expected_medium_id is not None and (
        not isinstance(expected_medium_id, str) or not expected_medium_id.isdigit()
    ):
        raise ValueError("Prospective medium_id must be a numeric string")

    shell = await _fetch_listing(client, board_url)
    _, _, limit, lang = _parse_page(
        shell,
        board_url,
        expected_medium_id=expected_medium_id,
        filters=filters,
    )

    offset = 0
    all_urls: set[str] = set()
    for _page_number in range(1, _MAX_PAGES + 1):
        html = await _fetch_listing(
            client,
            board_url,
            _form_data(offset=offset, limit=limit, lang=lang, filters=filters),
        )
        page_urls, next_offset, page_limit, page_lang = _parse_page(
            html,
            board_url,
            expected_medium_id=expected_medium_id,
            filters=filters,
        )
        if page_limit != limit or page_lang != lang:
            raise ValueError("Prospective form parameters changed during pagination")
        if next_offset >= 0 and not (page_urls - all_urls):
            raise ValueError("Prospective pagination made no progress")
        all_urls.update(page_urls)
        if next_offset < 0:
            return all_urls
        if next_offset <= offset:
            raise ValueError("Prospective pagination returned a non-increasing offset")
        offset = next_offset

    raise ValueError(f"Prospective pagination exceeded {_MAX_PAGES} pages")


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Recognize the provider's stable CareerCenter HTML contract."""
    _ = pw
    from src.core.monitors import fetch_page_text

    try:
        html = await fetch_page_text(url, client)
        if not html:
            return None
        medium_id = _medium_id(html)
        urls, _, limit, _ = _parse_page(
            html,
            url,
            expected_medium_id=medium_id,
            filters={},
        )
    except (ValueError, RuntimeError):
        return None
    return {"medium_id": medium_id, "page_size": int(limit), "urls": len(urls)}


register("prospective", discover, cost=10, can_handle=can_handle)
