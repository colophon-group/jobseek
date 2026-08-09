"""Shared identity and bootstrap parsing for Dayforce public career sites."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from src.shared.nextdata import extract_next_data

_TENANT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
_PORTAL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,126}[A-Za-z0-9])?$")
_CULTURE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})+$")
_RESERVED_TENANTS = frozenset({"api", "app", "help", "support", "www"})


def normalize_dayforce_tenant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    tenant = value.strip().lower()
    if _TENANT_RE.fullmatch(tenant) is None or tenant in _RESERVED_TENANTS:
        return None
    return tenant


def normalize_dayforce_portal(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    portal = value.strip()
    return portal if _PORTAL_RE.fullmatch(portal) is not None else None


def normalize_dayforce_culture(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    culture = value.strip()
    return culture if len(culture) <= 32 and _CULTURE_RE.fullmatch(culture) is not None else None


def _positive_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if not value.isdigit():
            return None
        value = int(value)
    if not isinstance(value, int) or not 1 <= value <= 9_223_372_036_854_775_807:
        return None
    return value


@dataclass(frozen=True, slots=True)
class DayforceBoard:
    tenant: str
    portal: str

    def listing_url(self) -> str:
        return f"https://jobs.dayforcehcm.com/{self.tenant}/{self.portal}"

    def localized_listing_url(self, culture: str) -> str:
        normalized = normalize_dayforce_culture(culture)
        if normalized is None:
            raise ValueError(f"Invalid Dayforce culture: {culture!r}")
        return f"https://jobs.dayforcehcm.com/{normalized}/{self.tenant}/{self.portal}"

    def search_url(self) -> str:
        return f"https://jobs.dayforcehcm.com/api/geo/{self.tenant}/jobposting/search"

    def job_url(self, culture: str, job_posting_id: int) -> str:
        return (
            f"https://jobs.dayforcehcm.com/{culture}/{self.tenant}/"
            f"{self.portal}/jobs/{job_posting_id}"
        )


def dayforce_board_from_metadata(metadata: Mapping[str, object]) -> DayforceBoard | None:
    tenant = normalize_dayforce_tenant(metadata.get("tenant"))
    portal = normalize_dayforce_portal(metadata.get("portal"))
    if tenant is None or portal is None:
        return None
    return DayforceBoard(tenant=tenant, portal=portal)


def _path_segments(path: str) -> list[str] | None:
    if not path.startswith("/"):
        return None
    body = path[1:-1] if path.endswith("/") else path[1:]
    if not body:
        return []
    segments = body.split("/")
    return None if any(not segment for segment in segments) else segments


def dayforce_board_from_url(
    url: str,
    *,
    validate_query: bool = True,
) -> DayforceBoard | None:
    """Parse canonical public Dayforce listing and detail URLs only."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower().rstrip(".") != "jobs.dayforcehcm.com"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or (validate_query and parsed.query)
        or parsed.fragment
    ):
        return None

    segments = _path_segments(parsed.path)
    if segments is None:
        return None
    if len(segments) == 2:
        tenant_raw, portal_raw = segments
    elif len(segments) == 3 and normalize_dayforce_culture(segments[0]) is not None:
        _culture, tenant_raw, portal_raw = segments
    elif (
        len(segments) == 5
        and normalize_dayforce_culture(segments[0]) is not None
        and segments[3].lower() == "jobs"
        and _positive_id(segments[4]) is not None
    ):
        _culture, tenant_raw, portal_raw, _jobs, _job_id = segments
    else:
        return None

    tenant = normalize_dayforce_tenant(tenant_raw)
    portal = normalize_dayforce_portal(portal_raw)
    if tenant is None or portal is None:
        return None
    return DayforceBoard(tenant=tenant, portal=portal)


def dayforce_listing_culture_from_url(url: str) -> str | None:
    """Return the culture only for a canonical locale-prefixed listing URL."""
    if dayforce_board_from_url(url) is None:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    segments = _path_segments(parsed.path)
    if segments is None or len(segments) != 3:
        return None
    return normalize_dayforce_culture(segments[0])


