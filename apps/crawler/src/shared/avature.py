"""Pure Avature public-portal identity and HTML parsing helpers.

Avature career portals can live on ``*.avature.net`` or on branded custom
domains.  Runtime detection therefore validates both the URL shape and the
``avature.portal.*`` metadata emitted by the public listing before trusting a
custom host.  This module is intentionally network-free so the crawler,
workspace workflow, and lightweight board probe share the same identity
rules.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

_VENDOR_HOST_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.avature\.net$",
    re.IGNORECASE,
)
_ID_RE = re.compile(r"[1-9]\d{0,19}")
_SAFE_SEGMENT_RE = re.compile(r"[^/?#\x00-\x20]{1,160}")
_LISTING_PAGES = frozenset({"searchjobs", "searchjobsmaps"})
_DETAIL_PAGES = {
    "jobdetail": ("SearchJobs", "jobId"),
    "folderdetail": ("SearchJobs", "folderId"),
    "pipelinedetail": ("SearchJobsMaps", "pipelineId"),
}
_PAGINATION_PAIRS = {
    frozenset({"jobRecordsPerPage", "jobOffset"}): ("jobRecordsPerPage", "jobOffset"),
    frozenset({"folderRecordsPerPage", "folderOffset"}): (
        "folderRecordsPerPage",
        "folderOffset",
    ),
    frozenset({"pipelineOffset"}): (None, "pipelineOffset"),
    frozenset({"pipelineRecordsPerPage", "pipelineOffset"}): (
        "pipelineRecordsPerPage",
        "pipelineOffset",
    ),
}


def _safe_host(value: str | None, *, vendor_only: bool) -> str | None:
    if not value:
        return None
    host = value.casefold().rstrip(".")
    if vendor_only:
        return host if _VENDOR_HOST_RE.fullmatch(host) else None
    if (
        host
        in {
            "avature.net",
            "localhost",
            "localhost.localdomain",
            "www.avature.net",
        }
        or "." not in host
    ):
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return None


def _safe_url_parts(url: str, *, vendor_only: bool):
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = _safe_host(parsed.hostname, vendor_only=vendor_only)
    if (
        host is None
        or parsed.scheme.casefold() != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return parsed, host


def _path_segments(path: str) -> list[str] | None:
    segments = [segment for segment in path.split("/") if segment]
    if not segments or any(_SAFE_SEGMENT_RE.fullmatch(segment) is None for segment in segments):
        return None
    return segments


@dataclass(frozen=True, slots=True)
class AvatureBoard:
    """Stable identity of one public Avature portal."""

    host: str
    prefix: str
    page: str = "SearchJobs"

    @property
    def listing_url(self) -> str:
        return f"https://{self.host}{self.prefix}/{self.page}"


def avature_board_from_url(
    url: str,
    *,
    allow_custom_host: bool = False,
) -> AvatureBoard | None:
    """Derive an unscoped listing identity from a listing or detail URL.

    Custom domains are shape-only candidates and must additionally be checked
    with :func:`parse_avature_page` before they are trusted.
    """

    safe = _safe_url_parts(url, vendor_only=not allow_custom_host)
    if safe is None:
        return None
    parsed, host = safe
    segments = _path_segments(parsed.path)
    if segments is None:
        return None

    lowered = [segment.casefold() for segment in segments]
    route_index = next(
        (index for index, segment in enumerate(lowered) if segment in _LISTING_PAGES),
        None,
    )
    if route_index is not None:
        if route_index != len(segments) - 1 or parsed.query:
            return None
        page = "SearchJobsMaps" if lowered[route_index] == "searchjobsmaps" else "SearchJobs"
        prefix = "/" + "/".join(segments[:route_index])
        return AvatureBoard(host=host, prefix=prefix.rstrip("/"), page=page)

    detail_index = next(
        (index for index, segment in enumerate(lowered) if segment in _DETAIL_PAGES),
        None,
    )
    if detail_index is None or detail_index == 0:
        return None
    detail_name = lowered[detail_index]
    listing_page, query_key = _DETAIL_PAGES[detail_name]
    tail = segments[detail_index + 1 :]
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if tail:
        if len(tail) < 2 or _ID_RE.fullmatch(tail[-1]) is None or query:
            return None
    elif len(query) != 1 or query[0][0] != query_key or _ID_RE.fullmatch(query[0][1]) is None:
        return None
    prefix = "/" + "/".join(segments[:detail_index])
    return AvatureBoard(host=host, prefix=prefix.rstrip("/"), page=listing_page)


def avature_board_from_metadata(metadata: Mapping[str, object]) -> AvatureBoard | None:
    listing_url = metadata.get("listing_url")
    if not isinstance(listing_url, str):
        return None
    return avature_board_from_url(listing_url, allow_custom_host=True)


def avature_request_host(board_url: str, metadata: Mapping[str, object]) -> str | None:
    """Return the host that the monitor will actually request.

    Legacy company URLs can remain in ``board_url`` while ``ws`` records the
    validated Avature listing in metadata.  Rate limiting and circuit breaking
    must follow that configured listing host rather than the legacy URL.
    """

    configured = avature_board_from_metadata(metadata)
    direct = avature_board_from_url(board_url, allow_custom_host=True)
    board = configured or direct
    return board.host if board is not None else None


def is_avature_vendor_url(url: str) -> bool:
    return avature_board_from_url(url) is not None


@dataclass(frozen=True, slots=True)
class AvaturePage:
    board: AvatureBoard
    portal_id: str
    total: int | None
    total_exact: bool
    range_start: int | None
    range_end: int | None
    jobs: dict[str, str]
    next_urls: tuple[str, ...]


class _AvatureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.portal: dict[str, str] = {}
        self.og_url: str | None = None
        self.hrefs: list[str] = []
        self.next_hrefs: list[str] = []
        self.legend_labels: list[str] = []
        self.legend_text: list[str] = []
        self._scope_stack: list[tuple[str, bool, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        values = {key.casefold(): value or "" for key, value in attrs}
        class_names = {part.casefold() for part in values.get("class", "").split()}
        parent_next = self._scope_stack[-1][1] if self._scope_stack else False
        parent_legend = self._scope_stack[-1][2] if self._scope_stack else False
        in_next = parent_next or "paginationnextlink" in class_names
        in_legend = parent_legend or bool(
            {"list-controls__text__legend", "pagination__legend"} & class_names
        )

        if tag == "meta":
            name = values.get("name", "")
            if name.casefold().startswith("avature.portal."):
                self.portal[name.casefold().removeprefix("avature.portal.")] = values.get(
                    "content", ""
                )
            if values.get("property", "").casefold() == "og:url":
                self.og_url = values.get("content") or None
            return

        self._scope_stack.append((tag, in_next, in_legend))
        if tag == "a" and values.get("href"):
            self.hrefs.append(values["href"])
            if in_next:
                self.next_hrefs.append(values["href"])
        if in_legend and values.get("aria-label"):
            self.legend_labels.append(values["aria-label"])

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self._scope_stack and self._scope_stack[-1][0] == tag.casefold():
            self._scope_stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self._scope_stack) - 1, -1, -1):
            if self._scope_stack[index][0] == tag:
                del self._scope_stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._scope_stack and self._scope_stack[-1][2]:
            self.legend_text.append(data)


def _canonical_detail(url: str, board: AvatureBoard) -> tuple[str, str] | None:
    safe = _safe_url_parts(url, vendor_only=False)
    if safe is None:
        return None
    parsed, host = safe
    if host != board.host:
        return None
    prefix = board.prefix.rstrip("/")
    if not parsed.path.casefold().startswith(f"{prefix}/".casefold()):
        return None
    remainder = parsed.path[len(prefix) :].strip("/")
    segments = [segment for segment in remainder.split("/") if segment]
    if not segments:
        return None
    route = segments[0].casefold()
    if route not in _DETAIL_PAGES:
        return None
    _listing_page, query_key = _DETAIL_PAGES[route]
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(segments) == 1:
        if len(query) != 1 or query[0][0] != query_key or _ID_RE.fullmatch(query[0][1]) is None:
            return None
        job_id = query[0][1]
        canonical = f"https://{board.host}{prefix}/{segments[0]}?{urlencode({query_key: job_id})}"
    else:
        if len(segments) < 3 or _ID_RE.fullmatch(segments[-1]) is None:
            return None
        job_id = segments[-1]
        canonical = f"https://{board.host}{prefix}/{'/'.join(segments)}"
    return f"{route}:{job_id}", canonical


def avature_pagination_url(
    url: str,
    board: AvatureBoard,
    *,
    allow_zero_offset: bool = False,
) -> tuple[str, int] | None:
    absolute = urljoin(board.listing_url, url)
    safe = _safe_url_parts(absolute, vendor_only=False)
    if safe is None:
        return None
    parsed, host = safe
    if (
        host != board.host
        or parsed.path.rstrip("/").casefold() != urlparse(board.listing_url).path.casefold()
    ):
        return None
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    keys = [key for key, _value in pairs]
    key_set = frozenset(keys)
    if len(keys) != len(set(keys)) or key_set not in _PAGINATION_PAIRS:
        return None
    size_key, offset_key = _PAGINATION_PAIRS[key_set]
    values = dict(pairs)
    if (size_key is not None and not values[size_key].isdigit()) or not values[
        offset_key
    ].isdigit():
        return None
    size = int(values[size_key]) if size_key is not None else None
    offset = int(values[offset_key])
    if (size is not None and size <= 0) or offset < 0 or (offset == 0 and not allow_zero_offset):
        return None
    params = [(offset_key, offset)]
    if size_key is not None and size is not None:
        params.insert(0, (size_key, size))
    canonical = f"{board.listing_url}/?{urlencode(params)}"
    return canonical, offset


def _count_marker(labels: list[str], text: str) -> tuple[int | None, bool]:
    candidates = labels + ([text] if text.strip() else [])
    for value in candidates:
        matches = list(re.finditer(r"(?<!\d)(\d[\d,.\s]*)(\+)?", value))
        if not matches:
            continue
        match = matches[-1]
        digits = re.sub(r"\D", "", match.group(1))
        if digits:
            return int(digits), match.group(2) is None
    return None, False


def parse_avature_page(html: str, request_url: str) -> AvaturePage | None:
    """Parse and validate one server-rendered Avature listing page."""

    parser = _AvatureParser()
    parser.feed(html)
    portal_id = parser.portal.get("id", "").strip()
    page_name = parser.portal.get("page", "").strip()
    canonical_hint = unescape(parser.og_url or request_url)
    parsed_hint = urlparse(canonical_hint)
    listing_hint = parsed_hint._replace(query="", fragment="").geturl()
    board = avature_board_from_url(listing_hint, allow_custom_host=True)
    if parsed_hint.query and (
        board is None
        or avature_pagination_url(canonical_hint, board, allow_zero_offset=True) is None
    ):
        return None
    if (
        board is None
        or not portal_id.isdigit()
        or int(portal_id) <= 0
        or page_name.casefold() != board.page.casefold()
    ):
        return None

    jobs: dict[str, str] = {}
    for href in parser.hrefs:
        resolved = _canonical_detail(urljoin(board.listing_url, href), board)
        if resolved is None:
            continue
        identity, canonical = resolved
        previous = jobs.get(identity)
        if previous is None or ("?" in previous and "?" not in canonical):
            jobs[identity] = canonical

    next_urls: set[str] = set()
    for href in parser.next_hrefs:
        resolved = avature_pagination_url(href, board)
        if resolved is not None:
            next_urls.add(resolved[0])

    legend = " ".join(part.strip() for part in parser.legend_text if part.strip())
    total, exact = _count_marker(parser.legend_labels, legend)
    range_match = re.search(r"(?<!\d)(\d[\d,.]*)\s*[-–]\s*(\d[\d,.]*)(?!\d)", legend)
    range_start = int(re.sub(r"\D", "", range_match.group(1))) if range_match else None
    range_end = int(re.sub(r"\D", "", range_match.group(2))) if range_match else None
    return AvaturePage(
        board=board,
        portal_id=str(int(portal_id)),
        total=total,
        total_exact=exact,
        range_start=range_start,
        range_end=range_end,
        jobs=jobs,
        next_urls=tuple(sorted(next_urls)),
    )
