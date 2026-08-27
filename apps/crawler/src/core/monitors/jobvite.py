"""Jobvite server-rendered listing monitor.

Jobvite publishes every open requisition as a stable anchor on a static HTML
listing.  This native adapter deliberately composes the generic DOM link
extractor and shared HTTP retry primitives; the existing JSON-LD scraper owns
detail extraction on its normal schedule.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import httpx
import structlog

from src.core.monitors import BoardGoneError, fetch_page_text, register
from src.core.monitors.dom import _extract_links_static, _raise_if_bot_challenge
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import PaginationFetchError, fetch_text_page_with_retry
from src.shared.jobvite import (
    JobviteBoard,
    is_jobvite_invalid_redirect,
    jobvite_board_from_metadata,
    jobvite_board_from_url,
    jobvite_job_url,
    jobvite_page_tenant,
)
from src.shared.tdm import TDMReservedError

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_HTML_CHARS = 5_000_000
MAX_LINKED_LISTINGS = 6
_TRANSIENT_STATUSES = frozenset({202, 401, 403})
_GONE_STATUSES = frozenset({404, 410})
_JOB_LINK_RE = re.compile(
    r"^https://jobs\.jobvite\.com/[a-z0-9](?:[a-z0-9-]{0,62})/"
    r"job/[A-Za-z0-9_-]{6,64}(?:[/?#]|$)",
    re.IGNORECASE,
)
_LISTING_LINK_RE = re.compile(
    r"^https://jobs\.jobvite\.com/(?:"
    r"[a-z0-9](?:[a-z0-9-]{0,62})(?:/jobs(?:/positions)?)?"
    r"|careers/[a-z0-9](?:[a-z0-9-]{0,62})(?:/jobs)?"
    r")(?:[?#]|$)",
    re.IGNORECASE,
)
_PAGE_URL_RE = re.compile(
    r"(https?://jobs\.jobvite\.com/(?:careers/)?"
    r"[a-z0-9](?:[a-z0-9-]{0,62})"
    r"(?:/jobs(?:/positions)?|/job/[A-Za-z0-9_-]{6,64})?/?"
    r"(?:\?[^\"'<\s]*)?)(?=[#\"'<\s]|$)",
    re.IGNORECASE,
)


def _board_identity(board: dict) -> JobviteBoard:
    metadata = board.get("metadata") or {}
    configured = jobvite_board_from_metadata(metadata) if isinstance(metadata, dict) else None
    direct = jobvite_board_from_url(board["board_url"])
    has_configured_identity = isinstance(metadata, dict) and bool(
        {"tenant", "listing_url"} & metadata.keys()
    )
    if has_configured_identity and configured is None:
        raise ValueError("Invalid or internally inconsistent Jobvite monitor configuration")
    if configured is not None and direct is not None and configured.tenant != direct.tenant:
        raise ValueError(
            f"Configured Jobvite tenant {configured.tenant!r} does not match "
            f"board URL tenant {direct.tenant!r}"
        )
    resolved = configured or direct
    if resolved is None:
        raise ValueError(
            f"Cannot derive a Jobvite tenant/listing from {board['board_url']!r}; "
            "configure metadata.tenant or metadata.listing_url"
        )
    return resolved


async def _fetch_listing(
    key: JobviteBoard,
    client: httpx.AsyncClient,
    *,
    terminal: bool,
) -> str:
    try:
        body = await fetch_text_page_with_retry(
            client,
            key.listing_url,
            follow_redirects=False,
            retryable_statuses=_TRANSIENT_STATUSES,
            end_of_pagination_statuses=(),
            require_nonempty=True,
            max_chars=MAX_HTML_CHARS + 1,
            log_event="jobvite.listing_backoff",
        )
    except PaginationFetchError as exc:
        if terminal and (
            exc.last_status in _GONE_STATUSES or is_jobvite_invalid_redirect(exc.last_location)
        ):
            raise BoardGoneError(
                "Jobvite board no longer exists",
                url=key.listing_url,
                status_code=exc.last_status,
            ) from exc
        raise
    if body is None:  # Strict status handling makes this unreachable.
        raise RuntimeError(f"Jobvite listing fetch returned no page for {key.listing_url!r}")
    _raise_if_bot_challenge(key.listing_url, body)
    if len(body) > MAX_HTML_CHARS:
        raise ValueError("Jobvite listing exceeded the HTML safety cap")
    return body


def _parse_listing(body: str, key: JobviteBoard) -> tuple[set[str], tuple[JobviteBoard, ...]]:
    page_tenant = jobvite_page_tenant(body)
    if page_tenant != key.tenant:
        raise ValueError(
            f"Jobvite listing identity mismatch: expected {key.tenant!r}, found {page_tenant!r}"
        )

    raw_jobs = _extract_links_static(body, key.listing_url, _JOB_LINK_RE)
    jobs = {
        canonical for url in raw_jobs if (canonical := jobvite_job_url(url, key.tenant)) is not None
    }
    if len(jobs) > MAX_JOBS:
        raise ValueError(f"Jobvite listing exceeded the {MAX_JOBS:,}-job safety cap")

    raw_listings = _extract_links_static(body, key.listing_url, _LISTING_LINK_RE)
    candidates = {
        candidate
        for url in raw_listings
        if (candidate := jobvite_board_from_url(url)) is not None
        and candidate.tenant == key.tenant
        and candidate != key
    }
    return jobs, tuple(sorted(candidates, key=lambda item: item.listing_url))


async def resolve_listing(
    initial: JobviteBoard,
    client: httpx.AsyncClient,
    *,
    terminal: bool,
) -> tuple[JobviteBoard, set[str]]:
    """Resolve branded landing pages to an explicit same-tenant job listing."""
    body = await _fetch_listing(initial, client, terminal=terminal)
    jobs, candidates = _parse_listing(body, initial)
    if jobs or not candidates:
        return initial, jobs

    # Some branded ``/careers/{tenant}`` pages are marketing shells whose
    # Jobs CTA points to ``/{tenant}/jobs/positions``.  Never treat such a
    # shell as an authoritative empty inventory before checking that explicit
    # same-tenant destination.
    for candidate in candidates[:MAX_LINKED_LISTINGS]:
        try:
            candidate_body = await _fetch_listing(candidate, client, terminal=False)
            candidate_jobs, _ = _parse_listing(candidate_body, candidate)
        except PaginationFetchError as exc:
            if exc.last_status in _GONE_STATUSES:
                continue
            raise
        if candidate_jobs:
            return candidate, candidate_jobs

    return initial, jobs


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Discover every canonical detail URL on one Jobvite career site."""
    _ = pw
    initial = _board_identity(board)
    resolved, jobs = await resolve_listing(initial, client, terminal=True)
    log.info(
        "jobvite.discovered",
        tenant=resolved.tenant,
        listing_url=resolved.listing_url,
        jobs=len(jobs),
    )
    return jobs


