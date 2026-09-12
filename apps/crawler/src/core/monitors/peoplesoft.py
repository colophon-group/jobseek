"""Oracle PeopleSoft Candidate Gateway monitor.

PeopleSoft Candidate Gateway requires an anonymous session cookie before its
public ``/psc/.../HRS_CG_SEARCH_FL.GBL`` listing becomes readable.  The
listing exposes row actions as JavaScript rather than anchors and grows its
result grid through stateful ``more`` form submissions, so the generic DOM
monitor cannot discover the stable detail URLs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser, LexborNode

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_HTML_CHARS = 10_000_000
_PATH_RE = re.compile(
    r"^/psc/(?P<site>[A-Za-z0-9_-]{1,64})/(?P<portal>[A-Za-z0-9_-]{1,64})/"
    r"HRMS/c/HRS_HRAM_FL\.HRS_CG_SEARCH_FL\.GBL$",
    re.IGNORECASE,
)
_JOB_ID_RE = re.compile(r"[1-9]\d{0,11}")
_COUNT_RE = re.compile(r"<b>\s*(?P<count>\d{1,6})\s*</b>\s*jobs?\s+found", re.IGNORECASE)
_GONE_STATUSES = frozenset({404, 410})
_MORE_ACTION = "HRS_AGNT_RSLT_I$hdown$0"
_MAX_GRID_REQUESTS = 1_001


@dataclass(frozen=True, slots=True)
class PeopleSoftBoard:
    origin: str
    site: str
    portal: str

    @property
    def component_path(self) -> str:
        return (
            f"/psc/{self.site}/{self.portal}/HRMS/c/"
            "HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"
        )

    @property
    def listing_url(self) -> str:
        return f"{self.origin}{self.component_path}?" + urlencode(
            {"Action": "U", "Page": "HRS_APP_SCHJOB_FL"}
        )

    @property
    def bootstrap_url(self) -> str:
        return f"{self.origin}/psp/{self.site}/{self.portal}/HRMS/?cmd=logout"

    def detail_url(self, job_id: str) -> str:
        return f"{self.origin}{self.component_path}?" + urlencode(
            {
                "Action": "U",
                "FOCUS": "Applicant",
                "JobOpeningId": job_id,
                "Page": "HRS_APP_JBPST_FL",
                "PostingSeq": "1",
                "SiteId": "1",
            }
        )


def peoplesoft_board_from_url(url: str) -> PeopleSoftBoard | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    match = _PATH_RE.fullmatch(parsed.path)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or match is None
    ):
        return None
    return PeopleSoftBoard(
        origin=f"https://{parsed.hostname.casefold()}",
        site=match.group("site"),
        portal=match.group("portal"),
    )


def peoplesoft_listing_from_url(url: str) -> PeopleSoftBoard | None:
    board = peoplesoft_board_from_url(url)
    if board is None:
        return None
    try:
        pairs = parse_qsl(urlparse(url).query, keep_blank_values=True, max_num_fields=6)
    except ValueError:
        return None
    params: dict[str, str] = {}
    for key, value in pairs:
        folded = key.casefold()
        if folded in params or folded not in {"action", "page"}:
            return None
        params[folded] = value
    if params.get("action", "U").casefold() != "u" or params.get(
        "page", "HRS_APP_SCHJOB_FL"
    ).casefold() != "hrs_app_schjob_fl":
        return None
    return board


def peoplesoft_job_from_url(url: str) -> tuple[PeopleSoftBoard, str] | None:
    board = peoplesoft_board_from_url(url)
    if board is None:
        return None
    try:
        pairs = parse_qsl(urlparse(url).query, keep_blank_values=True, max_num_fields=12)
    except ValueError:
        return None
    params: dict[str, str] = {}
    for key, value in pairs:
        if key.casefold() in {existing.casefold() for existing in params}:
            return None
        params[key] = value
    lowered = {key.casefold(): value for key, value in params.items()}
    job_id = lowered.get("jobopeningid")
    if (
        job_id is None
        or _JOB_ID_RE.fullmatch(job_id) is None
        or lowered.get("action", "U").casefold() != "u"
        or lowered.get("page", "").casefold() != "hrs_app_jbpst_fl"
        or lowered.get("postingseq", "1") != "1"
        or lowered.get("siteid", "1") != "1"
    ):
        return None
    return board, job_id


async def _fetch_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    log_event: str,
    follow_redirects: bool = False,
) -> str:
    try:
        page = await fetch_text_page_with_retry(
            client,
            url,
            follow_redirects=follow_redirects,
            require_nonempty=True,
            max_chars=MAX_HTML_CHARS,
            retryable_statuses={401, 403, 429},
            end_of_pagination_statuses=(),
            log_event=log_event,
        )
    except PaginationFetchError as exc:
        if exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "PeopleSoft Candidate Gateway page no longer exists",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise
    if page is None:
        raise RuntimeError(f"PeopleSoft returned no page for {url}")
    return page


async def fetch_peoplesoft_page(
    board: PeopleSoftBoard,
    url: str,
    client: httpx.AsyncClient,
) -> str:
    """Establish an anonymous Candidate Gateway session, then fetch a page."""

    await _fetch_page(
        client,
        board.bootstrap_url,
        log_event="peoplesoft.bootstrap_backoff",
        follow_redirects=True,
    )
    return await _fetch_page(client, url, log_event="peoplesoft.page_backoff")


def _value(row: LexborNode, prefix: str) -> str | None:
    node = row.css_first(f"span[id^='{prefix}$']")
    if node is None:
        return None
    value = " ".join(node.text(strip=True).split())
    return value or None


def _parse_listing_page(
    page: str, board: PeopleSoftBoard
) -> tuple[list[DiscoveredJob], int]:
    if "HRS_AGNT_RSLT_I" not in page or "Search Results List" not in page:
        raise ValueError("PeopleSoft response omitted the public search-result grid")
    count_match = _COUNT_RE.search(page)
    if count_match is None:
        raise ValueError("PeopleSoft response omitted the authoritative job count")
    expected = int(count_match.group("count"))
    document = LexborHTMLParser(page)
    jobs: list[DiscoveredJob] = []
    seen_ids: set[str] = set()
    for row in document.css("li[id^='HRS_AGNT_RSLT_I$0_row_']"):
        job_id = _value(row, "HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID")
        title = _value(row, "SCH_JOB_TITLE")
        location = _value(row, "LOCATION")
        posted = _value(row, "SCH_OPENED")
        if not job_id or _JOB_ID_RE.fullmatch(job_id) is None or not title or not location:
            raise ValueError("PeopleSoft result row omitted a required job field")
        if job_id in seen_ids:
            raise ValueError(f"PeopleSoft result grid repeated job ID {job_id}")
        seen_ids.add(job_id)
        try:
            date_posted = (
                datetime.strptime(posted, "%m/%d/%Y").date().isoformat()
                if posted
                else None
            )
        except ValueError as exc:
            raise ValueError(f"PeopleSoft job {job_id} had an invalid posting date") from exc
        metadata = {
            key: value
            for key, value in {
                "job_id": job_id,
                "department": _value(row, "HRS_APP_JBSCH_I_HRS_DEPT_DESCR"),
                "job_family": _value(row, "JOB_FAMILY_LABEL"),
            }.items()
            if value
        }
        jobs.append(
            DiscoveredJob(
                url=board.detail_url(job_id),
                title=title,
                locations=[location],
                date_posted=date_posted,
                metadata=metadata,
            )
        )
    return jobs, expected


def parse_listing(page: str, board: PeopleSoftBoard) -> list[DiscoveredJob]:
    jobs, expected = _parse_listing_page(page, board)
    if len(jobs) != expected:
        raise ValueError(
            f"PeopleSoft result count mismatch: advertised {expected}, parsed {len(jobs)}"
        )
    return jobs


def _continuation_form(page: str) -> list[tuple[str, str]] | None:
    document = LexborHTMLParser(page)
    more = document.css_first("div.ps_box-more")
    if more is None or _MORE_ACTION not in (more.attributes.get("onclick") or ""):
        return None

    fields: list[tuple[str, str]] = []
    for node in document.css("form input[name]"):
        attributes = node.attributes
        field_type = (attributes.get("type") or "text").casefold()
        if "disabled" in attributes or field_type in {
            "button",
            "file",
            "image",
            "reset",
            "submit",
        }:
            continue
        if field_type in {"checkbox", "radio"} and "checked" not in attributes:
            continue
        name = attributes.get("name")
        if not name or name in {"ICAction", "ICFocus", "ICXPos", "ICYPos"}:
            continue
        fields.append((name, attributes.get("value") or ""))
    fields.extend(
        (
            ("ICAction", _MORE_ACTION),
            ("ICFocus", _MORE_ACTION),
            ("ICXPos", "0"),
            ("ICYPos", "0"),
        )
    )
    return fields


async def fetch_all_listings(
    board: PeopleSoftBoard,
    client: httpx.AsyncClient,
) -> list[DiscoveredJob]:
    """Fetch the cumulative Candidate Gateway grid until its count is complete."""

    page = await fetch_peoplesoft_page(board, board.listing_url, client)
    previous_size = -1
    for request_number in range(_MAX_GRID_REQUESTS):
        jobs, expected = _parse_listing_page(page, board)
        if expected > MAX_JOBS:
            raise ValueError(
                f"PeopleSoft advertised {expected} jobs, exceeding cap {MAX_JOBS}"
            )
        if len(jobs) == expected:
            return jobs
        if len(jobs) <= previous_size:
            raise ValueError(
                "PeopleSoft continuation did not increase the result-grid size"
            )
        if len(jobs) > expected:
            raise ValueError(
                f"PeopleSoft parsed {len(jobs)} jobs but advertised only {expected}"
            )
        previous_size = len(jobs)
        fields = _continuation_form(page)
        if fields is None:
            raise ValueError(
                f"PeopleSoft result count mismatch: advertised {expected}, "
                f"parsed {len(jobs)}, and no continuation was available"
            )
        response = await client.post(
            board.listing_url,
            content=urlencode(fields).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        if response.status_code in _GONE_STATUSES:
            raise BoardGoneError(
                "PeopleSoft Candidate Gateway page no longer exists",
                url=board.listing_url,
                status_code=response.status_code,
            )
        response.raise_for_status()
        page = response.text
        if not page or len(page) > MAX_HTML_CHARS:
            raise ValueError("PeopleSoft continuation returned an invalid response body")
        log.debug(
            "peoplesoft.continued",
            request=request_number + 1,
            parsed=len(jobs),
            expected=expected,
        )
    raise ValueError("PeopleSoft exceeded the result-grid continuation limit")


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    _ = pw
    identity = peoplesoft_listing_from_url(board["board_url"])
    if identity is None:
        raise ValueError(f"Invalid PeopleSoft Candidate Gateway URL: {board['board_url']!r}")
    jobs = await fetch_all_listings(identity, client)
    log.info("peoplesoft.discovered", origin=identity.origin, jobs=len(jobs))
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    _ = pw
    identity = peoplesoft_listing_from_url(url)
    if identity is None or client is None:
        return None
    try:
        jobs = await fetch_all_listings(identity, client)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("peoplesoft.probe_failed", url=url, exc_info=True)
        return None
    return {"jobs": len(jobs)}


register("peoplesoft", discover, cost=10, can_handle=can_handle, rich=True)
