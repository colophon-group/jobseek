"""Softgarden ATS monitor.

Classic boards ({slug}.softgarden.io) embed all job IDs in inline JavaScript.
No API credentials needed.

Listing page JS (confirmed on hapaglloyd, ctseventim):
  var complete_job_id_list = jobs_selected = [48677018, 53688446, ...];

Returns detail URLs only — the json-ld scraper extracts JobPosting data.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import register
from src.core.monitors._ats_template import ProbeCount, ProbeResult, ats_can_handle
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

MAX_JOBS = 50_000

_IGNORE_SLUGS = frozenset({"www", "api", "app", "static", "cdn"})

_JOB_IDS_RE = re.compile(r"var\s+complete_job_id_list\s*=\s*(?:jobs_selected\s*=\s*)?\[([^\]]*)\]")

_PAGE_PATTERNS = [
    re.compile(r"\b(?!(?:www|api|app|static|cdn)\.)([\w-]+)\.softgarden\.io"),
]

# ── URL helpers ──────────────────────────────────────────────────────────


def _slug_from_url(url: str) -> str | None:
    """Extract customer slug from a *.softgarden.io URL."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith(".softgarden.io"):
        slug = host.removesuffix(".softgarden.io")
        if slug and slug not in _IGNORE_SLUGS:
            return slug
    return None


def _board_url(slug: str) -> str:
    return f"https://{slug}.softgarden.io"


def _job_url(base: str, job_id: int | str, pattern: str = "{base}/job/{id}?l=en") -> str:
    return pattern.replace("{base}", base).replace("{id}", str(job_id))


# ── Listing page parsing ────────────────────────────────────────────────


def _extract_job_ids(html: str) -> list[int]:
    """Extract job IDs from the listing page's inline JavaScript."""
    match = _JOB_IDS_RE.search(html)
    if not match:
        return []
    raw = match.group(1).strip()
    if not raw:
        return []
    ids: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if token:
            try:
                ids.append(int(token))
            except ValueError:
                continue
    return ids


# ── Discovery ────────────────────────────────────────────────────────────


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Discover job URLs from a Softgarden board.

    1. Fetch listing page → extract job IDs from inline JS
    2. Build detail URLs via configurable pattern
    """
    metadata = board.get("metadata") or {}
    slug = metadata.get("slug") or _slug_from_url(board["board_url"])

    if not slug:
        raise ValueError(
            f"Cannot derive Softgarden slug from board URL {board['board_url']!r} "
            "and no slug in metadata"
        )

    base = _board_url(slug)
    pattern = metadata.get("job_url_pattern", "{base}/job/{id}?l=en")

    # Fetch listing page
    resp = await client.get(base, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text

    # Extract job IDs
    job_ids = _extract_job_ids(html)
    if not job_ids:
        log.info("softgarden.no_jobs", slug=slug)
        return set()

    log.info("softgarden.listed", slug=slug, jobs=len(job_ids))

    if len(job_ids) > MAX_JOBS:
        log.warning("softgarden.truncated", slug=slug, total=len(job_ids), cap=MAX_JOBS)
        return truncated_url_result({_job_url(base, jid, pattern) for jid in job_ids})

    return {_job_url(base, jid, pattern) for jid in job_ids}


# ── Probing ──────────────────────────────────────────────────────────────


async def _probe_listing(slug: str, client: httpx.AsyncClient) -> tuple[bool, int | None]:
    """Probe a Softgarden listing page. Returns (found, job_count)."""
    try:
        resp = await client.get(_board_url(slug), follow_redirects=True)
        if resp.status_code != 200:
            return False, None
        job_ids = _extract_job_ids(resp.text)
        if job_ids:
            return True, len(job_ids)
        return False, None
    except Exception:
        return False, None


async def _fetch_job_count(
    slug: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeCount | None:
    _ = context
    found, count = await _probe_listing(slug, client)
    if found:
        return count
    return None


async def _probe_template_slug(
    slug: str,
    client: httpx.AsyncClient,
    context: None,
) -> ProbeResult:
    _ = context
    return await _probe_listing(slug, client)


def _slug_result(slug: str, count: ProbeCount | None, context: None) -> dict:
    _ = context
    result: dict = {"slug": slug}
    if count is not None:
        result["jobs"] = count
    return result


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Softgarden: URL domain match -> page HTML markers scan."""
    _ = pw
    return await ats_can_handle(
        url,
        client,
        monitor_name="softgarden",
        token_from_url=_slug_from_url,
        page_patterns=_PAGE_PATTERNS,
        ignore_tokens=_IGNORE_SLUGS,
        fetch_job_count=_fetch_job_count,
        api_probe=_probe_template_slug,
        initial_context=None,
        result_builder=_slug_result,
        page_token_probe=_probe_template_slug,
        allow_slug_guess=False,
        log_token_field="slug",
    )


register("softgarden", discover, cost=10, can_handle=can_handle, rich=False)