async def _probe_candidate(
    candidate: JobviteBoard,
    client: httpx.AsyncClient,
) -> tuple[JobviteBoard, set[str]] | None:
    try:
        return await resolve_listing(candidate, client, terminal=False)
    except TDMReservedError:
        raise
    except Exception:
        log.debug(
            "jobvite.probe_failed",
            listing_url=candidate.listing_url,
            exc_info=True,
        )
        return None


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect direct or explicitly linked Jobvite public career sites."""
    _ = pw
    direct = jobvite_board_from_url(url)
    if direct is not None:
        if client is None:
            return {"tenant": direct.tenant, "listing_url": direct.listing_url}
        probed = await _probe_candidate(direct, client)
        if probed is None:
            return None
        resolved, jobs = probed
        return {
            "tenant": resolved.tenant,
            "listing_url": resolved.listing_url,
            "jobs": len(jobs),
        }

    if client is None:
        return None
    page = await fetch_page_text(url, client)
    if not page:
        return None
    candidates: dict[str, JobviteBoard] = {}
    for match in _PAGE_URL_RE.finditer(html.unescape(page)):
        candidate = jobvite_board_from_url(match.group(1))
        if candidate is not None:
            candidates[candidate.listing_url] = candidate
    for candidate in list(candidates.values())[:MAX_LINKED_LISTINGS]:
        probed = await _probe_candidate(candidate, client)
        if probed is None:
            continue
        resolved, jobs = probed
        log.info(
            "jobvite.detected_in_page",
            url=url,
            tenant=resolved.tenant,
            listing_url=resolved.listing_url,
        )
        return {
            "tenant": resolved.tenant,
            "listing_url": resolved.listing_url,
            "jobs": len(jobs),
        }
    return None


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    key = jobvite_board_from_metadata(metadata) or jobvite_board_from_url(board_url)
    if key is None:
        return
    await save_text_response(
        artifact_dir,
        client,
        key.listing_url,
        filename="jobvite-listing.html",
        follow_redirects=False,
    )


register("jobvite", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
