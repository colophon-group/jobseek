"""PCRecruiter server-rendered POST-pagination monitor.

The public board renders twelve jobs per page and moves between pages by
posting provider-issued ``pcr-id`` and ``unifiedsearch`` tokens.  Adding a GET
page parameter does not paginate this provider and can instead return a newly
shuffled first page forever, so this adapter follows and validates the actual
form contract.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import httpx
import structlog
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import BoardGoneError, register
from src.core.monitors.dom import _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.pcrecruiter import (
    PCRecruiterBoard,
    pcrecruiter_board_from_metadata,
    pcrecruiter_board_from_url,
)
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_PAGES = 5_000
MAX_HTML_BYTES = 2_000_000
_GONE_STATUSES = frozenset({404, 410})
_RESULT_RE = re.compile(r"\s*(\d+)\s*-\s*(\d+)\s+of\s+(\d+)\s*", re.IGNORECASE)
_RECORD_ID_RE = re.compile(r"[0-9]{1,32}")
_REQUIRED_FORM_FIELDS = frozenset(
    {"action", "showjobs", "pcr-id", "morecount", "sortorder", "unifiedsearch"}
)


@dataclass(frozen=True, slots=True)
class _Page:
    start: int
    end: int
    total: int
    jobs: frozenset[str]
    post_url: str
    form: dict[str, str]


def _board_identity(board: dict) -> PCRecruiterBoard:
    metadata = board.get("metadata") or {}
    configured = pcrecruiter_board_from_metadata(metadata) if metadata else None
    direct = pcrecruiter_board_from_url(board["board_url"])
    if metadata and configured is None:
        raise ValueError("Invalid PCRecruiter monitor configuration")
    if configured is not None and direct is not None and configured.uid != direct.uid:
        raise ValueError(
            f"Configured PCRecruiter uid {configured.uid!r} does not match "
            f"board URL uid {direct.uid!r}"
        )
    resolved = configured or direct
    if resolved is None:
        raise ValueError(
            f"Cannot derive a PCRecruiter uid from {board['board_url']!r}; configure metadata.uid"
        )
    return resolved


def _record_id(href: str, page_url: str) -> str | None:
    try:
        parsed = urlparse(urljoin(page_url, href))
        query = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return None
    values: dict[str, str] = {}
    for key, value in query:
        normalized = key.casefold()
        if normalized in values:
            return None
        values[normalized] = value
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold().rstrip(".") != "host.pcrecruiter.net"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
        or parsed.path.casefold() != "/pcrbin/jobboard.aspx"
        or parsed.params
        or parsed.fragment
        or values.get("action", "").casefold() != "detail"
    ):
        return None
    record_id = values.get("recordid", "")
    return record_id if _RECORD_ID_RE.fullmatch(record_id) is not None else None


def _form_data(tree: LexborHTMLParser, listing_url: str) -> tuple[str, dict[str, str]]:
    forms = tree.css("form#googlePage")
    if len(forms) != 1 or forms[0].attributes.get("method", "").casefold() != "post":
        raise ValueError("PCRecruiter listing omitted its unique POST pagination form")
    form = forms[0]
    post_url = urljoin(listing_url, form.attributes.get("action") or "")
    parsed = urlparse(post_url)
    listing = urlparse(listing_url)
    if (
        parsed.scheme.casefold() != listing.scheme.casefold()
        or parsed.netloc.casefold() != listing.netloc.casefold()
        or parsed.path.casefold() != "/pcrbin/jobboard.aspx"
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("PCRecruiter pagination form targeted an untrusted URL")

    data: dict[str, str] = {}
    for node in form.css("input[name]"):
        name = node.attributes.get("name", "")
        if name in data:
            raise ValueError(f"PCRecruiter pagination form duplicated {name!r}")
        data[name] = node.attributes.get("value", "")
    if not data.keys() >= _REQUIRED_FORM_FIELDS:
        raise ValueError("PCRecruiter pagination form omitted required state fields")
    if any(len(value) > 100_000 or "\x00" in value for value in data.values()):
        raise ValueError("PCRecruiter pagination form contained invalid state")
    return post_url, data


def _parse_page(html: str, board: PCRecruiterBoard) -> _Page:
    tree = LexborHTMLParser(html)
    markers = tree.css("#resultcount")
    if len(markers) != 1:
        raise ValueError("PCRecruiter listing omitted its unique result count")
    match = _RESULT_RE.fullmatch(markers[0].text(separator=" ", strip=True))
    if match is None:
        raise ValueError("PCRecruiter listing returned an invalid result count")
    start, end, total = (int(value) for value in match.groups())
    if total > MAX_JOBS:
        raise ValueError(f"PCRecruiter listing exceeded the {MAX_JOBS:,}-job safety cap")
    if total == 0:
        if (start, end) != (0, 0):
            raise ValueError("PCRecruiter empty listing returned an invalid result range")
    elif not (1 <= start <= end <= total):
        raise ValueError("PCRecruiter listing returned an invalid result range")

    record_ids: set[str] = set()
    for anchor in tree.css("td.td_jobtitle a[href]"):
        record_id = _record_id(anchor.attributes.get("href", ""), board.listing_url)
        if record_id is None:
            raise ValueError("PCRecruiter listing returned an invalid job detail URL")
        record_ids.add(record_id)
    expected_count = 0 if total == 0 else end - start + 1
    if len(record_ids) != expected_count:
        raise ValueError("PCRecruiter result count did not match its unique detail links")

    post_url, form = _form_data(tree, board.listing_url)
    return _Page(
        start=start,
        end=end,
        total=total,
        jobs=frozenset(board.job_url(record_id) for record_id in record_ids),
        post_url=post_url,
        form=form,
    )


async def _fetch_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    content: bytes | None = None,
    terminal: bool,
) -> str:
    method = "POST" if content is not None else "GET"
    headers = {"Accept": "text/html,application/xhtml+xml"}
    if content is not None:
        parsed = urlparse(url)
        headers.update(
            {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": f"{parsed.scheme}://{parsed.netloc}",
                "Referer": url,
            }
        )
    try:
        html = await fetch_text_page_with_retry(
            client,
            url,
            method=method,
            content=content,
            headers=headers,
            follow_redirects=False,
            end_of_pagination_statuses=(),
            retryable_statuses={202, 401, 403},
            require_nonempty=True,
            max_bytes=MAX_HTML_BYTES,
            log_event="pcrecruiter.page_backoff",
        )
    except PaginationFetchError as exc:
        if terminal and exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "PCRecruiter board no longer exists",
                url=url,
                status_code=exc.last_status,
            ) from exc
        raise
    if html is None:
        raise RuntimeError("PCRecruiter listing returned no document")
    _raise_if_bot_challenge(url, html)
    return html


async def _first_page(
    board: PCRecruiterBoard,
    client: httpx.AsyncClient,
    *,
    terminal: bool,
) -> _Page:
    html = await _fetch_page(client, board.listing_url, terminal=terminal)
    page = _parse_page(html, board)
    if page.total and page.start != 1:
        raise ValueError("PCRecruiter pagination did not start at the first result")
    return page


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Drain the provider's stateful POST pagination and verify every range."""
    _ = pw
    identity = _board_identity(board)
    page = await _first_page(identity, client, terminal=True)
    if page.total == 0:
        log.info("pcrecruiter.discovered", uid=identity.uid, jobs=0, pages=1)
        return set()

    page_size = page.end
    expected_total = page.total
    expected_pages = math.ceil(expected_total / page_size)
    if expected_pages > MAX_PAGES:
        raise ValueError(f"PCRecruiter pagination exceeded the {MAX_PAGES:,}-page safety cap")
    jobs = set(page.jobs)
    for page_number in range(2, expected_pages + 1):
        form = dict(page.form)
        offset = (page_number - 1) * page_size
        form["morecount"] = f"{offset}$${page_number - 1}"
        html = await _fetch_page(
            client,
            page.post_url,
            content=urlencode(form).encode(),
            terminal=False,
        )
        page = _parse_page(html, identity)
        expected_start = offset + 1
        expected_end = min(offset + page_size, expected_total)
        if page.total != expected_total:
            raise ValueError("PCRecruiter advertised total drifted during pagination")
        if page.start != expected_start or page.end != expected_end:
            raise ValueError("PCRecruiter pagination range drifted")
        if jobs & page.jobs:
            raise ValueError("PCRecruiter pagination repeated jobs")
        jobs.update(page.jobs)

    if len(jobs) != expected_total:
        raise ValueError("PCRecruiter pagination did not match its advertised total")
    log.info(
        "pcrecruiter.discovered",
        uid=identity.uid,
        jobs=len(jobs),
        pages=expected_pages,
    )
    return jobs


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Recognize and validate exact public PCRecruiter board URLs."""
    _ = pw
    board = pcrecruiter_board_from_url(url)
    if board is None:
        return None
    if client is None:
        return {"uid": board.uid}
    try:
        page = await _first_page(board, client, terminal=False)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("pcrecruiter.probe_failed", uid=board.uid, exc_info=True)
        return None
    return {"uid": board.uid, "jobs": page.total, "page_size": page.end or 0}


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    board = pcrecruiter_board_from_metadata(metadata) or pcrecruiter_board_from_url(board_url)
    if board is None:
        return
    await save_text_response(
        artifact_dir,
        client,
        board.listing_url,
        filename="pcrecruiter-listing.html",
        follow_redirects=False,
    )


register("pcrecruiter", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
