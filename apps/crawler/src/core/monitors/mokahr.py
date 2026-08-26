"""Mokahr ATS monitor.

Mokahr is a Chinese ATS used by companies like ZTE. Tenants normally use
``app.mokahr.com``, but the same SPA and API can also be served from a
company-owned custom domain.
The API encrypts responses with AES-128-CBC using a per-response key
(``necromancer``) and a per-site IV embedded in the SPA HTML.

Config keys:
    org_id   — organisation slug (e.g. "zte")
    site_id  — numeric site ID (e.g. 47588)
    locale   — API locale (default "zh-CN")
    partitions — optional bounded list of additional official Mokahr sites
                 for the same organisation. Each item contains an exact
                 ``board_url`` and ``site_id``. Jobs are unioned by the
                 provider ID, with the primary board and then partition list
                 order acting as the deterministic source preference.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from html import unescape
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.shared.truncation import truncated_rich_result

if TYPE_CHECKING:
    from src.core.monitor import MonitorResult

log = structlog.get_logger()

_DEFAULT_ORIGIN = "https://app.mokahr.com"
_LIST_PATH = "/api/outer/ats-apply/website/jobs/v2"
_PAGE_SIZE = 50
_MAX_JOBS = 50_000
_MAX_PARTITIONS = 16
_ROUTE_RE = re.compile(r"(?:social|campus)[_-](?:recruitment|apply)", re.IGNORECASE)
_ORG_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_SITE_ID_RE = re.compile(r"[1-9]\d{0,11}")
_JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_LOCALE_RE = re.compile(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{2,8}){0,2}")
_OPEN_STATUS = "open"
_INACTIVE_STATUSES = frozenset({"closed", "pause"})
_CLOSED_PAGE_TITLE_RE = re.compile(
    r"<title[^>]*>\s*当前网页已关停\s*</title>",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _Partition:
    """One authenticated Mokahr site participating in a board union."""

    page_url: str
    origin: str
    path: str
    org_id: str
    site_id: int


@dataclass(slots=True)
class _PartitionResult:
    """Validated active inventory from one provider site."""

    jobs_by_id: dict[str, DiscoveredJob]
    advertised_total: int
    inactive_total: int
    truncated: bool = False


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


async def _get_init_data(
    page_url: str,
    client: httpx.AsyncClient,
    *,
    raise_on_404: bool = False,
) -> dict | None:
    """Return the SPA's parsed ``init-data`` payload, or ``None`` on failure.

    The payload exposes the AES IV (``aesIv``) plus rich auxiliary data
    the detail API doesn't carry — most usefully ``jobsGroupedByLocation``,
    which maps ``cityId -> cityName``. The detail API only returns
    ``cityId`` for ``locations[i]``, so the scraper falls back to this
    map to produce human-readable city names.
    """
    resp = await client.get(page_url, follow_redirects=True)
    if resp.status_code == 404 and raise_on_404:
        raise BoardGoneError("Mokahr board page returned 404", url=str(resp.url))
    if resp.status_code != 200:
        return None
    if raise_on_404 and _CLOSED_PAGE_TITLE_RE.search(resp.text):
        raise BoardGoneError("Mokahr board page is explicitly shut down", url=str(resp.url))
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


def _origin(url: str) -> str | None:
    """Return a normalized, public-facing HTTPS origin or ``None``.

    Custom Mokahr hosts become API origins, so reject credentials, cleartext
    HTTP, and non-standard ports rather than reflecting an untrusted netloc
    into subsequent requests.
    """
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return f"https://{host}"


def _parse_board_route(url: str) -> tuple[str, str, int] | None:
    """Return ``(path, org_id, site_id)`` for an exact Mokahr SPA route."""
    if _origin(url) is None:
        return None
    parsed = urlsplit(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) != 3
        or _ROUTE_RE.fullmatch(segments[0]) is None
        or _ORG_ID_RE.fullmatch(segments[1]) is None
        or _SITE_ID_RE.fullmatch(segments[2]) is None
    ):
        return None
    return segments[0], segments[1], int(segments[2])


def _build_board_url(
    org_id: str,
    site_id: int,
    path: str = "social-recruitment",
    origin: str = _DEFAULT_ORIGIN,
) -> str:
    return f"{origin.rstrip('/')}/{path}/{org_id}/{site_id}"


def _site_id(value: object, *, field: str) -> int:
    """Return a strictly bounded numeric Mokahr site ID."""
    if isinstance(value, str) and _SITE_ID_RE.fullmatch(value):
        value = int(value)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or _SITE_ID_RE.fullmatch(str(value)) is None
    ):
        raise ValueError(f"{field} must be a positive bounded Mokahr site ID")
    return value


def _partition(
    board_url: str,
    org_id: str,
    site_id: int,
    *,
    require_route: bool,
) -> _Partition:
    """Build one exact source partition and reject contradictory routes."""
    if not isinstance(board_url, str) or not board_url or len(board_url) > 2_048:
        raise ValueError("Mokahr partition board_url must be non-empty bounded text")
    origin = _origin(board_url)
    if origin is None:
        raise ValueError("Mokahr board URL must use a trusted HTTPS origin")
    route = _parse_board_route(board_url)
    if route is None:
        if require_route:
            raise ValueError("Additional Mokahr partitions require an exact recruitment route")
        path = "campus-recruitment" if "campus" in board_url.lower() else "social-recruitment"
    else:
        path, route_org_id, route_site_id = route
        if route_org_id != org_id or route_site_id != site_id:
            raise ValueError("Mokahr board route identity does not match configured org_id/site_id")
    return _Partition(
        page_url=_build_board_url(org_id, site_id, path, origin),
        origin=origin,
        path=path,
        org_id=org_id,
        site_id=site_id,
    )


def _configured_partitions(
    board_url: str, config: dict, org_id: str, site_id: int
) -> list[_Partition]:
    """Return the primary site plus a bounded, unique list of official sites."""
    partitions = [_partition(board_url, org_id, site_id, require_route=False)]
    raw_partitions = config.get("partitions", [])
    if not isinstance(raw_partitions, list):
        raise ValueError("mokahr partitions must be a list")
    if len(raw_partitions) + 1 > _MAX_PARTITIONS:
        raise ValueError(f"mokahr supports at most {_MAX_PARTITIONS} source partitions")

    for index, raw in enumerate(raw_partitions):
        if not isinstance(raw, dict) or set(raw) != {"board_url", "site_id"}:
            raise ValueError("each mokahr partition must contain exactly board_url and site_id")
        partition_board_url = raw.get("board_url")
        if not isinstance(partition_board_url, str):
            raise ValueError("Mokahr partition board_url must be text")
        partition_site_id = _site_id(raw.get("site_id"), field=f"partitions[{index}].site_id")
        partitions.append(
            _partition(
                partition_board_url,
                org_id,
                partition_site_id,
                require_route=True,
            )
        )

    seen_sites: set[int] = set()
    seen_pages: set[str] = set()
    for source in partitions:
        if source.site_id in seen_sites or source.page_url in seen_pages:
            raise ValueError("mokahr source partitions must have unique site identities")
        seen_sites.add(source.site_id)
        seen_pages.add(source.page_url)
    return partitions


def _validated_init_data(init_data: dict | None, source: _Partition) -> tuple[str, dict[int, str]]:
    """Authenticate the SPA bootstrap against the exact configured source."""
    if not isinstance(init_data, dict):
        raise RuntimeError(f"Could not parse Mokahr init-data from {source.page_url}")
    iv = init_data.get("aesIv")
    if not isinstance(iv, str) or len(iv) != 16 or not iv.isascii():
        raise RuntimeError(f"Could not extract a valid AES IV from {source.page_url}")

    org = init_data.get("org")
    if not isinstance(org, dict) or org.get("id") != source.org_id:
        raise ValueError("Mokahr init-data organisation does not match configured org_id")
    bootstrap_site_id = _site_id(
        init_data.get("siteId"),
        field="Mokahr init-data siteId",
    )
    org_site_id = _site_id(org.get("siteId"), field="Mokahr init-data org.siteId")
    if bootstrap_site_id != source.site_id or org_site_id != source.site_id:
        raise ValueError("Mokahr init-data site identity does not match configured site_id")

    site_type = org.get("type")
    expected_type = "camp" if source.path.lower().startswith("campus") else "social"
    if site_type is not None and site_type != expected_type:
        raise ValueError("Mokahr init-data site type does not match the configured route")
    return iv, _build_city_name_map(init_data)


def _validated_list_payload(
    envelope: object,
    iv: str,
    source: _Partition,
) -> tuple[list[dict], int]:
    """Decrypt and validate one authoritative Mokahr listing page."""
    if not isinstance(envelope, dict):
        raise ValueError("Mokahr listing envelope must be an object")
    data_b64 = envelope.get("data")
    key = envelope.get("necromancer")
    if not isinstance(data_b64, str) or not data_b64 or not isinstance(key, str) or not key:
        raise ValueError("Mokahr listing envelope is missing encryption fields")
    try:
        payload = _decrypt(data_b64, key, iv)
    except Exception as exc:
        raise ValueError("Mokahr listing payload could not be decrypted") from exc
    if not isinstance(payload, dict):
        raise ValueError("Mokahr listing payload must be an object")
    if payload.get("success") is not True or payload.get("code") != 0:
        raise ValueError(
            "Mokahr listing API rejected the request "
            f"(code={payload.get('code')!r}, msg={payload.get('msg')!r})"
        )

    inner = payload.get("data")
    if not isinstance(inner, dict):
        raise ValueError("Mokahr listing payload is missing data")
    raw_jobs = inner.get("jobs")
    stats = inner.get("jobStats")
    if not isinstance(raw_jobs, list) or any(not isinstance(job, dict) for job in raw_jobs):
        raise ValueError("Mokahr listing jobs must be a list of objects")
    if not isinstance(stats, dict) or stats.get("orgId") != source.org_id:
        raise ValueError("Mokahr listing statistics do not authenticate the organisation")
    total = stats.get("total")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise ValueError("Mokahr listing statistics contain an invalid total")
    return raw_jobs, total


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


def _job_url(
    org_id: str,
    site_id: int,
    job_id: str,
    *,
    origin: str = _DEFAULT_ORIGIN,
    path: str = "social-recruitment",
) -> str:
    return f"{origin.rstrip('/')}/{path}/{org_id}/{site_id}#/job/{job_id}"


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
    job: dict,
    org_id: str,
    site_id: int,
    city_name_map: dict[int, str] | None = None,
    *,
    origin: str = _DEFAULT_ORIGIN,
    path: str = "social-recruitment",
) -> DiscoveredJob | None:
    job_id = job.get("id")
    title = job.get("title")
    if not job_id or not title:
        return None

    employment_type = job.get("commitment") or None

    published = job.get("publishedAt")

    metadata = _parse_metadata(job)
    metadata["provider_id"] = str(job_id)
    metadata["provider_site_id"] = site_id
    base_salary = _parse_salary(job)
    experience = _parse_experience(job)
    extras: dict = {}
    if experience:
        extras["experience"] = experience

    return DiscoveredJob(
        url=_job_url(org_id, site_id, job_id, origin=origin, path=path),
        title=title,
        description=job.get("jobDescription"),
        locations=_parse_locations(job, city_name_map),
        employment_type=employment_type,
        date_posted=published,
        base_salary=base_salary,
        extras=extras or None,
        metadata=metadata or None,
    )


async def _fetch_list_page(
    source: _Partition,
    client: httpx.AsyncClient,
    iv: str,
    locale: str,
    offset: int,
    *,
    limit: int | None = None,
) -> tuple[list[dict], int]:
    """Fetch one page with authoritative statistics enabled."""
    page_limit = _PAGE_SIZE if limit is None else limit
    body = {
        "orgId": source.org_id,
        "siteId": source.site_id,
        "limit": page_limit,
        "offset": offset,
        # Every page carries the total. Treating later pages as uncounted was
        # the source of silent partial-success and mass gone-detection risk.
        "needStat": True,
        "locale": locale,
    }
    resp = await client.post(f"{source.origin}{_LIST_PATH}", json=body)
    resp.raise_for_status()
    try:
        envelope = resp.json()
    except ValueError as exc:
        raise ValueError("Mokahr listing response is not JSON") from exc
    return _validated_list_payload(envelope, iv, source)


async def _discover_partition(
    source: _Partition,
    client: httpx.AsyncClient,
    locale: str,
) -> _PartitionResult:
    """Read one site completely while preserving authoritative count semantics."""
    init_data = await _get_init_data(source.page_url, client, raise_on_404=True)
    iv, city_name_map = _validated_init_data(init_data, source)

    expected_total: int | None = None
    raw_seen = 0
    inactive_total = 0
    seen_ids: set[str] = set()
    active_jobs: dict[str, DiscoveredJob] = {}

    while True:
        request_limit = min(_PAGE_SIZE, max(_MAX_JOBS - raw_seen, 1))
        raw_jobs, page_total = await _fetch_list_page(
            source,
            client,
            iv,
            locale,
            raw_seen,
            limit=request_limit,
        )
        if expected_total is None:
            expected_total = page_total
        elif page_total != expected_total:
            raise ValueError(
                "Mokahr advertised total changed during pagination "
                f"for site {source.site_id} ({expected_total} -> {page_total})"
            )

        assert expected_total is not None
        raw_limit = min(expected_total, _MAX_JOBS)
        if expected_total == 0:
            if raw_jobs:
                raise ValueError("Mokahr returned jobs while advertising an empty inventory")
            # A second independently authenticated zero prevents one transient
            # empty response from authoritatively tombstoning the whole board.
            confirm_jobs, confirm_total = await _fetch_list_page(
                source,
                client,
                iv,
                locale,
                0,
            )
            if confirm_total != 0 or confirm_jobs:
                raise ValueError("Mokahr zero inventory did not converge on confirmation")
            return _PartitionResult({}, 0, 0)

        if not raw_jobs:
            raise ValueError(
                f"Mokahr site {source.site_id} returned an empty page before its total"
            )
        if len(raw_jobs) > request_limit or raw_seen + len(raw_jobs) > expected_total:
            raise ValueError(f"Mokahr site {source.site_id} returned an inconsistent page size")

        for raw in raw_jobs:
            if raw.get("orgId") != source.org_id:
                raise ValueError("Mokahr job row does not match the configured organisation")
            job_id = raw.get("id")
            if not isinstance(job_id, str) or _JOB_ID_RE.fullmatch(job_id) is None:
                raise ValueError("Mokahr job row has an invalid provider ID")
            if job_id in seen_ids:
                raise ValueError(f"Mokahr site {source.site_id} repeated provider ID {job_id!r}")
            seen_ids.add(job_id)

            status = raw.get("status")
            if not isinstance(status, str):
                raise ValueError("Mokahr job row is missing an explicit status")
            normalized_status = status.lower()
            if normalized_status == _OPEN_STATUS:
                title = raw.get("title")
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("Open Mokahr job row is missing a title")
                parsed = _parse_job(
                    raw,
                    source.org_id,
                    source.site_id,
                    city_name_map,
                    origin=source.origin,
                    path=source.path,
                )
                if parsed is None:
                    raise ValueError("Open Mokahr job row could not be parsed")
                active_jobs[job_id] = parsed
            elif normalized_status in _INACTIVE_STATUSES:
                inactive_total += 1
            else:
                raise ValueError(f"Mokahr job row has unknown status {status!r}")

        raw_seen += len(raw_jobs)
        log.debug(
            "mokahr.page",
            org_id=source.org_id,
            site_id=source.site_id,
            offset=raw_seen - len(raw_jobs),
            fetched=len(raw_jobs),
            raw_seen=raw_seen,
            advertised=expected_total,
            active=len(active_jobs),
        )
        if raw_seen >= raw_limit:
            break
        if len(raw_jobs) < request_limit:
            raise ValueError(f"Mokahr site {source.site_id} ended before its advertised total")

    truncated = expected_total > _MAX_JOBS
    if not truncated and raw_seen != expected_total:
        raise ValueError(
            f"Mokahr site {source.site_id} returned {raw_seen} rows for "
            f"advertised total {expected_total}"
        )
    return _PartitionResult(
        active_jobs,
        expected_total,
        inactive_total,
        truncated=truncated,
    )


async def discover(
    board: dict, client: httpx.AsyncClient, pw=None
) -> list[DiscoveredJob] | MonitorResult:
    """Fetch a complete, active-only union from authenticated Mokahr sites."""
    config = board.get("metadata") or {}
    if isinstance(config, str):
        config = json.loads(config) if config else {}
    if not isinstance(config, dict):
        raise ValueError("mokahr monitor config must be an object")

    org_id = config.get("org_id")
    if not isinstance(org_id, str) or _ORG_ID_RE.fullmatch(org_id) is None:
        raise ValueError("mokahr monitor requires a valid org_id in config")
    site_id = _site_id(config.get("site_id"), field="mokahr site_id")
    locale = config.get("locale", "zh-CN")
    if not isinstance(locale, str) or _LOCALE_RE.fullmatch(locale) is None:
        raise ValueError("mokahr locale must be a bounded language tag")

    sources = _configured_partitions(board.get("board_url", ""), config, org_id, site_id)
    partition_results = await asyncio.gather(
        *(_discover_partition(source, client, locale) for source in sources)
    )

    # Resolve aliases by stable provider ID, never by title, locale, source
    # host, or list position. Config order is the fixed source preference, so
    # response timing and pagination order cannot change the selected alias.
    selected: dict[str, DiscoveredJob] = {}
    truncated = False
    contextual_title_differences = 0
    for result in partition_results:
        truncated = truncated or result.truncated
        for provider_id, job in result.jobs_by_id.items():
            existing = selected.get(provider_id)
            if existing is None:
                if len(selected) >= _MAX_JOBS:
                    truncated = True
                    continue
                selected[provider_id] = job
            elif existing.title != job.title:
                contextual_title_differences += 1

    jobs = list(selected.values())
    log.info(
        "mokahr.complete",
        org_id=org_id,
        partitions=len(sources),
        advertised=sum(result.advertised_total for result in partition_results),
        inactive=sum(result.inactive_total for result in partition_results),
        active_unique=len(jobs),
        contextual_title_differences=contextual_title_differences,
        truncated=truncated,
    )
    if truncated:
        return truncated_rich_result(jobs)
    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect shared-host and custom-domain Mokahr career sites."""
    origin = _origin(url)
    if origin is None:
        return None
    route = _parse_board_route(url)
    if route is not None and origin == _DEFAULT_ORIGIN:
        return {"org_id": route[1], "site_id": route[2]}

    # A route-shaped path is not proof on an arbitrary host. Custom domains
    # must expose the Mokahr SPA bootstrap, and any route IDs must agree with
    # that authoritative payload.
    if client is None:
        return None
    try:
        init_data = await _get_init_data(url, client)
    except httpx.HTTPError:
        return None
    if not init_data or not init_data.get("aesIv"):
        return None
    org = init_data.get("org")
    org_id = org.get("id") if isinstance(org, dict) else None
    site_id = init_data.get("siteId")
    if isinstance(site_id, str) and _SITE_ID_RE.fullmatch(site_id):
        site_id = int(site_id)
    if (
        not isinstance(org_id, str)
        or _ORG_ID_RE.fullmatch(org_id) is None
        or isinstance(site_id, bool)
        or not isinstance(site_id, int)
        or site_id <= 0
    ):
        return None
    if route is not None and (route[1] != org_id or route[2] != site_id):
        return None
    return {"org_id": org_id, "site_id": site_id}


register("mokahr", discover, cost=10, can_handle=can_handle, rich=True)
