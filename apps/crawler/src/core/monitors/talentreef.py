"""TalentReef public career-page monitor.

TalentReef's current ``apply.jobappnetwork.com`` pages are client-rendered,
but their career-page definition and posting search are public JSON APIs.  A
career-page alias carries the client and brand scope, which is essential: the
same client can publish several brand-specific boards.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, DiscoveredJob, register
from src.shared.truncation import truncated_rich_result

log = structlog.get_logger()

_BOARD_HOST = "apply.jobappnetwork.com"
_API_ORIGIN = "https://prod-kong.internal.talentreef.com/apply"
_ALIAS_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}", re.IGNORECASE)
_CLIENT_RE = re.compile(r"[0-9]{1,20}")
_PAGE_SIZE = 1_000
MAX_JOBS = 50_000


def _identity_from_url(url: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() != _BOARD_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts or len(parts) > 2 or not _ALIAS_RE.fullmatch(parts[0]):
        return None
    locale = parts[1].lower() if len(parts) == 2 else "en"
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", locale):
        return None
    return parts[0].lower(), locale


def _career_page_url(alias: str) -> str:
    return f"{_API_ORIGIN}/careerPages/alias/{alias}"


def _search_url(search_locale: str) -> str:
    return f"{_API_ORIGIN}/proxy-es/search-{search_locale}/posting/_search"


def _search_locale(locale: str) -> str:
    return "en-us" if locale == "en" else locale


def _clean_strings(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


async def _fetch_career_page(
    alias: str,
    locale: str,
    client: httpx.AsyncClient,
) -> dict:
    response = await client.get(
        _career_page_url(alias),
        headers={"Accept": "application/json", "Referer": f"https://{_BOARD_HOST}/{alias}"},
        follow_redirects=True,
        timeout=30,
    )
    if response.status_code in {404, 410}:
        raise BoardGoneError(
            f"TalentReef board {alias!r} no longer exists",
            url=str(response.url),
            status_code=response.status_code,
        )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("TalentReef career-page response is not a list")

    published = [item for item in payload if isinstance(item, dict) and item.get("published")]
    if not published:
        raise ValueError(f"TalentReef board {alias!r} has no published career page")
    selected = next(
        (item for item in published if str(item.get("locale", "")).lower() == locale),
        None,
    )
    if selected is None:
        selected = next((item for item in published if item.get("defaultLocale")), published[0])

    client_id = str(selected.get("clientId", "")).strip()
    brands = _clean_strings(selected.get("brands"))
    if not _CLIENT_RE.fullmatch(client_id) or not brands:
        raise ValueError("TalentReef career page omitted its client or brand scope")
    return {
        "alias": alias,
        "client_id": client_id,
        "locale": str(selected.get("locale") or locale).lower(),
        "brands": brands,
    }


def _search_payload(metadata: Mapping[str, object], start: int) -> dict:
    client_id = str(metadata["client_id"])
    brands = list(metadata["brands"])
    return {
        "from": start,
        "size": _PAGE_SIZE,
        "_source": [
            "positionType",
            "category",
            "description",
            "address",
            "jobId",
            "clientId",
            "clientName",
            "brandId",
            "brand",
            "internalOrExternal",
            "url",
            "postingUuid",
            "isSalaried",
            "minCompensation",
            "maxCompensation",
            "pubCompensation",
            "createdDate",
        ],
        "query": {
            "bool": {
                "filter": [
                    {"terms": {"clientId.raw": [client_id]}},
                    {"terms": {"brand.raw": brands}},
                    {"terms": {"internalOrExternal": [{"internalOrExternal": "externalOnly"}]}},
                ]
            }
        },
        "sort": [{"positionType.raw": {"order": "asc"}}],
    }


async def _fetch_search_page(
    metadata: Mapping[str, object],
    start: int,
    client: httpx.AsyncClient,
) -> tuple[list[dict], int]:
    locale = _search_locale(str(metadata["locale"]))
    response = await client.post(
        _search_url(locale),
        json=_search_payload(metadata, start),
        headers={"Accept": "application/json", "Referer": f"https://{_BOARD_HOST}/"},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    raw_hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(raw_hits, dict) or not isinstance(raw_hits.get("hits"), list):
        raise ValueError("TalentReef posting search omitted its hits list")
    total_value = raw_hits.get("total", 0)
    if isinstance(total_value, dict):
        total_value = total_value.get("value", 0)
    if not isinstance(total_value, int) or total_value < 0:
        raise ValueError("TalentReef posting search returned an invalid total")
    hits = [item for item in raw_hits["hits"] if isinstance(item, dict)]
    return hits, total_value


def _location(raw: Mapping[str, object]) -> list[str] | None:
    address = raw.get("address")
    if not isinstance(address, dict):
        return None
    parts: list[str] = []
    for key in ("street1", "city", "stateOrProvince", "postalCode", "country"):
        value = address.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return [", ".join(parts)] if parts else None


def _parse_job(
    raw_hit: Mapping[str, object],
    metadata: Mapping[str, object],
) -> DiscoveredJob | None:
    source = raw_hit.get("_source")
    if not isinstance(source, dict):
        return None
    job_id = str(source.get("jobId", "")).strip()
    title = source.get("positionType")
    if not job_id or not isinstance(title, str) or not title.strip():
        return None
    client_id = str(metadata["client_id"])
    locale = str(metadata["locale"])
    url = f"https://{_BOARD_HOST}/clients/{client_id}/posting/{job_id}/{locale}"
    return DiscoveredJob(
        url=url,
        title=title.strip(),
        description=source.get("description")
        if isinstance(source.get("description"), str)
        else None,
        locations=_location(source),
        employment_type=source.get("category") if isinstance(source.get("category"), str) else None,
        date_posted=source.get("createdDate")
        if isinstance(source.get("createdDate"), str)
        else None,
        language=locale.split("-", 1)[0],
        metadata={
            key: value
            for key, value in {
                "job_id": source.get("jobId"),
                "posting_uuid": source.get("postingUuid"),
                "brand": source.get("brand"),
                "client_name": source.get("clientName"),
            }.items()
            if value not in {None, ""}
        }
        or None,
        source_identity=f"talentreef:{client_id}:{job_id}",
    )


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch every external posting scoped to one TalentReef career page."""
    _ = pw
    configured = board.get("metadata") or {}
    identity = _identity_from_url(board["board_url"])
    alias = configured.get("alias") or (identity[0] if identity else None)
    locale = configured.get("locale") or (identity[1] if identity else "en")
    if not isinstance(alias, str) or not _ALIAS_RE.fullmatch(alias):
        raise ValueError("Cannot derive a valid TalentReef career-page alias")
    metadata = await _fetch_career_page(alias.lower(), str(locale).lower(), client)

    hits: list[dict] = []
    total = 0
    while len(hits) < MAX_JOBS:
        page, total = await _fetch_search_page(metadata, len(hits), client)
        hits.extend(page)
        if len(hits) >= total:
            break
        if not page:
            raise ValueError("TalentReef posting search stopped before its advertised total")

    jobs: list[DiscoveredJob] = []
    seen: set[str] = set()
    invalid = 0
    for hit in hits:
        job = _parse_job(hit, metadata)
        if job is None or job.url in seen:
            invalid += 1
            continue
        seen.add(job.url)
        jobs.append(job)
    if hits and not jobs:
        raise ValueError("TalentReef posting search returned no valid jobs")
    truncated = invalid > 0 or total > len(hits)
    if truncated:
        log.warning(
            "talentreef.truncated",
            alias=metadata["alias"],
            total=total,
            returned=len(jobs),
            invalid=invalid,
        )
        return truncated_rich_result(jobs)
    log.info("talentreef.discovered", alias=metadata["alias"], jobs=len(jobs))
    return jobs


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Detect and verify a TalentReef career page, including empty boards."""
    _ = pw
    identity = _identity_from_url(url)
    if identity is None:
        return None
    alias, locale = identity
    if client is None:
        return {"alias": alias, "locale": locale}
    try:
        metadata = await _fetch_career_page(alias, locale, client)
        _, total = await _fetch_search_page(metadata, 0, client)
    except Exception:
        return None
    return {**metadata, "jobs": total}


register("talentreef", discover, cost=10, can_handle=can_handle, rich=True)
