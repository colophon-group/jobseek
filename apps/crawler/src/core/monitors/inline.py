"""Inline single-page job extraction monitor.

Extracts multiple jobs from a single career page where all postings are
listed inline (no individual job URLs).  Uses step-based extraction
(same as the DOM scraper) in a loop — the cursor advances through the
page, extracting one job per iteration.

Each job gets a synthetic URL with a ``_jid`` query parameter for
pipeline compatibility::

    https://example.com/open-positions?_jid=senior-engineer-a1b2c3

Registered as a **rich** monitor — the scraper step is skipped.

Requires playwright when ``render`` is true:
``uv run playwright install chromium``
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.browser import BROWSER_KEYS, navigate, open_page, safe_content
from src.shared.extract import flatten, walk_steps
from src.shared.slug import slugify

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

_MAX_JOBS = 500  # safety cap


def _generate_url(board_url: str, title: str, seen: dict[str, int]) -> str:
    """Generate a stable synthetic URL for an inline job.

    Format: ``{board_url}?_jid={slug}-{hash[:6]}``
    Appends a counter suffix on collision (identical titles).
    """
    slug = slugify(title)[:50]
    title_hash = hashlib.sha256(title.strip().lower().encode()).hexdigest()[:6]
    jid = f"{slug}-{title_hash}" if slug else title_hash

    # Handle collisions (identical titles on same page)
    count = seen.get(jid, 0)
    seen[jid] = count + 1
    if count > 0:
        jid = f"{jid}-{count + 1}"

    # Append _jid to the board URL, preserving existing query params
    parsed = urlparse(board_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    params["_jid"] = [jid]
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


async def _fetch_html(
    board_url: str,
    metadata: dict,
    http: httpx.AsyncClient,
    pw=None,
) -> str:
    """Fetch page HTML, using Playwright when render is configured."""
    if metadata.get("render") and pw:
        browser_cfg = {k: v for k, v in metadata.items() if k in BROWSER_KEYS}
        async with open_page(pw, browser_cfg, use_proxy=bool(metadata.get("proxy"))) as page:
            await navigate(page, board_url, browser_cfg)
            return await safe_content(page)

    resp = await http.get(board_url, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


async def discover(
    board: dict,
    client: httpx.AsyncClient = None,
    pw=None,
) -> list[DiscoveredJob]:
    """Extract inline jobs from a single page.

    Config keys:
        steps      — extraction steps (same format as DOM scraper)
        render     — if true, use Playwright (default: false)
        defaults   — default field values applied to all jobs
        + browser keys (wait, timeout, actions, etc.)
    """
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}

    steps = metadata.get("steps")
    if not steps:
        log.warning("inline.no_steps", url=board_url)
        return []

    defaults = metadata.get("defaults") or {}

    html = await _fetch_html(board_url, metadata, client, pw)
    elements = flatten(html)

    if not elements:
        log.info("inline.empty_page", url=board_url)
        return []

    # Extract jobs by running steps repeatedly
    jobs: list[DiscoveredJob] = []
    seen_jids: dict[str, int] = {}
    cursor = 0

    while cursor < len(elements) and len(jobs) < _MAX_JOBS:
        result, new_cursor = walk_steps(elements, steps, start=cursor)

        # Stop if no title found or cursor didn't advance
        title = result.get("title")
        if not title or new_cursor <= cursor:
            break

        cursor = new_cursor

        url = _generate_url(board_url, title, seen_jids)

        # Build DiscoveredJob with extracted + default fields
        description = result.get("description")
        location = result.get("location")
        locations = None
        if location:
            if isinstance(location, list):
                locations = location
            else:
                locations = [loc.strip() for loc in location.split(",") if loc.strip()]

        job = DiscoveredJob(
            url=url,
            title=title,
            description=description,
            locations=locations or (defaults.get("locations") if not locations else None),
            employment_type=result.get("employment_type") or defaults.get("employment_type"),
            job_location_type=result.get("job_location_type") or defaults.get("job_location_type"),
            date_posted=result.get("date_posted") or defaults.get("date_posted"),
        )
        jobs.append(job)

    log.info("inline.discovered", url=board_url, jobs=len(jobs))
    return jobs


register("inline", discover, cost=60, rich=True)
