"""Mokahr ATS monitor.

Mokahr (app.mokahr.com) is a Chinese ATS used by companies like ZTE.
The API encrypts responses with AES-128-CBC using a per-response key
(``necromancer``) and a per-site IV embedded in the SPA HTML.

Config keys:
    org_id   — organisation slug (e.g. "zte")
    site_id  — numeric site ID (e.g. 47588)
    locale   — API locale (default "zh-CN")
"""

from __future__ import annotations

import base64
import json
import re
from html import unescape

import httpx
import structlog

from src.core.monitors import DiscoveredJob, register
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

_API_URL = "https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2"
_DETAIL_URL = "https://app.mokahr.com/api/outer/ats-apply/website/job"
_PAGE_SIZE = 20
_MAX_JOBS = 50_000

# Mokahr commitment values pass through unchanged — the central
# :func:`src.core.enum_normalize.normalize_employment_type` map handles
# the camelCase API codes (``fullTime``/``partTime``/``intern``/
# ``contract``) and the Chinese localised labels (``全职``/``兼职``/
# ``实习``) returned by the same endpoint.

# Mokahr ``salaryUnit`` enum (from
# ``static-ats.mokahr.com/recruitment-web-client/javascripts/vendor-…js``).
# The names map onto period strings recognised by
# ``src.core.salary_extract`` and ``processing.cpu._extract_salary_fields``
# (``"yearly"`` / ``"monthly"`` / ``"hourly"``); other unit codes have no
# matching period and are propagated raw so callers can decide what to do.
#
# The ``mult`` factor converts the raw value to "1 unit of the named
# period" — e.g. ``salaryUnit=0`` is "thousand RMB per month", so
# ``min=40 mult=1000 → 40000 monthly``.
_SALARY_UNIT: dict[int, tuple[str | None, int]] = {
    0: ("monthly", 1000),  # K_MONTH — thousand RMB / month (most common in CN)
    1: ("monthly", 1),  # YUAN_MONTH — RMB / month
    2: ("weekly", 1),  # YUAN_WEEK — RMB / week
    3: ("daily", 1),  # YUAN_DAY — RMB / day
    4: ("hourly", 1),  # YUAN_HOUR — RMB / hour
    5: ("per_task", 1),  # YUAN_EVERY_TIME — RMB / occasion
    6: ("monthly", 1),  # MONTH — RMB / month (legacy alias)
    7: ("weekly", 1),  # WEEK
    8: ("daily", 1),  # DAY
    9: ("hourly", 1),  # HOUR
    10: ("per_task", 1),  # EVERY_TIME
    11: ("yearly", 1),  # YEAR — RMB / year
}


def _decrypt(data_b64: str, key_str: str, iv_str: str) -> dict:
    """Decrypt an AES-128-CBC Mokahr response.

    Mokahr uses 16-character ASCII strings as the AES key and IV
    (not hex-encoded byte sequences).
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    key = key_str.encode("ascii")
    iv = iv_str.encode("ascii")
    ct = base64.b64decode(data_b64)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    padded = decryptor.update(ct) + decryptor.finalize()

    unpadder = PKCS7(128).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    return json.loads(plaintext)


async def _get_init_data(page_url: str, client: httpx.AsyncClient) -> dict | None:
    """Return the SPA's parsed ``init-data`` payload, or ``None`` on failure.

    The payload exposes the AES IV (``aesIv``) plus rich auxiliary data
    the detail API doesn't carry — most usefully ``jobsGroupedByLocation``,
    which maps ``cityId -> cityName``. The detail API only returns
    ``cityId`` for ``locations[i]``, so the scraper falls back to this
    map to produce human-readable city names.
    """
    resp = await client.get(page_url, follow_redirects=True)
    if resp.status_code != 200:
        return None
    m = re.search(r'id="init-data"[^>]*value="([^"]*)"', resp.text)
    if not m:
        return None
    raw = unescape(m.group(1))
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


async def _get_iv(page_url: str, client: httpx.AsyncClient) -> str | None:
    """Extract the AES IV from the SPA's ``init-data`` element."""
    init = await _get_init_data(page_url, client)
    if init is None:
        return None
    return init.get("aesIv")


