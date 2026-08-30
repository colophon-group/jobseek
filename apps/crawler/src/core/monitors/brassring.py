"""BrassRing / Infinite Talent public job-search monitor.

BrassRing's ``TGnewUI`` boards bootstrap an Angular application with a small
set of featured jobs.  Submitting the empty search loads the authoritative
inventory from ``Search/Ajax/MatchedJobs`` and subsequent pages from
``Search/Ajax/ProcessSortAndShowMoreJobs``.  Both responses contain complete
job records, so this monitor captures those first-party JSON responses instead
of scraping the transient result DOM.
"""

from __future__ import annotations

import asyncio
import html
import json
import math
import re
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.browser import BROWSER_KEYS, navigate, open_page
from src.shared.html_normalize import normalize_description_html
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

PAGE_SIZE = 50
MAX_JOBS = 50_000
_BOARD_PATH_RE = re.compile(r"/tgnewui/search/", re.IGNORECASE)
_MATCHED_JOBS_PATH = "/Search/Ajax/MatchedJobs"
_NEXT_PAGE_PATH = "/Search/Ajax/ProcessSortAndShowMoreJobs"
_SEARCH_SELECTOR = "#clearResumeJobsBtn"
_NEXT_SELECTOR = 'button[title="Next Page"]:not(.disabled-link)'
_SORT_BUTTON_SELECTOR = "#sortBy-button"
_SORT_OPTIONS_SELECTOR = "#sortBy option"
_SORT_MENU_SELECTOR = "#sortBy-menu li"
_ALPHABETICAL_SORT_VALUE = "1"
_SNAPSHOT_ATTEMPTS = 2
_SNAPSHOT_RETRY_DELAY = 1.0


class _SnapshotChanged(ValueError):
    """The board inventory changed while its paginated snapshot was collected."""


def _clean_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(html.unescape(value).split())
    return cleaned or None


def _board_ids(url: str) -> tuple[str, str] | None:
    """Return the numeric partner/site IDs for a public TGnewUI board."""

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if not _BOARD_PATH_RE.search(parsed.path):
        return None
    query = parse_qs(parsed.query)
    partner_id = (query.get("partnerid") or query.get("partnerId") or [None])[0]
    site_id = (query.get("siteid") or query.get("siteId") or [None])[0]
    if not (
        isinstance(partner_id, str)
        and partner_id.isdigit()
        and isinstance(site_id, str)
        and site_id.isdigit()
    ):
        return None
    return partner_id, site_id


def _questions(raw: object) -> dict[str, object]:
    if not isinstance(raw, list):
        return {}
    result: dict[str, object] = {}
    for question in raw:
        if not isinstance(question, dict):
            continue
        name = question.get("QuestionName")
        if isinstance(name, str) and name:
            result[name.casefold()] = question.get("Value")
    return result


