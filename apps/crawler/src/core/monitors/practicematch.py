"""PracticeMatch employer landing-page monitor.

PracticeMatch employer pages render the first physician result page in HTML,
then paginate physician and advanced-practitioner results through the same
form endpoint used by ``employer/js/landingPages.js``.  The provider drops
connections from the crawler's direct datacenter egress, so detected boards
opt into the configured proxy transport by default.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode, urlparse

import structlog

from src.core.monitors import register
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import fetch_text_page_with_retry
from src.shared.truncation import truncated_url_result

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_URLS = 50_000
_MAX_PAGES = 2_000
_MAX_PAGE_BYTES = 25_000_000
_RETRIES = 3
_BASE_DELAY = 0.5
_EMPLOYER_HOSTS = frozenset({"employer.practicematch.com", "www.practicematch.com"})
_JOB_PATH_RE = re.compile(r"^/physicians/job-details\.cfm/(\d+)(?:/|$)", re.IGNORECASE)
_LANDING_PATH_RE = re.compile(r"^/employer/(?![^/]+\.cfm/?$)[^/]+/?$", re.IGNORECASE)
_HIDDEN_FIELDS = frozenset(
    {
        "facilityID",
        "facilityLandingURL",
        "contactID",
        "siteID",
        "oppIDs",
        "hasMap",
        "oppProf",
    }
)


def _canonical_job_url(raw_url: str) -> str | None:
    """Return the stable numeric PracticeMatch job URL, without tracking data."""
    parsed = urlparse(raw_url)
    host = (parsed.hostname or "").lower()
    if host not in {"practicematch.com", "www.practicematch.com"}:
        return None
    match = _JOB_PATH_RE.match(parsed.path)
    if not match:
        return None
    return f"https://www.practicematch.com/physicians/job-details.cfm/{match.group(1)}/"


class _LandingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden: dict[str, str] = {}
        self.urls: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key: value or "" for key, value in attrs}
        if tag.lower() == "input" and attr.get("id") in _HIDDEN_FIELDS:
            self.hidden[attr["id"]] = attr.get("value", "")
            return
        if tag.lower() != "a" or not attr.get("href"):
            return
        url = _canonical_job_url(attr["href"])
        if url:
            self.urls.add(url)


def _parse_landing_html(html: str) -> tuple[dict[str, str], set[str]]:
    parser = _LandingParser()
    parser.feed(html)
    parser.close()
    return parser.hidden, parser.urls


def _config_max_pages(metadata: dict) -> int:
    value = metadata.get("max_pages")
    if value is None:
        return _MAX_PAGES
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("PracticeMatch max_pages must be an integer") from exc
    if parsed < 1:
        raise ValueError("PracticeMatch max_pages must be positive")
    return min(parsed, _MAX_PAGES)


def _pagination_url(board_url: str) -> str:
    parsed = urlparse(board_url)
    return (
        f"{parsed.scheme}://{parsed.netloc}/employer/cfcs/landingPageUtils.cfc?method=getOppData2"
    )


def _form(hidden: dict[str, str], *, profession_id: str, page: int) -> bytes:
    return urlencode(
        {
            "facilityID": hidden["facilityID"],
            "facilityLandingURL": hidden.get("facilityLandingURL", ""),
            "specialty": "",
            "professionID": profession_id,
            "keywords": "",
            "pageNum": str(page),
            "state": "",
            "contactID": hidden.get("contactID", "0"),
            "siteID": hidden.get("siteID", ""),
            "oppIDs": hidden.get("oppIDs", "0"),
            # Map and specialty fragments are not needed for URL discovery.
            "hasMap": "0",
            "updateSpec": "0",
        }
    ).encode()


def _response_listing_html(payload: str, endpoint: str) -> str:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"PracticeMatch pagination returned invalid JSON from {endpoint}") from exc
    if not isinstance(data, dict):
        kind = type(data).__name__
        raise ValueError(f"PracticeMatch pagination returned {kind}, expected object")
    value = data.get("OPPLISTINGSHTML")
    if value is None:
        value = data.get("oppListingsHTML")
    if not isinstance(value, str):
        raise ValueError("PracticeMatch pagination response is missing OPPLISTINGSHTML")
    return value


async def _post_page(
    client: httpx.AsyncClient,
    endpoint: str,
    hidden: dict[str, str],
    *,
    profession_id: str,
    page: int,
) -> set[str]:
    payload = await fetch_text_page_with_retry(
        client,
        endpoint,
        method="POST",
        content=_form(hidden, profession_id=profession_id, page=page),
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        },
        retries=_RETRIES,
        base_delay=_BASE_DELAY,
        require_nonempty=True,
        end_of_pagination_statuses=(),
        max_bytes=_MAX_PAGE_BYTES,
        log_event="practicematch.page_backoff",
    )
    if payload is None:  # excluded by end_of_pagination_statuses=(), for type narrowing
        raise ValueError("PracticeMatch pagination returned no response body")
    _hidden, urls = _parse_landing_html(_response_listing_html(payload, endpoint))
    return urls


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    """Detect the provider from its dedicated employer landing-page route."""
    _ = client, pw
    parsed = urlparse(url)
    if (parsed.hostname or "").lower() not in _EMPLOYER_HOSTS:
        return None
    if not _LANDING_PATH_RE.match(parsed.path):
        return None
    return {"proxy": True}


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Return all physician and advanced-practitioner detail URLs."""
    _ = pw
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}
    max_pages = _config_max_pages(metadata)

    html = await fetch_text_page_with_retry(
        client,
        board_url,
        retries=_RETRIES,
        base_delay=_BASE_DELAY,
        require_nonempty=True,
        end_of_pagination_statuses=(),
        max_bytes=_MAX_PAGE_BYTES,
        log_event="practicematch.landing_backoff",
    )
    if html is None:  # excluded by end_of_pagination_statuses=(), for type narrowing
        raise ValueError("PracticeMatch landing page returned no response body")

    hidden, urls = _parse_landing_html(html)
    if not hidden.get("facilityID"):
        raise ValueError("PracticeMatch landing page is missing facilityID")

    endpoint = _pagination_url(board_url)
    truncated = False
    # The landing HTML already contains physician page 1. The AP tab uses
    # professionID=-1 and must be fetched separately from page 1.
    starts = (("1", 2 if urls else 1), ("-1", 1))
    for profession_id, start_page in starts:
        for page in range(start_page, max_pages + 1):
            page_urls = await _post_page(
                client,
                endpoint,
                hidden,
                profession_id=profession_id,
                page=page,
            )
            if not page_urls:
                break
            new_urls = page_urls - urls
            if not new_urls:
                break
            urls.update(new_urls)
            if len(urls) >= MAX_URLS:
                log.warning("practicematch.truncated", board_url=board_url, total=len(urls))
                return truncated_url_result(urls)
        else:
            truncated = True

    log.info("practicematch.complete", board_url=board_url, urls_found=len(urls))
    return truncated_url_result(urls) if truncated else urls


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    # monitor_one() supplies the shared direct client to artifact savers; route
    # this extra fetch through the same per-board proxy config as discovery.
    from src.shared.http import client_for

    async with client_for(client, metadata) as routed:
        await save_text_response(
            artifact_dir,
            routed,
            board_url,
            filename="page.html",
            follow_redirects=True,
        )


register("practicematch", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
