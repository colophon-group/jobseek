"""Strict identity and HTML helpers for Taleo Business Edition boards."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

_HOST_RE = re.compile(r"^(?P<cluster>[a-z]{3})\.tbe\.taleo\.net$")
_PARTITION_RE = re.compile(r"^[a-z]{3}[0-9]{2}$")
_ORG_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,63}$")
_POSITIVE_INT_RE = re.compile(r"^[1-9][0-9]{0,9}$")
_LISTING_PATH_RE = re.compile(
    r"/(?P<partition>[a-z]{3}[0-9]{2})/ats/careers/v2/searchResults/?",
    re.IGNORECASE,
)
_DETAIL_PATH_RE = re.compile(
    r"/(?P<partition>[a-z]{3}[0-9]{2})/ats/careers/v2/viewRequisition/?",
    re.IGNORECASE,
)
_DISPATCHER_PATH = "/dispatcher/servlet/DispatcherServlet"


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 9_999_999_999 else None
    if not isinstance(value, str) or _POSITIVE_INT_RE.fullmatch(value.strip()) is None:
        return None
    return int(value)


def _org(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().upper()
    return normalized if _ORG_RE.fullmatch(normalized) is not None else None


def _host_and_partition(host: object, partition: object) -> tuple[str, str] | None:
    if not isinstance(host, str) or not isinstance(partition, str):
        return None
    normalized_host = host.strip().lower()
    normalized_partition = partition.strip().lower()
    host_match = _HOST_RE.fullmatch(normalized_host)
    if (
        host_match is None
        or _PARTITION_RE.fullmatch(normalized_partition) is None
        or not normalized_partition.startswith(host_match.group("cluster"))
    ):
        return None
    return normalized_host, normalized_partition


@dataclass(frozen=True, slots=True)
class TaleoBoard:
    host: str
    partition: str
    org: str
    cws: int

    def listing_url(self, *, row_from: int | None = None) -> str:
        params: dict[str, object] = {"org": self.org, "cws": self.cws}
        if row_from is not None:
            if (
                isinstance(row_from, bool)
                or not isinstance(row_from, int)
                or row_from < 0
                or row_from > 49_990
                or row_from % 10
            ):
                raise ValueError(f"Invalid Taleo row offset: {row_from!r}")
            params["rowFrom"] = row_from
        query = urlencode(params)
        return f"https://{self.host}/{self.partition}/ats/careers/v2/searchResults?{query}"

    def job_url(self, requisition_id: int) -> str:
        normalized = _positive_int(requisition_id)
        if normalized is None:
            raise ValueError(f"Invalid Taleo requisition ID: {requisition_id!r}")
        query = urlencode({"org": self.org, "cws": self.cws, "rid": normalized})
        return f"https://{self.host}/{self.partition}/ats/careers/v2/viewRequisition?{query}"


def taleo_board_from_metadata(metadata: Mapping[str, object]) -> TaleoBoard | None:
    host_partition = _host_and_partition(metadata.get("host"), metadata.get("partition"))
    org = _org(metadata.get("org"))
    cws = _positive_int(metadata.get("cws"))
    if host_partition is None or org is None or cws is None:
        return None
    host, partition = host_partition
    return TaleoBoard(host, partition, org, cws)


def _url_parts(
    url: str,
) -> tuple[TaleoBoard, str, int | None, int | None] | None:
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlparse(html.unescape(url))
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=8)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None

    params: dict[str, str] = {}
    for key, value in pairs:
        if key in params:
            return None
        params[key] = value

    listing_match = _LISTING_PATH_RE.fullmatch(parsed.path)
    detail_match = _DETAIL_PATH_RE.fullmatch(parsed.path)
    match = listing_match or detail_match
    if match is None:
        return None
    host_partition = _host_and_partition(parsed.hostname or "", match.group("partition"))
    org = _org(params.get("org"))
    cws = _positive_int(params.get("cws"))
    if host_partition is None or org is None or cws is None:
        return None
    host, partition = host_partition
    board = TaleoBoard(host, partition, org, cws)

    if listing_match is not None:
        if not set(params).issubset({"org", "cws", "rowFrom"}):
            return None
        row_from: int | None = None
        if "rowFrom" in params:
            raw_offset = params["rowFrom"]
            if not raw_offset.isdigit():
                return None
            row_from = int(raw_offset)
            if row_from < 0 or row_from > 49_990 or row_from % 10:
                return None
        return board, "listing", None, row_from

    if set(params) != {"org", "cws", "rid"}:
        return None
    requisition_id = _positive_int(params.get("rid"))
    return (board, "detail", requisition_id, None) if requisition_id is not None else None


def taleo_board_from_url(url: str) -> TaleoBoard | None:
    parts = _url_parts(url)
    return parts[0] if parts is not None else None


def taleo_listing_board_from_url(url: str) -> TaleoBoard | None:
    parts = _url_parts(url)
    return parts[0] if parts is not None and parts[1] == "listing" else None


def _taleo_first_listing_board_from_url(url: str) -> TaleoBoard | None:
    """Return the board only for its unpaginated, canonical first-page route."""
    parts = _url_parts(url)
    if parts is None or parts[1] != "listing" or parts[3] is not None:
        return None
    return parts[0]


def taleo_request_host(board_url: str, metadata: Mapping[str, object]) -> str | None:
    """Return the validated Taleo host that a configured monitor will request.

    Resolved metadata intentionally wins over the source URL: Taleo moves
    tenants between clusters, while the CSV board URL remains a useful public
    entry point. The monitor uses the resolved identity for every request, so
    schedulers and the host circuit must use that same host.
    """
    configured = taleo_board_from_metadata(metadata)
    if configured is not None:
        return configured.host
    direct = taleo_board_from_url(board_url)
    return direct.host if direct is not None else None


def taleo_requisition_id(url: str, board: TaleoBoard) -> int | None:
    parts = _url_parts(url)
    if parts is None or parts[0] != board or parts[1] != "detail":
        return None
    return parts[2]


def taleo_safe_redirect(
    board: TaleoBoard,
    request_url: str,
    location: str | None,
) -> tuple[str, TaleoBoard] | None:
    """Resolve one same-organization Taleo migration redirect.

    Taleo moves tenants between official clusters through a dispatcher. The
    intermediate URL is accepted only when it embeds the exact listing that
    initiated the redirect; the eventual target may change cluster,
    partition, and career-web-site ID, but never organization.
    """
    if not location:
        return None
    target = urljoin(request_url, html.unescape(location))
    listing = _taleo_first_listing_board_from_url(target)
    if listing is not None:
        return (listing.listing_url(), listing) if listing.org == board.org else None

    try:
        parsed = urlparse(target)
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=4)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or _HOST_RE.fullmatch(host) is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path != _DISPATCHER_PATH
        or parsed.fragment
    ):
        return None
    params: dict[str, str] = {}
    for key, value in pairs:
        if key in params:
            return None
        params[key] = value
    if set(params) != {"org", "act", "redirectUrl"}:
        return None
    embedded = _taleo_first_listing_board_from_url(params["redirectUrl"])
    if _org(params["org"]) != board.org or params["act"] != "redirectCws" or embedded != board:
        return None
    return target, board


class _TotalParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.current: list[str] = []
        self.values: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth:
            self.depth += 1
            return
        if tag != "span":
            return
        classes = next((value for key, value in attrs if key == "class"), None)
        if classes and "oracletaleocwsv2-panel-number" in classes.split():
            self.depth = 1
            self.current = []

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.current.append(data)

    def handle_endtag(self, tag: str) -> None:
        _ = tag
        if not self.depth:
            return
        self.depth -= 1
        if self.depth:
            return
        raw = "".join(self.current).strip().replace(",", "")
        if raw.isdigit():
            self.values.append(int(raw))


def taleo_total_from_html(document: str) -> int | None:
    parser = _TotalParser()
    parser.feed(document)
    values = set(parser.values)
    return next(iter(values)) if len(values) == 1 else None


class _NextLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        values = dict(attrs)
        classes = values.get("class") or ""
        if "jscroll-next" in classes.split():
            self.hrefs.append(values.get("href"))


def _next_offset(href: str, board: TaleoBoard) -> int | None:
    # HTMLParser already resolves character references in attribute values.
    # A second unescape would corrupt ``&currentTime`` into the ``&curren``
    # entity followed by ``tTime``.
    target = urljoin(board.listing_url(), href)
    try:
        parsed = urlparse(target)
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=8)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != board.host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path.rstrip("/") != f"/{board.partition}/ats/careers/v2/searchResults"
        or parsed.fragment
    ):
        return None
    params: dict[str, str] = {}
    for key, value in pairs:
        if key in params:
            return None
        params[key] = value
    if not {"next", "rowFrom"}.issubset(params) or not set(params).issubset(
        {"next", "rowFrom", "act", "sortColumn", "sortOrder", "currentTime"}
    ):
        return None
    if params["next"] or not params["rowFrom"].isdigit():
        return None
    if any(params.get(key, "null") != "null" for key in ("act", "sortColumn", "sortOrder")):
        return None
    current_time = params.get("currentTime")
    if current_time is not None and not current_time.isdigit():
        return None
    offset = int(params["rowFrom"])
    return offset if 10 <= offset <= 50_000 and offset % 10 == 0 else None


def taleo_next_offset_from_html(
    document: str,
    board: TaleoBoard,
    *,
    current_offset: int,
) -> int | None:
    """Return the validated next offset for Taleo's no-total theme."""
    parser = _NextLinkParser()
    parser.feed(document)
    if not parser.hrefs:
        return None
    offsets: set[int] = set()
    for href in parser.hrefs:
        offset = _next_offset(href, board) if href is not None else None
        if offset is None:
            raise ValueError("Taleo listing exposed a malformed next-page cursor")
        offsets.add(offset)
    if len(offsets) != 1:
        raise ValueError("Taleo listing exposed conflicting next-page cursors")
    offset = next(iter(offsets))
    if offset != current_offset + 10:
        raise ValueError("Taleo listing exposed a non-sequential next-page cursor")
    return offset


def taleo_listing_marker_from_html(document: str) -> bool:
    return "oracletaleocwsv2" in document.casefold()


def taleo_inactive_redirect(
    board: TaleoBoard,
    request_url: str,
    location: str | None,
) -> bool:
    """Recognize Taleo's dispatcher-only inactive-tenant tombstone."""
    if not location or not location.startswith("INACTIVEcareers/v2/searchResults?"):
        return False
    # The request must itself be the validated dispatcher hop for this board.
    dispatcher = taleo_safe_redirect(board, board.listing_url(), request_url)
    if dispatcher is None or dispatcher[0] != request_url:
        return False
    try:
        pairs = parse_qsl(location.partition("?")[2], keep_blank_values=True, max_num_fields=3)
    except ValueError:
        return False
    params: dict[str, str] = {}
    for key, value in pairs:
        if key in params:
            return False
        params[key] = value
    return (
        set(params) == {"org", "cws"}
        and _org(params["org"]) == board.org
        and _positive_int(params["cws"]) == board.cws
    )