def _build_city_name_map(init_data: dict | None) -> dict[int, str]:
    """Build a ``cityId -> cityName`` lookup from the SPA init-data.

    The detail API only returns ``cityId`` (no ``cityName``) on each
    ``locations[i]`` entry, so we mine the SPA's
    ``jobsGroupedByLocation`` block — which the listing UI uses for
    facet labels — and fall back to it during detail parsing.

    The SPA's group facets often round cityIds to district-level (e.g.
    Nanjing's Jiangning district ``320114`` instead of city-level
    ``320100``). To make city-level lookups still hit, every district
    code that resolves to a city name also seeds the parent city code
    with the same name when the parent isn't already covered.
    """
    if not isinstance(init_data, dict):
        return {}
    groups = init_data.get("jobsGroupedByLocation")
    if not isinstance(groups, list):
        return {}
    out: dict[int, str] = {}
    for g in groups:
        if not isinstance(g, dict):
            continue
        cid = g.get("cityId")
        label = g.get("label") or g.get("id")
        if not isinstance(cid, int) or not isinstance(label, str) or not label:
            continue
        out[cid] = label
        # Seed the parent city code (e.g. 320114 -> 320100) so
        # detail-API city-level codes still resolve. Don't overwrite an
        # existing parent entry — the original SPA mapping wins.
        if cid % 100 != 0 and cid >= 100000:
            parent = (cid // 100) * 100
            out.setdefault(parent, label)
    return out


def _parse_salary(detail: dict) -> dict | None:
    """Map Mokahr ``minSalary``/``maxSalary``/``salaryUnit`` -> ``base_salary``.

    Returns the same shape as :func:`src.core.scrapers.jsonld._extract_salary`
    (``{"currency", "min", "max", "unit"}``) so the value flows through
    the existing R2 staging path unchanged. Currency is hard-coded to
    ``"CNY"`` because Mokahr is a China-only ATS — the SPA only renders
    the ranges with RMB symbols / 元 / K suffixes.

    Returns ``None`` when both ``minSalary`` and ``maxSalary`` are
    falsy (the listing API returns ``0`` / ``null`` for the vast
    majority of postings — Mokahr's "no salary disclosed" sentinel).
    """
    raw_min = detail.get("minSalary")
    raw_max = detail.get("maxSalary")
    if not raw_min and not raw_max:
        return None
    unit_code = detail.get("salaryUnit")
    period: str | None = None
    mult = 1
    if isinstance(unit_code, int):
        mapped = _SALARY_UNIT.get(unit_code)
        if mapped is not None:
            period, mult = mapped
    try:
        sal_min: int | float | None = (
            float(raw_min) * mult if isinstance(raw_min, (int, float)) and raw_min else None
        )
        sal_max: int | float | None = (
            float(raw_max) * mult if isinstance(raw_max, (int, float)) and raw_max else None
        )
    except (TypeError, ValueError):
        return None
    if sal_min is None and sal_max is None:
        return None
    # Coerce whole numbers back to ints to keep R2 hashes stable.
    if isinstance(sal_min, float) and sal_min.is_integer():
        sal_min = int(sal_min)
    if isinstance(sal_max, float) and sal_max.is_integer():
        sal_max = int(sal_max)
    return {
        "currency": "CNY",
        "min": sal_min,
        "max": sal_max,
        "unit": period,
    }


def _build_board_url(org_id: str, site_id: int, path: str = "social-recruitment") -> str:
    return f"https://app.mokahr.com/{path}/{org_id}/{site_id}"


def _lookup_city_name(cid: int | None, city_name_map: dict[int, str] | None) -> str:
    """Resolve a Mokahr ``cityId`` against the SPA-mined name map.

    Mokahr's ``cityId`` is a GB/T 2260 administrative-division code
    (6 digits: 2 province + 2 city + 2 district). The SPA's
    ``jobsGroupedByLocation`` block mostly carries city-level codes
    (last two digits = ``00``), so a district-level cityId on the
    detail payload misses on a direct lookup. Walk up two levels:

    - district code (``110105``) -> city code (``110100``)
    - city code (``110100``) -> province/municipality code
      (``110000``) — needed for direct-administered municipalities
      (Beijing/Shanghai/Tianjin/Chongqing) whose SPA grouping uses
      ``xx0000`` and whose detail payloads cite a district directly.
    """
    if cid is None or not city_name_map:
        return ""
    direct = city_name_map.get(cid)
    if direct:
        return direct
    if cid < 100000:
        return ""
    # Step up to city level (zero out district digits).
    if cid % 100 != 0:
        parent = (cid // 100) * 100
        parent_name = city_name_map.get(parent)
        if parent_name:
            return parent_name
    # Step up to province level (zero out city + district digits).
    if cid % 10000 != 0:
        province = (cid // 10000) * 10000
        province_name = city_name_map.get(province)
        if province_name:
            return province_name
    return ""


def _parse_locations(job: dict, city_name_map: dict[int, str] | None = None) -> list[str] | None:
    """Parse a Mokahr ``locations`` block to ``["City, Country", …]``.

    The **listing** API returns ``cityName`` + ``provinceName`` directly.
    The **detail** API returns only ``cityId`` (no name), so the scraper
    passes a ``cityId -> cityName`` map mined from the SPA's
    ``init-data`` (see :func:`_build_city_name_map`). ``provinceName`` is
    used as a fallback when neither ``cityName`` nor a map hit is
    available.

    Returns ``None`` when no human-readable label can be produced — the
    pre-existing ``["中国"]`` collapse (when only ``country`` was usable)
    is preserved as a last-ditch fallback so the scraper still emits
    *something* in the truly degenerate case.
    """
    locs = job.get("locations")
    if not locs or not isinstance(locs, list):
        return None
    parts: list[str] = []
    seen: set[str] = set()
    for loc in locs:
        if isinstance(loc, dict):
            city = loc.get("cityName") or ""
            if not city:
                city = _lookup_city_name(loc.get("cityId"), city_name_map)
            if not city:
                # Last-ditch: use provinceName when no city name is available.
                city = loc.get("provinceName") or ""
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


def _job_url(org_id: str, site_id: int, job_id: str) -> str:
    return f"https://app.mokahr.com/social-recruitment/{org_id}/{site_id}#/job/{job_id}"


def _parse_metadata(job: dict) -> dict:
    """Collect non-canonical structured fields under ``metadata``.

    Mokahr exposes a handful of CN-specific labels (``education``,
    ``zhineng`` job-function, ``department``) that don't map onto any
    canonical :class:`JobContent` field. Stash them as raw strings so
    the labelled-postings dataset preserves them and downstream
    consumers (R2 history blobs, taxonomy enrichment) can use them
    when they understand the vocabulary.
    """
    metadata: dict = {}
    dept = job.get("department")
    if isinstance(dept, dict) and dept.get("name"):
        metadata["department"] = dept["name"]
    elif isinstance(dept, str) and dept:
        metadata["department"] = dept
    education = job.get("education")
    if isinstance(education, str) and education:
        metadata["education"] = education
    zhineng = job.get("zhineng")
    if isinstance(zhineng, dict) and zhineng.get("name"):
        metadata["job_function"] = zhineng["name"]
    elif isinstance(zhineng, str) and zhineng:
        metadata["job_function"] = zhineng
    return metadata


def _parse_experience(job: dict) -> dict | None:
    """Pack Mokahr's ``minExperience``/``maxExperience`` into ``extras``.

    Both fields are optional numbers (years). Returned shape mirrors
    the ``ExperienceRange`` used by
    :func:`src.core.experience_extract.extract_experience` — ``min_years``
    + ``max_years`` (the latter ``None`` for open-ended).
    """
    raw_min = job.get("minExperience")
    raw_max = job.get("maxExperience")
    if not isinstance(raw_min, (int, float)) and not isinstance(raw_max, (int, float)):
        return None
    out: dict = {}
    if isinstance(raw_min, (int, float)):
        out["min_years"] = float(raw_min)
    if isinstance(raw_max, (int, float)):
        out["max_years"] = float(raw_max)
    return out or None


def _parse_job(
    job: dict, org_id: str, site_id: int, city_name_map: dict[int, str] | None = None
) -> DiscoveredJob | None:
    job_id = job.get("id")
    title = job.get("title")
    if not job_id or not title:
        return None

    employment_type = job.get("commitment") or None

    published = job.get("publishedAt")

    metadata = _parse_metadata(job)
    base_salary = _parse_salary(job)
    experience = _parse_experience(job)
    extras: dict = {}
    if experience:
        extras["experience"] = experience

    return DiscoveredJob(
        url=_job_url(org_id, site_id, job_id),
        title=title,
        description=job.get("jobDescription"),
        locations=_parse_locations(job, city_name_map),
        employment_type=employment_type,
        date_posted=published,
        base_salary=base_salary,
        extras=extras or None,
        metadata=metadata or None,
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> list[DiscoveredJob]:
    """Fetch all jobs from Mokahr's encrypted API."""
    config = board.get("metadata") or {}
    if isinstance(config, str):
        config = json.loads(config) if config else {}

    org_id = config.get("org_id")
    site_id = config.get("site_id")
    locale = config.get("locale", "zh-CN")

    if not org_id or not site_id:
        raise ValueError("mokahr monitor requires org_id and site_id in config")

    # Determine the recruitment path from the board URL.
    board_url = board.get("board_url", "")
    m = re.search(r"app\.mokahr\.com/((?:social|campus)[_-](?:recruitment|apply))/", board_url)
    path = (
        m.group(1)
        if m
        else ("campus-recruitment" if "campus" in board_url else "social-recruitment")
    )

    page_url = _build_board_url(org_id, site_id, path)
    init_data = await _get_init_data(page_url, client)
    iv = init_data.get("aesIv") if init_data else None
    if not iv:
        raise RuntimeError(f"Could not extract AES IV from {page_url}")
    city_name_map = _build_city_name_map(init_data)

    jobs: list[DiscoveredJob] = []
    offset = 0
    truncated = False

    while True:
        if len(jobs) >= _MAX_JOBS:
            truncated = True
            log.warning("mokahr.truncated", org_id=org_id, total=len(jobs), cap=_MAX_JOBS)
            break
        body = {
            "orgId": org_id,
            "siteId": site_id,
            "limit": _PAGE_SIZE,
            "offset": offset,
            "needStat": offset == 0,
            "locale": locale,
        }
        resp = await client.post(_API_URL, json=body)
        resp.raise_for_status()
        envelope = resp.json()

        data_b64 = envelope.get("data")
        key_hex = envelope.get("necromancer")
        if not data_b64 or not key_hex:
            log.warning("mokahr.missing_encryption_fields", offset=offset)
            break

        payload = _decrypt(data_b64, key_hex, iv)
        inner = payload.get("data", {})
        raw_jobs = inner.get("jobs", [])

        if not raw_jobs:
            break

        for raw in raw_jobs:
            parsed = _parse_job(raw, org_id, site_id, city_name_map)
            if parsed:
                jobs.append(parsed)

        log.debug("mokahr.page", offset=offset, fetched=len(raw_jobs), total=len(jobs))
        offset += _PAGE_SIZE

        if len(raw_jobs) < _PAGE_SIZE:
            break

    log.info("mokahr.complete", org_id=org_id, site_id=site_id, total=len(jobs))
    if truncated:
        return truncated_rich_result(jobs)
    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect Mokahr from URL pattern."""
    m = re.search(
        r"app\.mokahr\.com/(?:social|campus)[_-](?:recruitment|apply)/([\w-]+)/(\d+)", url
    )
    if not m:
        return None
    return {"org_id": m.group(1), "site_id": int(m.group(2))}


register("mokahr", discover, cost=10, can_handle=can_handle, rich=True)
