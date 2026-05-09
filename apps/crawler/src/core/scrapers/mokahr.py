"""Mokahr ATS detail scraper.

The Mokahr listing API (``/api/outer/ats-apply/website/jobs/v2``) returns
metadata only — title, locations, commitment, dates — but NOT the
``jobDescription`` field. The detail endpoint
(``/api/outer/ats-apply/website/job``, POST) returns the full record
including ``jobDescription`` (HTML).  Both responses are AES-128-CBC
encrypted with a per-response key (``necromancer``) and a per-site IV
extracted from the SPA's ``init-data`` attribute.

This scraper:

1. Parses ``org_id``, ``site_id``, and ``job_id`` from the source URL
   (e.g. ``https://app.mokahr.com/social-recruitment/zte/47588#/job/<uuid>``).
2. Fetches the SPA root to obtain the AES IV.
3. POSTs the detail endpoint and decrypts the response.
4. Returns a :class:`JobContent` with ``description`` (HTML) and any
   incidental fields the detail payload exposes (employment type,
   salary, date_posted) — the monitor already supplies title and
   locations on the rich path, but populating them defensively makes the
   scraper usable for ad-hoc URL probes too.

Pair with the ``mokahr`` monitor and declare
``scraper_config: {"enrich": ["description"]}`` in ``boards.csv`` so
``processing/board.py`` takes the ``_INSERT_RICH_JOB_ENRICH`` path
(``next_scrape_at = now()``) and queues the scrape.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.monitors.mokahr import _COMMITMENT_MAP, _decrypt, _get_iv
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_DETAIL_URL = "https://app.mokahr.com/api/outer/ats-apply/website/job"

# Source URLs from the monitor look like:
#   https://app.mokahr.com/social-recruitment/<org>/<site>#/job/<job_id>
#   https://app.mokahr.com/campus-recruitment/<org>/<site>#/job/<job_id>
_URL_RE = re.compile(
    r"app\.mokahr\.com/(?P<path>(?:social|campus)[_-](?:recruitment|apply))/"
    r"(?P<org>[\w-]+)/(?P<site>\d+)(?:[#/?].*?/job/(?P<job>[\w-]+))?",
    re.IGNORECASE,
)


def _parse_url(url: str) -> tuple[str, str, int, str] | None:
    """Return ``(path, org_id, site_id, job_id)`` or ``None``."""
    m = _URL_RE.search(url)
    if not m:
        return None
    job_id = m.group("job")
    if not job_id:
        return None
    try:
        site_id = int(m.group("site"))
    except (TypeError, ValueError):
        return None
    return m.group("path"), m.group("org"), site_id, job_id


def _parse_locations(detail: dict) -> list[str] | None:
    """Mirror ``mokahr._parse_locations`` — kept local to avoid coupling."""
    locs = detail.get("locations")
    if not isinstance(locs, list) or not locs:
        return None
    parts: list[str] = []
    seen: set[str] = set()
    for loc in locs:
        if isinstance(loc, dict):
            city = loc.get("cityName", "")
            country = loc.get("country", "")
            s = ", ".join(p for p in (city, country) if p)
        elif isinstance(loc, str):
            s = loc
        else:
            continue
        if s and s not in seen:
            parts.append(s)
            seen.add(s)
    return parts or None


def _parse_detail(detail: dict) -> JobContent:
    """Map a decrypted Mokahr detail payload to :class:`JobContent`."""
    description = detail.get("jobDescription") or None
    title = detail.get("title") or None
    locations = _parse_locations(detail)
    employment_type = _COMMITMENT_MAP.get(detail.get("commitment", ""))
    date_posted = detail.get("publishedAt") or detail.get("openedAt") or None
    if isinstance(date_posted, str) and "T" in date_posted:
        date_posted = date_posted.split("T", 1)[0]

    metadata: dict = {}
    dept = detail.get("department")
    if isinstance(dept, dict) and dept.get("name"):
        metadata["department"] = dept["name"]
    elif isinstance(dept, str) and dept:
        metadata["department"] = dept

    return JobContent(
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        date_posted=date_posted,
        metadata=metadata or None,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch the Mokahr detail endpoint for *url* and return a :class:`JobContent`.

    Returns an empty :class:`JobContent` (so the pipeline records a soft
    miss rather than crashing) when:

    - the URL doesn't match the Mokahr social/campus recruitment shape,
    - the SPA root doesn't expose the AES IV,
    - the encrypted envelope is malformed,
    - the upstream returns a non-2xx status.
    """
    parsed = _parse_url(url)
    if parsed is None:
        log.warning("mokahr_scraper.unparseable_url", url=url)
        return JobContent()
    path, org_id, site_id, job_id = parsed

    locale = config.get("locale", "zh-CN")
    # The monitor's _job_url always emits ``social-recruitment`` regardless of
    # the originating board path, so a chunk of legacy source URLs for campus
    # site_ids are mis-prefixed. Try the URL's own path first, then the
    # opposite one as a fallback, before giving up.
    paths_to_try = [path]
    other = "campus-recruitment" if "social" in path else "social-recruitment"
    if other != path:
        paths_to_try.append(other)

    iv: str | None = None
    page_url: str | None = None
    for attempt_path in paths_to_try:
        page_url = f"https://app.mokahr.com/{attempt_path}/{org_id}/{site_id}"
        iv = await _get_iv(page_url, http)
        if iv:
            break
    if not iv:
        log.warning("mokahr_scraper.no_iv", url=url, page_url=page_url)
        return JobContent()

    body = {"orgId": org_id, "siteId": site_id, "jobId": job_id, "locale": locale}
    try:
        resp = await http.post(_DETAIL_URL, json=body)
    except httpx.HTTPError as exc:
        log.warning("mokahr_scraper.transport_error", url=url, error=str(exc))
        return JobContent()

    if resp.status_code != 200:
        log.warning("mokahr_scraper.detail_failed", url=url, status=resp.status_code)
        return JobContent()

    try:
        envelope = resp.json()
    except ValueError:
        log.warning("mokahr_scraper.bad_json", url=url)
        return JobContent()

    data_b64 = envelope.get("data")
    key = envelope.get("necromancer")
    if not data_b64 or not key:
        log.warning("mokahr_scraper.missing_encryption_fields", url=url)
        return JobContent()

    try:
        payload = _decrypt(data_b64, key, iv)
    except Exception:
        log.warning("mokahr_scraper.decrypt_failed", url=url, exc_info=True)
        return JobContent()

    detail = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(detail, dict):
        log.warning("mokahr_scraper.no_data", url=url)
        return JobContent()

    return _parse_detail(detail)


register("mokahr", scrape)
