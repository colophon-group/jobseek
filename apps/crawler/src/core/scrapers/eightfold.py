"""Eightfold.ai job detail scraper with JSON-LD + position API fallback.

Eightfold-powered career sites (``*.eightfold.ai``) normally inline a
``schema.org/JobPosting`` ``<script type="application/ld+json">`` block on
the server-rendered detail page, so the generic ``json-ld`` scraper has
worked for most tenants.  A subset of pages, however, return the HTML shell
without the JSON-LD block (possibly a cache/template variant or a
bot-detection decoy) — the page is a 200 OK but carries no structured data,
so ``json-ld`` fails and the crawler retries until the row is tombstoned.

This scraper wraps the JSON-LD path with a fallback to the public Eightfold
position API:

    GET https://{tenant}.eightfold.ai/api/apply/v2/jobs/{job_id}?domain={domain}

which returns the same job data as a structured JSON document (``name``,
``location``/``locations``, ``job_description``, ``t_create``, ``ats_job_id``,
…) for every active position id.  JSON-LD stays the fast path — a single
GET of the HTML, already cacheable/CDN-friendly — and the API is only hit
when the JSON-LD path extracts nothing, so the happy-path cost is
unchanged.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import structlog

from src.core.scrapers import JobContent, register
from src.core.scrapers.jsonld import parse_html as _jsonld_parse_html

log = structlog.get_logger()

# Matches the numeric job id at the start of the slug in /careers/job/{id}[-slug]
_JOB_ID_RE = re.compile(r"/careers/job/(\d+)")


def _parse_job_id(url: str) -> str | None:
    """Extract the numeric position id from an eightfold job URL."""
    match = _JOB_ID_RE.search(url)
    return match.group(1) if match else None


def _parse_domain(url: str) -> str | None:
    """Extract the ``domain`` query parameter (tenant key) or fall back to host.

    Eightfold URLs normally carry ``?domain=citi`` — the API requires this
    exact value.  If the parameter is missing, derive the tenant from the
    subdomain (``citi.eightfold.ai`` → ``citi``) as a best effort.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if qs.get("domain"):
        return qs["domain"][0]
    host = (parsed.hostname or "").lower()
    if host.endswith(".eightfold.ai"):
        return host[: -len(".eightfold.ai")]
    return None


def _parse_position_api(data: dict) -> JobContent:
    """Map an eightfold position-API response to :class:`JobContent`.

    The API returns a flat dict with the core position data at the top
    level. Field mapping:

    - ``posting_name`` / ``name`` → ``title``
    - ``job_description`` → ``description`` (already HTML)
    - ``locations`` (list) or ``location`` (scalar) → ``locations``
    - ``t_create`` (Unix seconds) → ``date_posted`` (ISO date)
    - ``ats_job_id``, ``display_job_id``, ``department``, ``business_unit``
      → ``metadata`` (preserved for downstream enrichment)
    """
    title = data.get("posting_name") or data.get("name")
    description = data.get("job_description")

    locations: list[str] | None = None
    raw_locs = data.get("locations")
    if isinstance(raw_locs, list):
        locs = [str(loc).strip() for loc in raw_locs if loc]
        if locs:
            locations = locs
    if locations is None and data.get("location"):
        locations = [str(data["location"]).strip()]

    date_posted: str | None = None
    t_create = data.get("t_create")
    if isinstance(t_create, (int, float)) and t_create > 0:
        try:
            date_posted = datetime.fromtimestamp(float(t_create), tz=UTC).date().isoformat()
        except (OverflowError, OSError, ValueError):
            date_posted = None

    metadata: dict = {}
    for key in ("ats_job_id", "display_job_id", "department", "business_unit"):
        val = data.get(key)
        if val:
            metadata[key] = val

    return JobContent(
        title=title,
        description=description,
        locations=locations,
        date_posted=date_posted,
        metadata=metadata or None,
    )


def _api_url(host: str, domain: str, job_id: str) -> str:
    return f"https://{host}/api/apply/v2/jobs/{job_id}?domain={domain}"


async def _fetch_position_api(url: str, http: httpx.AsyncClient) -> JobContent:
    """Call the eightfold position API for the given job URL."""
    job_id = _parse_job_id(url)
    domain = _parse_domain(url)
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if not (job_id and domain and host):
        log.warning("eightfold_scraper.unparseable_url", url=url)
        return JobContent()

    api_url = _api_url(host, domain, job_id)
    try:
        resp = await http.get(api_url, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        log.warning("eightfold_scraper.api_request_failed", url=api_url, error=str(exc))
        return JobContent()

    if resp.status_code != 200:
        log.warning("eightfold_scraper.api_failed", url=api_url, status=resp.status_code)
        return JobContent()

    try:
        data = resp.json()
    except ValueError as exc:
        log.warning("eightfold_scraper.api_parse_error", url=api_url, error=str(exc))
        return JobContent()

    if not isinstance(data, dict):
        return JobContent()
    return _parse_position_api(data)


def _merge_from_fallback(primary: JobContent, fallback: JobContent) -> JobContent:
    """Fill ``None`` / empty fields on *primary* from *fallback* in place."""
    for field in (
        "title",
        "description",
        "locations",
        "employment_type",
        "job_location_type",
        "date_posted",
        "base_salary",
        "language",
    ):
        current = getattr(primary, field)
        if current is None or (isinstance(current, list) and not current):
            setattr(primary, field, getattr(fallback, field))

    # Merge dict fields (extras, metadata) without overwriting existing keys
    for field in ("extras", "metadata"):
        current = getattr(primary, field)
        fb_val = getattr(fallback, field)
        if fb_val:
            if current:
                merged = {**fb_val, **current}  # primary wins on key collisions
                setattr(primary, field, merged)
            else:
                setattr(primary, field, fb_val)
    return primary


async def scrape(
    url: str,
    config: dict,
    http: httpx.AsyncClient,
    pw=None,
    **kwargs,
) -> JobContent:
    """Scrape an eightfold job page.

    Fast path: fetch the HTML and parse the JSON-LD ``JobPosting`` block.
    Fallback: when the JSON-LD path returns nothing (or no title), call the
    eightfold position API and merge its fields into whatever the JSON-LD
    path produced.
    """
    # Fast path — fetch the HTML once and hand it to the json-ld parser.
    html: str = ""
    try:
        response = await http.get(url, follow_redirects=True)
        response.raise_for_status()
        html = response.text
    except httpx.HTTPError as exc:
        log.warning("eightfold_scraper.html_fetch_failed", url=url, error=str(exc))

    content = _jsonld_parse_html(html, config) if html else JobContent()

    # If the fast path returned nothing useful, probe the position API.
    # ``title`` is the cheapest "did json-ld find anything" marker.
    if content.title:
        log.debug("eightfold_scraper.jsonld_hit", url=url)
        return content

    log.info("eightfold_scraper.jsonld_empty_falling_back", url=url)
    api_content = await _fetch_position_api(url, http)
    if api_content.title or api_content.description:
        log.info(
            "eightfold_scraper.api_hit",
            url=url,
            has_title=api_content.title is not None,
            has_description=api_content.description is not None,
        )
        return _merge_from_fallback(content, api_content)

    log.warning("eightfold_scraper.no_content", url=url)
    return content


register("eightfold", scrape)
