"""Beisen modern API and legacy server-rendered listing monitor.

Modern ``*.zhiye.com`` portals expose full job records through their public
same-origin listing API. Older portals render paginated job tables (or an
expanded ``/index`` list); those variants return partial rich records and
reuse Jobseek's DOM scraper only for description enrichment.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.core.monitors.dom import _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.beisen import (
    BeisenBoard,
    beisen_board_from_metadata,
    beisen_board_from_url,
    beisen_tenant_from_url,
    extract_beisen_bootstrap,
    normalize_beisen_job_id,
)
from src.shared.html_normalize import normalize_description_html
from src.shared.http_retry import (
    PaginationFetchError,
    fetch_json_page_with_retry,
    fetch_text_page_with_retry,
)
from src.shared.tdm import TDMReservedError

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

PAGE_SIZE = 1_000
MAX_JOBS = 50_000
MAX_PAGES = MAX_JOBS // PAGE_SIZE
MAX_HTML_CHARS = 2_000_000
_MODERN_DISPLAY_FIELDS = [
    "Category",
    "Kind",
    "LocId",
    "DetailAddress",
    "Org",
    "PostDate",
    "Salary",
    "Degree",
    "YearsOfWorking",
]
_GONE_STATUSES = frozenset({404, 410})
_TRANSIENT_STATUSES = frozenset({202, 401, 403})
_LEGACY_MARKER_RE = re.compile(r"\b_splash\([^)]*['\"]new_zhiye_com['\"]", re.IGNORECASE)
_PAGE_INDEX_RE = re.compile(r"[?&](?:amp;)?PageIndex=(\d+)(?=[&#\"'\s]|$)", re.IGNORECASE)
_CURRENT_TOTAL_RE = re.compile(r"当前第\s*\d+\s*/\s*(\d+)\s*页")
_CURRENT_PAGE_RE = re.compile(
    r"class=['\"][^'\"]*\b(?:now|current)\b[^'\"]*['\"][^>]*>\s*(\d+)\s*<",
    re.IGNORECASE,
)
_RECORD_TOTAL_RE = re.compile(r"共\s*([\d,]+)\s*条记录")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PUBLIC_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_PAGE_PATTERNS = [
    re.compile(
        r"(https://[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.zhiye\.com"
        r"(?:/(?:Social|social|index|jobs|SocialList|CampusList|InternList))?/?"
        r"(?:\?[^\"'<\s]*)?)(?=[#\"'<\s]|$)",
        re.IGNORECASE,
    )
]


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(html.unescape(value).split())
    return cleaned or None


def _safe_date(value: object) -> str | None:
    raw = _clean_string(value)
    if raw is None or raw.startswith("0001-"):
        return None
    candidate = raw[:10]
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def _text_html(value: object) -> str | None:
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return None
    paragraphs: list[str] = []
    for part in re.split(r"\n\s*\n+", html.unescape(raw).replace("\r\n", "\n")):
        lines = [
            html.escape(line.strip(), quote=False) for line in part.split("\n") if line.strip()
        ]
        if lines:
            paragraphs.append(f"<p>{'<br>\n'.join(lines)}</p>")
    return "\n".join(paragraphs) or None


def _description(raw: dict) -> str | None:
    sections: list[str] = []
    for heading, key in (("Responsibilities", "Duty"), ("Requirements", "Require")):
        body = _text_html(raw.get(key))
        if body:
            sections.extend((f"<h3>{heading}</h3>", body))
    return normalize_description_html("\n".join(sections)) if sections else None


def _metadata(raw: dict) -> dict[str, object]:
    fields = {
        "job_ad_id": "JobAdId",
        "category": "Category",
        "category_id": "CategoryId",
        "organization": "Org",
        "organization_id": "OrgId",
        "degree": "Degree",
        "years_of_working": "YearsOfWorking",
        "salary": "Salary",
    }
    metadata: dict[str, object] = {}
    for output, source in fields.items():
        value = raw.get(source)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            metadata[output] = value
    return metadata


def _legacy_job_id_from_href(href: str, tenant: str) -> str | None:
    try:
        parsed = urlparse(html.unescape(href))
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=2)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"", "https"}
        or (host and host != f"{tenant}.zhiye.com")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or len(pairs) > 1
        or any(name.casefold() != "pageindex" or not value.isdigit() for name, value in pairs)
    ):
        return None
    match = re.fullmatch(r"/zpdetail/([1-9]\d{0,18})/?", parsed.path, re.IGNORECASE)
    return normalize_beisen_job_id(match.group(1)) if match else None


def _parse_modern_job(raw: object, board: BeisenBoard) -> DiscoveredJob | None:
    if not isinstance(raw, dict) or raw.get("Status") not in {None, 1}:
        return None
    public_id = raw.get("Id")
    category_id = _clean_string(raw.get("CategoryId"))
    title = _clean_string(raw.get("JobAdName"))
    if (
        not isinstance(public_id, str)
        or _PUBLIC_ID_RE.fullmatch(public_id) is None
        or category_id is None
        or title is None
    ):
        return None
    raw_locations = raw.get("LocNames")
    locations: list[str] = []
    seen: set[str] = set()
    if isinstance(raw_locations, list):
        for value in raw_locations:
            location = _clean_string(value)
            key = location.casefold() if location else None
            if location and key not in seen:
                seen.add(location.casefold())
                locations.append(location)
    detail_address = _clean_string(raw.get("DetailAddress"))
    if not locations and detail_address:
        locations.append(detail_address)
    return DiscoveredJob(
        url=board.modern_job_url(public_id, category_id),
        title=title,
        description=_description(raw),
        locations=locations or None,
        employment_type=_clean_string(raw.get("Kind")),
        date_posted=_safe_date(raw.get("PostDate")),
        metadata=_metadata(raw),
    )


class _LegacyParser(HTMLParser):
    def __init__(self, board: BeisenBoard) -> None:
        super().__init__(convert_charrefs=True)
        self.board = board
        self.jobs: list[DiscoveredJob] = []
        self._row_depth = 0
        self._row_cells: list[str] = []
        self._cell_depth = 0
        self._cell_text: list[str] = []
        self._job_id: str | None = None
        self._job_title: str | None = None
        self._li_depth = 0
        self._li_spans: list[str] = []
        self._span_depth = 0
        self._span_text: list[str] = []
        self._inline_job_id: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        tag = tag.casefold()
        if self.board.legacy_template == "standard":
            if tag == "tr":
                self._row_depth += 1
                if self._row_depth == 1:
                    self._row_cells = []
                    self._job_id = None
                    self._job_title = None
            elif self._row_depth and tag == "td":
                self._cell_depth += 1
                if self._cell_depth == 1:
                    self._cell_text = []
            elif self._row_depth and tag == "a":
                href = values.get("href") or ""
                job_id = _legacy_job_id_from_href(href, self.board.tenant)
                if job_id is not None:
                    self._job_id = job_id
                    self._job_title = _clean_string(values.get("title"))
            return

        if tag == "li":
            self._li_depth += 1
            if self._li_depth == 1:
                self._li_spans = []
                self._inline_job_id = None
        elif self._li_depth and tag == "span":
            self._span_depth += 1
            if self._span_depth == 1:
                self._span_text = []
        if self._li_depth:
            self._inline_job_id = (
                normalize_beisen_job_id(values.get("jobadid")) or self._inline_job_id
            )

    def handle_data(self, data: str) -> None:
        if self._cell_depth:
            self._cell_text.append(data)
        if self._span_depth:
            self._span_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self.board.legacy_template == "standard":
            if tag == "td" and self._cell_depth:
                self._cell_depth -= 1
                if self._cell_depth == 0:
                    self._row_cells.append(_clean_string("".join(self._cell_text)) or "")
            elif tag == "tr" and self._row_depth:
                self._row_depth -= 1
                if self._row_depth == 0:
                    self._finish_standard_row()
            return

        if tag == "span" and self._span_depth:
            self._span_depth -= 1
            if self._span_depth == 0:
                self._li_spans.append(_clean_string("".join(self._span_text)) or "")
        elif tag == "li" and self._li_depth:
            self._li_depth -= 1
            if self._li_depth == 0:
                self._finish_inline_row()

    def _finish_standard_row(self) -> None:
        if self._job_id is None:
            return
        job_id = self._job_id
        date_index = next(
            (index for index, value in enumerate(self._row_cells) if _DATE_RE.fullmatch(value)),
            None,
        )
        location = self._row_cells[date_index - 1] if date_index and date_index > 0 else ""
        self.jobs.append(
            DiscoveredJob(
                url=self.board.legacy_job_url(job_id),
                title=self._job_title or (self._row_cells[0] if self._row_cells else None),
                locations=[location] if location else None,
                date_posted=(
                    _safe_date(self._row_cells[date_index]) if date_index is not None else None
                ),
                metadata={"job_ad_id": int(job_id)},
            )
        )

    def _finish_inline_row(self) -> None:
        if self._inline_job_id is None or len(self._li_spans) < 5:
            return
        location = self._li_spans[3]
        self.jobs.append(
            DiscoveredJob(
                url=self.board.legacy_job_url(self._inline_job_id),
                title=self._li_spans[0] or None,
                locations=[location] if location else None,
                date_posted=_safe_date(self._li_spans[4]),
                metadata={"job_ad_id": int(self._inline_job_id)},
            )
        )


def _legacy_page_jobs(page: str, board: BeisenBoard) -> tuple[list[DiscoveredJob], int]:
    if _LEGACY_MARKER_RE.search(page) is None:
        raise ValueError(f"Beisen tenant {board.tenant!r} returned a non-legacy page")
    parser = _LegacyParser(board)
    parser.feed(page)
    parser.close()
    linked_max = max((int(value) for value in _PAGE_INDEX_RE.findall(page)), default=1)
    current_total = _CURRENT_TOTAL_RE.search(page)
    current_page = _CURRENT_PAGE_RE.search(page)
    max_page = max(
        linked_max,
        int(current_total.group(1)) if current_total else 1,
        int(current_page.group(1)) if current_page else 1,
    )
    return parser.jobs, max(1, max_page)


def _legacy_record_total(page: str) -> int | None:
    match = _RECORD_TOTAL_RE.search(page)
    return int(match.group(1).replace(",", "")) if match else None


async def _fetch_html(
    url: str,
    client: httpx.AsyncClient,
    *,
    board_gone_on_terminal: bool = False,
) -> str:
    try:
        page = await fetch_text_page_with_retry(
            client,
            url,
            require_nonempty=True,
            max_chars=MAX_HTML_CHARS + 1,
            follow_redirects=False,
            end_of_pagination_statuses=(),
            retryable_statuses=_TRANSIENT_STATUSES,
            log_event="beisen.page_backoff",
        )
    except PaginationFetchError as exc:
        if board_gone_on_terminal and exc.last_status in _GONE_STATUSES:
            raise BoardGoneError("Beisen board no longer exists", url=url) from exc
        raise
    if page is None:
        raise RuntimeError(f"Beisen listing fetch returned no page for {url!r}")
    _raise_if_bot_challenge(url, page)
    return page


async def _bootstrap(tenant: str, client: httpx.AsyncClient) -> tuple[str, BeisenBoard | None]:
    url = f"https://{tenant}.zhiye.com/"
    page = await _fetch_html(url, client, board_gone_on_terminal=True)
    if len(page) > MAX_HTML_CHARS:
        raise ValueError(f"Beisen tenant {tenant!r} bootstrap exceeded the HTML safety cap")
    bootstrap = extract_beisen_bootstrap(page, tenant)
    if bootstrap is None:
        return page, None
    board, enabled = bootstrap
    if not enabled:
        raise BoardGoneError("Beisen portal is disabled", url=url)
    return page, board


async def _modern_page(
    board: BeisenBoard,
    client: httpx.AsyncClient,
    page_index: int,
    *,
    page_size: int = PAGE_SIZE,
) -> dict:
    if board.portal_id is None:
        raise ValueError(f"Beisen tenant {board.tenant!r} is missing its portal ID")
    payload = await fetch_json_page_with_retry(
        client,
        board.api_url(),
        expect_shape=dict,
        method="POST",
        json_body={
            "PageIndex": page_index,
            "PageSize": page_size,
            "KeyWords": "",
            "SpecialType": 0,
            "PortalId": board.portal_id,
            # Beisen only populates optional list fields when they are requested.
            # In particular, asking for LocId populates LocNames even when LocId
            # itself remains null.
            "DisplayFields": _MODERN_DISPLAY_FIELDS,
        },
        follow_redirects=False,
        retryable_statuses=_TRANSIENT_STATUSES,
        log_event="beisen.api_backoff",
    )
    if payload.get("Code") != 200 or not isinstance(payload.get("Data"), list):
        raise ValueError(f"Beisen tenant {board.tenant!r} returned an invalid listing payload")
    count = payload.get("Count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"Beisen tenant {board.tenant!r} omitted a valid listing count")
    return payload


async def _discover_modern(board: BeisenBoard, client: httpx.AsyncClient) -> MonitorResult:
    from src.core.monitor import MonitorResult

    first = await _modern_page(board, client, 0)
    total = first["Count"]
    page_count = max(1, math.ceil(total / PAGE_SIZE))
    truncated = total > MAX_JOBS or page_count > MAX_PAGES
    jobs: dict[str, DiscoveredJob] = {}

    def merge(page_index: int, payload: dict) -> None:
        nonlocal truncated
        rows = payload["Data"]
        if payload["Count"] != total or len(rows) > PAGE_SIZE:
            truncated = True
        expected = min(PAGE_SIZE, max(total - page_index * PAGE_SIZE, 0))
        if len(rows) != expected:
            truncated = True
        for raw in rows:
            job = _parse_modern_job(raw, board)
            if job is None or job.url in jobs:
                truncated = True
                continue
            jobs[job.url] = job

    merge(0, first)
    for page_index in range(1, min(page_count, MAX_PAGES)):
        merge(page_index, await _modern_page(board, client, page_index))
    if len(jobs) != min(total, MAX_JOBS):
        truncated = True
    if truncated and not jobs and total:
        raise ValueError(f"Beisen tenant {board.tenant!r} produced an incomplete empty listing")
    return MonitorResult(urls=set(jobs), jobs_by_url=jobs, truncated=truncated)


async def _discover_legacy(board: BeisenBoard, client: httpx.AsyncClient) -> MonitorResult:
    from src.core.monitor import MonitorResult

    first_page = await _fetch_html(
        board.listing_url(),
        client,
        board_gone_on_terminal=True,
    )
    first_jobs, advertised_pages = _legacy_page_jobs(first_page[:MAX_HTML_CHARS], board)
    advertised_total = _legacy_record_total(first_page)
    truncated = (
        len(first_page) > MAX_HTML_CHARS
        or advertised_pages > MAX_JOBS
        or (advertised_total is not None and advertised_total > MAX_JOBS)
    )
    jobs: dict[str, DiscoveredJob] = {}

    def merge(page_number: int, page_jobs: list[DiscoveredJob]) -> None:
        nonlocal truncated
        if page_number > 1 and not page_jobs:
            raise ValueError(
                f"Beisen tenant {board.tenant!r} returned an empty advertised page {page_number}"
            )
        for job in page_jobs:
            if job.url in jobs:
                truncated = True
            jobs[job.url] = job

    merge(1, first_jobs)
    page_limit = min(advertised_pages, MAX_JOBS)
    for page_number in range(2, page_limit + 1):
        separator = "&" if "?" in board.listing_url() else "?"
        page = await _fetch_html(
            f"{board.listing_url()}{separator}PageIndex={page_number}",
            client,
        )
        page_jobs, page_max = _legacy_page_jobs(page[:MAX_HTML_CHARS], board)
        page_total = _legacy_record_total(page)
        if (
            page_max != advertised_pages
            or len(page) > MAX_HTML_CHARS
            or page_total != advertised_total
        ):
            truncated = True
        merge(page_number, page_jobs)
        if len(jobs) >= MAX_JOBS:
            truncated = True
            break
    if not jobs and advertised_pages > 1:
        raise ValueError(f"Beisen tenant {board.tenant!r} produced an incomplete empty listing")
    if advertised_total is not None and len(jobs) != min(advertised_total, MAX_JOBS):
        truncated = True
    return MonitorResult(
        urls=set(jobs),
        jobs_by_url=jobs,
        hybrid=True,
        truncated=truncated,
    )


async def _resolved_board(board: dict, client: httpx.AsyncClient) -> BeisenBoard:
    metadata = board.get("metadata") or {}
    configured = beisen_board_from_metadata(metadata) if isinstance(metadata, dict) else None
    direct = beisen_board_from_url(board["board_url"])
    if configured is not None and direct is not None and configured.tenant != direct.tenant:
        raise ValueError(
            f"Configured Beisen tenant {configured.tenant!r} does not match "
            f"board URL tenant {direct.tenant!r}"
        )
    tenant = configured.tenant if configured is not None else direct.tenant if direct else None
    if tenant is None:
        raise ValueError(f"Cannot derive Beisen tenant from board URL {board['board_url']!r}")
    root, modern = await _bootstrap(tenant, client)
    if modern is not None:
        if configured is not None and configured.variant != "modern":
            raise ValueError(f"Beisen tenant {tenant!r} changed from legacy to modern")
        if configured is not None and (
            configured.portal_id != modern.portal_id or configured.tenant_id != modern.tenant_id
        ):
            raise ValueError(f"Beisen tenant {tenant!r} live portal identity changed")
        return modern
    if configured is not None and configured.variant == "legacy":
        return configured
    path = urlparse(board["board_url"]).path
    folded = path.rstrip("/").casefold()
    if folded in {"/social", "/index"}:
        template = "inline" if folded == "/index" else "standard"
        return BeisenBoard(
            tenant=tenant,
            variant="legacy",
            listing_path="/index" if template == "inline" else "/Social",
            legacy_template=template,
        )
    if _LEGACY_MARKER_RE.search(root):
        linked = re.search(r"href=['\"](/(?:Social|social|index))/?['\"]", root, re.IGNORECASE)
        if linked:
            template = "inline" if linked.group(1).casefold() == "/index" else "standard"
            return BeisenBoard(
                tenant=tenant,
                variant="legacy",
                listing_path="/index" if template == "inline" else "/Social",
                legacy_template=template,
            )
    raise ValueError(f"Beisen tenant {tenant!r} did not expose a supported portal")


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> MonitorResult:
    _ = pw
    resolved = await _resolved_board(board, client)
    result = (
        await _discover_modern(resolved, client)
        if resolved.variant == "modern"
        else await _discover_legacy(resolved, client)
    )
    log_method = log.warning if result.truncated else log.info
    log_method(
        "beisen.discovered",
        tenant=resolved.tenant,
        variant=resolved.variant,
        jobs=len(result.urls),
        truncated=result.truncated,
    )
    return result


@dataclass(slots=True)
class _ProbeContext:
    metadata: dict[str, object] | None = None


async def _probe_candidate(
    token: str,
    client: httpx.AsyncClient,
    context: _ProbeContext,
) -> ProbeResult:
    root_url = token
    tenant = beisen_tenant_from_url(root_url)
    if tenant is None:
        return False, None
    try:
        root, modern = await _bootstrap(tenant, client)
        if modern is not None:
            payload = await _modern_page(modern, client, 0, page_size=1)
            context.metadata = {
                "tenant": tenant,
                "variant": "modern",
                "portal_id": modern.portal_id,
                "tenant_id": modern.tenant_id,
            }
            return True, payload["Count"]

        parsed = urlparse(root_url)
        candidates = [parsed.path]
        linked = re.search(r"href=['\"](/(?:Social|social|index))/?['\"]", root, re.IGNORECASE)
        if linked:
            candidates.append(linked.group(1))
        for path in candidates:
            folded = path.rstrip("/").casefold()
            if folded not in {"/social", "/index"}:
                continue
            template = "inline" if folded == "/index" else "standard"
            board = BeisenBoard(
                tenant=tenant,
                variant="legacy",
                listing_path="/index" if template == "inline" else "/Social",
                legacy_template=template,
            )
            page = await _fetch_html(board.listing_url(), client)
            jobs, pages = _legacy_page_jobs(page[:MAX_HTML_CHARS], board)
            context.metadata = {
                "tenant": tenant,
                "variant": "legacy",
                "listing_path": board.listing_path,
                "legacy_template": template,
            }
            total = _legacy_record_total(page)
            count: ProbeCount = (
                total if total is not None else (len(jobs) if pages == 1 else f"{pages} pages")
            )
            return True, count
    except TDMReservedError:
        raise
    except Exception:
        log.debug("beisen.probe_failed", root_url=root_url, exc_info=True)
    return False, None


async def _fetch_job_count(
    token: str,
    client: httpx.AsyncClient,
    context: _ProbeContext,
) -> ProbeCount | None:
    found, count = await _probe_candidate(token, client, context)
    return count if found else None


def _root_from_url(url: str) -> str | None:
    tenant = beisen_tenant_from_url(html.unescape(url))
    if tenant is None:
        return None
    path = urlparse(html.unescape(url)).path.rstrip("/")
    if path.casefold() in {"/social", "/index"}:
        return f"https://{tenant}.zhiye.com{path}"
    return f"https://{tenant}.zhiye.com/"


def _root_from_match(match: re.Match[str], context: _ProbeContext) -> _ProbeContext:
    _ = match
    return context


def _build_result(
    root_url: str,
    count: ProbeCount | None,
    context: _ProbeContext,
) -> dict:
    _ = root_url
    if context.metadata is None:
        raise ValueError("Beisen result builder received no verified portal metadata")
    result = dict(context.metadata)
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    _ = pw
    if client is None:
        return None
    return await ats_can_handle(
        url,
        client,
        monitor_name="beisen",
        token_from_url=_root_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=frozenset(),
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_candidate,
        initial_context=_ProbeContext(),
        result_builder=_build_result,
        context_from_match=_root_from_match,
        page_token_probe=_probe_candidate,
        require_direct_count=True,
        allow_slug_guess=False,
        log_token_field="root_url",
    )


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    configured = beisen_board_from_metadata(metadata)
    direct = beisen_board_from_url(board_url)
    tenant = configured.tenant if configured is not None else direct.tenant if direct else None
    if tenant is None:
        return
    await save_text_response(
        artifact_dir,
        client,
        f"https://{tenant}.zhiye.com/",
        filename="beisen-bootstrap.html",
        follow_redirects=False,
    )
    if configured is not None and configured.variant == "legacy":
        await save_text_response(
            artifact_dir,
            client,
            configured.listing_url(),
            filename="beisen-listing.html",
            follow_redirects=False,
        )


register(
    "beisen",
    discover,
    cost=10,
    can_handle=can_handle,
    rich=True,
    save_raw=save_raw,
)
