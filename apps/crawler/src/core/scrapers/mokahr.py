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
2. Fetches the SPA root to obtain the parsed ``init-data`` block — both
   the AES IV (``aesIv``) and the ``cityId -> cityName`` lookup mined
   from ``jobsGroupedByLocation``.
3. POSTs the detail endpoint and decrypts the response.
4. Returns a :class:`JobContent` populated with **every structured
   field the detail payload exposes** (Rule 16):

   - ``title`` / ``description`` (HTML)
   - ``locations`` — joined ``"City, Country"``; ``cityId`` is resolved
     against the SPA-mined map because the detail API only carries
     ``cityId``, never ``cityName``
   - ``employment_type`` — ``commitment`` (Chinese ``全职/兼职/实习``)
     mapped via :data:`mokahr._COMMITMENT_MAP`
   - ``base_salary`` — ``minSalary``/``maxSalary``/``salaryUnit``
     with ``currency="CNY"``; ``salaryUnit=0`` ("K/月") multiplies the
     range by 1000 so downstream sees full RMB amounts
   - ``extras.experience`` — ``minExperience``/``maxExperience`` years
   - ``metadata.{department,education,job_function}`` — raw CN labels
   - ``date_posted`` — ``publishedAt`` or ``openedAt`` (date-only)

Pair with the ``mokahr`` monitor and declare
``scraper_config: {"enrich": ["description"]}`` in ``boards.csv`` so
``processing/board.py`` takes the ``_INSERT_RICH_JOB_ENRICH`` path
(``next_scrape_at = now()``) and queues the scrape.
"""

from __future__ import annotations

import re

import httpx
import structlog

from src.core.monitors.mokahr import (
    _COMMITMENT_MAP,
    _build_city_name_map,
    _decrypt,
    _get_init_data,
    _parse_experience,
    _parse_metadata,
    _parse_salary,
)
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


def _parse_detail(detail: dict, city_name_map: dict[int, str] | None = None) -> JobContent:
    """Map a decrypted Mokahr detail payload to :class:`JobContent`.

    Extracts every structured field the detail API exposes:

    - ``title`` / ``jobDescription``
    - ``locations`` — joined ``"City, Country"``; ``cityId`` is resolved
      against *city_name_map* (mined from the SPA's ``init-data``)
      because the detail API only returns ``cityId``, never ``cityName``
    - ``commitment`` -> ``employment_type`` (Chinese ``全职/兼职/实习``
      via :data:`_COMMITMENT_MAP`)
    - ``minSalary`` / ``maxSalary`` / ``salaryUnit`` -> ``base_salary``
      with ``currency="CNY"``; ``salaryUnit=0`` ("K_MONTH") multiplies
      by 1000 so downstream sees full RMB amounts
    - ``minExperience`` / ``maxExperience`` -> ``extras.experience``
    - ``education`` / ``zhineng.name`` / ``department.name`` ->
      ``metadata`` (raw CN labels, e.g. ``"硕士"``)
    - ``publishedAt`` / ``openedAt`` -> ``date_posted`` (date-only)
    """
    # Reuse monitor helpers — _parse_locations sits in the monitor
    # because it's locally important; the rest are imported above.
    from src.core.monitors.mokahr import _parse_locations as _parse_locs

    description = detail.get("jobDescription") or None
    title = detail.get("title") or None
    locations = _parse_locs(detail, city_name_map)
    employment_type = _COMMITMENT_MAP.get(detail.get("commitment", ""))
    date_posted = detail.get("publishedAt") or detail.get("openedAt") or None
    if isinstance(date_posted, str) and "T" in date_posted:
        date_posted = date_posted.split("T", 1)[0]

    metadata = _parse_metadata(detail)
    base_salary = _parse_salary(detail)
    experience = _parse_experience(detail)
    extras: dict = {}
    if experience:
        extras["experience"] = experience

    return JobContent(
        title=title,
        description=description,
        locations=locations,
        employment_type=employment_type,
        date_posted=date_posted,
        base_salary=base_salary,
        extras=extras or None,
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

    init_data: dict | None = None
    page_url: str | None = None
    for attempt_path in paths_to_try:
        page_url = f"https://app.mokahr.com/{attempt_path}/{org_id}/{site_id}"
        init_data = await _get_init_data(page_url, http)
        if init_data and init_data.get("aesIv"):
            break
    iv = init_data.get("aesIv") if init_data else None
    if not iv:
        log.warning("mokahr_scraper.no_iv", url=url, page_url=page_url)
        return JobContent()
    # Build a cityId -> cityName lookup from the SPA listing data, so
    # detail-API ``locations`` (which only carries ``cityId``) can still
    # produce human-readable city names. Empty dict on miss is fine —
    # the parser falls back to ``provinceName`` then ``country``.
    city_name_map = _build_city_name_map(init_data)

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

    return _parse_detail(detail, city_name_map)


register("mokahr", scrape)