def _safe_date(value: object) -> str | None:
    raw = _clean_string(value)
    if raw is None:
        return None
    try:
        return datetime.strptime(raw, "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def _location(questions: dict[str, object]) -> list[str] | None:
    parts: list[str] = []
    seen: set[str] = set()
    # ADM and other BrassRing tenants use these fields for city, state/region,
    # and country.  Treat them as components of one place, not three separate
    # job locations.
    for key in ("formtext8", "formtext9", "formtext10"):
        part = _clean_string(questions.get(key))
        if part and part.casefold() not in seen:
            seen.add(part.casefold())
            parts.append(part)
    return [", ".join(parts)] if parts else None


def _parse_job(raw: object, partner_id: str, site_id: str) -> DiscoveredJob | None:
    if not isinstance(raw, dict):
        return None
    questions = _questions(raw.get("Questions"))
    job_id = _clean_string(questions.get("reqid"))
    title = _clean_string(questions.get("jobtitle"))
    link = _clean_string(raw.get("Link"))
    if not job_id or not job_id.isdigit() or not title or not link:
        return None

    link_ids = _board_ids(link)
    parsed_link = urlparse(link)
    link_query = parse_qs(parsed_link.query)
    link_job_id = (link_query.get("jobid") or [None])[0]
    if link_ids != (partner_id, site_id) or link_job_id != job_id:
        return None

    raw_description = questions.get("jobdescription") or questions.get("formtext3")
    description = normalize_description_html(
        raw_description if isinstance(raw_description, str) else None
    )

    metadata: dict[str, object] = {
        "provider": "brassring",
        "requisition_id": job_id,
    }
    department = _clean_string(questions.get("department"))
    if department:
        metadata["department"] = department

    return DiscoveredJob(
        url=link,
        title=title,
        description=description,
        locations=_location(questions),
        date_posted=_safe_date(questions.get("lastupdated")),
        metadata=metadata,
    )


def _parse_page(payload: object) -> tuple[int, list[object]]:
    if not isinstance(payload, dict):
        raise ValueError("BrassRing search returned a non-object response")
    total = payload.get("JobsCount")
    jobs_obj = payload.get("Jobs")
    rows = jobs_obj.get("Job") if isinstance(jobs_obj, dict) else None
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise ValueError("BrassRing search omitted a valid JobsCount")
    if rows is None and total == 0:
        return total, []
    if not isinstance(rows, list):
        raise ValueError("BrassRing search omitted its Jobs.Job list")
    return total, rows


def _bounded_inventory_rows(
    rows: list[object],
    expected_total: int,
    *,
    truncated: bool,
) -> list[object]:
    if len(rows) < expected_total or (not truncated and len(rows) != expected_total):
        raise _SnapshotChanged(
            f"BrassRing returned {len(rows)} rows for {expected_total} expected jobs"
        )
    return rows[:expected_total]


async def _click_for_json(page, selector: str, response_path: str) -> object:
    async with page.expect_response(
        lambda response: response_path.casefold() in response.url.casefold(),
        timeout=60_000,
    ) as pending:
        await page.locator(selector).first.click()
    response = await pending.value
    if response.status != 200:
        raise ValueError(f"BrassRing search returned HTTP {response.status}")
    body = await response.text()
    from src.shared.tdm import check_browser_response

    check_browser_response(dict(response.headers), body, url=response.url)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError("BrassRing search returned invalid JSON") from exc


async def _sort_alphabetically(page) -> object:
    """Start a fresh first page using the provider's stable title ordering."""

    option_values = await page.locator(_SORT_OPTIONS_SELECTOR).evaluate_all(
        "options => options.map(option => option.value)"
    )
    if not isinstance(option_values, list) or _ALPHABETICAL_SORT_VALUE not in option_values:
        raise _SnapshotChanged("BrassRing search omitted its alphabetical sort option")
    option_number = option_values.index(_ALPHABETICAL_SORT_VALUE) + 1

    sort_button = page.locator(_SORT_BUTTON_SELECTOR).first
    if await sort_button.count() == 0:
        raise _SnapshotChanged("BrassRing search omitted its sort control")
    await sort_button.click()
    return await _click_for_json(
        page,
        f"{_SORT_MENU_SELECTOR}:nth-child({option_number})",
        _NEXT_PAGE_PATH,
    )


async def _discover_page(board_url: str, metadata: dict, partner_id: str, site_id: str, pw):
    browser_config = {
        "wait": "domcontentloaded",
        "timeout": 60_000,
        **{key: value for key, value in metadata.items() if key in BROWSER_KEYS},
    }
    async with open_page(
        pw,
        browser_config,
        use_proxy=bool(metadata.get("proxy")),
        target_url=board_url,
    ) as page:
        await navigate(page, board_url, browser_config)
        await page.locator(_SEARCH_SELECTOR).first.wait_for(state="visible", timeout=60_000)

        payload = await _click_for_json(page, _SEARCH_SELECTOR, _MATCHED_JOBS_PATH)
        total, rows = _parse_page(payload)
        expected_total = min(total, MAX_JOBS)
        if expected_total and not rows:
            raise _SnapshotChanged(f"BrassRing returned no first-page rows for {total} jobs")
        page_size = len(rows) or PAGE_SIZE
        expected_pages = math.ceil(expected_total / page_size) if expected_total else 0

        if expected_pages > 1:
            await page.wait_for_function(
                """expected => {
                    const current = document.querySelector(
                        '.pagewise-pagination[aria-current="page"]'
                    );
                    return current && current.textContent.trim() === String(expected);
                }""",
                arg=1,
                timeout=60_000,
            )

            # LastUpdated ordering moves every time a listing changes and can
            # shift the same requisition across adjacent pages mid-run. The
            # provider's alphabetical ordering is stable across those updates.
            # Treat its page-one response as the start of a new authoritative
            # snapshot; the initial response only proves pagination is needed.
            sorted_payload = await _sort_alphabetically(page)
            sorted_total, sorted_rows = _parse_page(sorted_payload)
            if total > 0 and sorted_total == 0:
                raise _SnapshotChanged(
                    "BrassRing total changed from non-zero to zero while selecting stable sort"
                )
            total, rows = sorted_total, sorted_rows
            expected_total = min(total, MAX_JOBS)
            if expected_total and not rows:
                raise _SnapshotChanged(
                    f"BrassRing returned no sorted first-page rows for {total} jobs"
                )
            page_size = len(rows) or PAGE_SIZE
            expected_pages = math.ceil(expected_total / page_size) if expected_total else 0

            if expected_pages > 1:
                await page.wait_for_function(
                    """expected => {
                        const current = document.querySelector(
                            '.pagewise-pagination[aria-current="page"]'
                        );
                        return current && current.textContent.trim() === String(expected);
                    }""",
                    arg=1,
                    timeout=60_000,
                )

        for page_number in range(2, expected_pages + 1):
            next_button = page.locator(_NEXT_SELECTOR).first
            if await next_button.count() == 0:
                raise _SnapshotChanged(
                    f"BrassRing pagination ended at page {page_number - 1} "
                    f"with {len(rows)} of {total} rows"
                )
            next_payload = await _click_for_json(page, _NEXT_SELECTOR, _NEXT_PAGE_PATH)
            next_total, next_rows = _parse_page(next_payload)
            if next_total != total:
                raise _SnapshotChanged(
                    f"BrassRing JobsCount changed during pagination: {total} -> {next_total}"
                )
            rows.extend(next_rows)
            # The XHR resolves before Angular has necessarily committed its
            # new pageNumber.  Without this barrier the next loop iteration
            # can click the old control again and silently collect duplicate
            # pages while skipping the tail.
            await page.wait_for_function(
                """expected => {
                    const current = document.querySelector(
                        '.pagewise-pagination[aria-current="page"]'
                    );
                    return current && current.textContent.trim() === String(expected);
                }""",
                arg=page_number,
                timeout=60_000,
            )

    truncated = total > MAX_JOBS
    rows = _bounded_inventory_rows(rows, expected_total, truncated=truncated)

    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    for raw in rows:
        job = _parse_job(raw, partner_id, site_id)
        if job is None:
            raise _SnapshotChanged("BrassRing returned a row without valid job identity")
        job_id = job.metadata.get("requisition_id") if job.metadata is not None else None
        if not isinstance(job_id, str):
            raise _SnapshotChanged("BrassRing returned a row without valid requisition identity")
        if job_id in seen:
            raise _SnapshotChanged(f"BrassRing repeated requisition {job_id}")
        seen.add(job_id)
        jobs.append(job)
    if truncated:
        return truncated_rich_result(jobs)
    return jobs


async def discover(board: dict, client: httpx.AsyncClient = None, pw=None):
    board_url = board["board_url"]
    ids = _board_ids(board_url)
    if ids is None:
        raise ValueError(f"Cannot derive BrassRing partner/site IDs from {board_url!r}")
    partner_id, site_id = ids
    metadata = board.get("metadata") or {}

    async def collect(playwright):
        for attempt in range(1, _SNAPSHOT_ATTEMPTS + 1):
            try:
                return await _discover_page(board_url, metadata, partner_id, site_id, playwright)
            except _SnapshotChanged as exc:
                if attempt == _SNAPSHOT_ATTEMPTS:
                    raise
                log.warning(
                    "brassring.snapshot_changed",
                    board_url=board_url,
                    attempt=attempt,
                    error=str(exc),
                )
                await asyncio.sleep(_SNAPSHOT_RETRY_DELAY)
        raise AssertionError("unreachable")

    if pw is not None:
        return await collect(pw)

    try:
        from playwright.async_api import async_playwright
    except ImportError as err:  # pragma: no cover - dependency is required in browser workers
        raise RuntimeError("playwright is required for the BrassRing monitor") from err
    async with async_playwright() as playwright:
        return await collect(playwright)


async def can_handle(url: str, client: httpx.AsyncClient, pw=None) -> dict | None:
    ids = _board_ids(url)
    if ids is None:
        return None
    # The TGnewUI route plus its required numeric identifiers is a stable
    # provider fingerprint.  A lightweight page check prevents similarly
    # shaped non-BrassRing routes from being selected speculatively.
    response = await client.get(url, follow_redirects=True)
    if response.status_code != 200:
        return None
    from src.shared.tdm import check_response

    check_response(response, body_excerpt=response.text[:500_000])
    body = response.text.casefold()
    if 'id="searchresults"' not in body or "angular" not in body:
        return None
    partner_id, site_id = ids
    return {"partner_id": partner_id, "site_id": site_id}


register("brassring", discover, cost=10, can_handle=can_handle, rich=True)