def resolve_dayforce_listing_redirect(
    board: DayforceBoard,
    source_url: str,
    location: str | None,
) -> str | None:
    """Resolve one trusted redirect to the same board's localized listing."""
    if not isinstance(location, str) or not location:
        return None
    target = urljoin(source_url, location)
    redirected = dayforce_board_from_url(target)
    if redirected != board or dayforce_listing_culture_from_url(target) is None:
        return None
    return target


@dataclass(frozen=True, slots=True)
class DayforceSite:
    job_board_id: int
    culture: str
    cultures: tuple[str, ...]
    disabled: bool


def extract_dayforce_site(page: str, board: DayforceBoard) -> DayforceSite:
    """Extract and validate Dayforce's server-rendered ``site-info`` query."""
    data = extract_next_data(page)
    if not isinstance(data, dict):
        raise ValueError("Dayforce listing omitted valid __NEXT_DATA__")

    query = data.get("query")
    if not isinstance(query, dict):
        raise ValueError("Dayforce listing omitted its board query identity")
    query_tenant = normalize_dayforce_tenant(query.get("clientNamespace"))
    query_portal = normalize_dayforce_portal(query.get("careerSiteXRefCode"))
    if (
        query_tenant != board.tenant
        or query_portal is None
        or query_portal.casefold() != board.portal.casefold()
    ):
        raise ValueError("Dayforce listing bootstrap does not match the configured board")

    props = data.get("props")
    page_props = props.get("pageProps") if isinstance(props, dict) else None
    dehydrated = page_props.get("dehydratedState") if isinstance(page_props, dict) else None
    queries = dehydrated.get("queries") if isinstance(dehydrated, dict) else None
    if not isinstance(queries, list):
        raise ValueError("Dayforce listing omitted its dehydrated site queries")

    candidates: list[dict] = []
    for entry in queries:
        if not isinstance(entry, dict):
            continue
        key = entry.get("queryKey")
        state = entry.get("state")
        site = state.get("data") if isinstance(state, dict) else None
        if isinstance(key, list) and key[:1] == ["site-info"] and isinstance(site, dict):
            candidates.append(site)

    matching: list[dict] = []
    for site in candidates:
        tenant = normalize_dayforce_tenant(site.get("clientNamespace"))
        portal = normalize_dayforce_portal(site.get("jobBoardCode"))
        if tenant == board.tenant and portal and portal.casefold() == board.portal.casefold():
            matching.append(site)
    if len(matching) != 1:
        raise ValueError("Dayforce listing omitted a unique matching site-info record")

    site = matching[0]
    job_board_id = _positive_id(site.get("jobBoardId"))
    culture = normalize_dayforce_culture(site.get("cultureCode"))
    raw_cultures = site.get("isoCultureCodes")
    disabled = site.get("isDisabled")
    if job_board_id is None:
        raise ValueError("Dayforce listing returned an invalid job-board ID")
    if culture is None:
        raise ValueError("Dayforce listing returned an invalid culture")
    if not isinstance(raw_cultures, list) or not raw_cultures:
        raise ValueError("Dayforce listing omitted its supported cultures")
    cultures = tuple(filter(None, (normalize_dayforce_culture(value) for value in raw_cultures)))
    if len(cultures) != len(raw_cultures) or len(set(map(str.casefold, cultures))) != len(cultures):
        raise ValueError("Dayforce listing returned invalid supported cultures")
    if culture.casefold() not in {value.casefold() for value in cultures}:
        raise ValueError("Dayforce listing culture is not supported by the board")
    if disabled is not None and not isinstance(disabled, bool):
        raise ValueError("Dayforce listing returned an invalid disabled state")

    return DayforceSite(
        job_board_id=job_board_id,
        culture=culture,
        cultures=cultures,
        disabled=disabled is True,
    )
