"""Strict identity helpers for SAP SuccessFactors career sites.

Modern Career Site Builder tenants expose ``/googlefeed.xml`` and continue to
use the existing RSS transport.  Older tenants share SAP origins and identify
the board with a case-sensitive ``company`` query value.  Keeping that legacy
identity here lets runtime discovery and the standalone workspace agree
without importing monitor code.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qsl, urlencode, urlparse

_LEGACY_HOST_RE = re.compile(
    r"(?:career\d{0,3}|performancemanager\d{1,3})\."
    r"(?:successfactors\.(?:com|eu)|sapsf\.(?:com|eu|cn))",
    re.IGNORECASE,
)
_COMPANY_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
_ALLOWED_QUERY_KEYS = frozenset(
    {
        "career_ns",
        "company",
        "lang",
        "navBarLevel",
        "rcm_site_locale",
        "site",
    }
)


def normalize_successfactors_company(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized if _COMPANY_RE.fullmatch(normalized) is not None else None


def is_successfactors_legacy_host(host: str) -> bool:
    return _LEGACY_HOST_RE.fullmatch(host.strip().rstrip(".")) is not None


def is_successfactors_host(host: str) -> bool:
    normalized = host.strip().rstrip(".").casefold()
    return is_successfactors_legacy_host(normalized) or normalized.endswith(
        (
            ".jobs.hr.cloud.sap",
            ".jobs2web.com",
            ".jobs2web.sapsf.cn",
        )
    )


@dataclass(frozen=True, slots=True)
class SuccessFactorsLegacyBoard:
    host: str
    company: str

    @property
    def listing_url(self) -> str:
        query = urlencode(
            {
                "company": self.company,
                "career_ns": "job_listing_summary",
                "navBarLevel": "JOB_SEARCH",
            }
        )
        return f"https://{self.host}/career?{query}"


def _safe_parts(url: str):
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlparse(unescape(url))
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").casefold().rstrip(".")
    if (
        parsed.scheme.casefold() != "https"
        or not is_successfactors_legacy_host(host)
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
        or parsed.path.rstrip("/").casefold() != "/career"
    ):
        return None
    return parsed, host


def successfactors_legacy_board_from_url(url: str) -> SuccessFactorsLegacyBoard | None:
    """Parse an unscoped legacy listing URL.

    Job-detail identifiers and unknown query parameters are rejected so a
    filtered or single-posting URL is never silently widened into a board.
    """

    safe = _safe_parts(url)
    if safe is None:
        return None
    parsed, host = safe
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=12)
    except ValueError:
        return None
    params: dict[str, str] = {}
    for name, value in pairs:
        if name in params or name not in _ALLOWED_QUERY_KEYS:
            return None
        params[name] = value
    company = normalize_successfactors_company(params.get("company"))
    if company is None:
        return None
    career_ns = params.get("career_ns")
    if career_ns not in {None, "", "job_listing_summary"}:
        return None
    nav_level = params.get("navBarLevel")
    if nav_level not in {None, "", "JOB_SEARCH"}:
        return None
    return SuccessFactorsLegacyBoard(host=host, company=company)


def successfactors_legacy_board_from_metadata(
    metadata: Mapping[str, object],
) -> SuccessFactorsLegacyBoard | None:
    host_value = metadata.get("host")
    company = normalize_successfactors_company(metadata.get("company"))
    host = host_value.strip().casefold().rstrip(".") if isinstance(host_value, str) else ""
    explicit = (
        SuccessFactorsLegacyBoard(host=host, company=company)
        if company is not None and is_successfactors_legacy_host(host)
        else None
    )
    has_explicit = "host" in metadata or "company" in metadata
    if has_explicit and explicit is None:
        return None

    listing_value = metadata.get("listing_url")
    if listing_value is None:
        return explicit
    if not isinstance(listing_value, str):
        return None
    listed = successfactors_legacy_board_from_url(listing_value)
    if listed is None or (explicit is not None and listed != explicit):
        return None
    return listed
