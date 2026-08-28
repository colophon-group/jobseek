"""Shared parsing and trust boundaries for Cornerstone public career sites."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, urlparse

_TENANT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
_CORP_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,126}[a-z0-9])?$", re.IGNORECASE)
_CULTURE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
_CONTEXT_MARKER_RE = re.compile(r"\bcsod\.context\s*=\s*")
_RESERVED_TENANTS = frozenset({"api", "app", "help", "login", "portal", "support", "www"})
_TRUSTED_DOMAINS = frozenset({"csod.com", "csodfed.com"})


class CornerstoneContextMissingError(ValueError):
    """An otherwise accepted listing page omitted its transient bootstrap."""


def _normalize_token(value: object, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    return token if pattern.fullmatch(token) else None


def normalize_cornerstone_tenant(value: object) -> str | None:
    tenant = _normalize_token(value, _TENANT_RE)
    return None if tenant in _RESERVED_TENANTS else tenant


def normalize_cornerstone_corp(value: object) -> str | None:
    return _normalize_token(value, _CORP_RE)


def normalize_cornerstone_domain(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    domain = value.strip().lower().rstrip(".")
    return domain if domain in _TRUSTED_DOMAINS else None


def normalize_cornerstone_site_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        if not value.isdigit():
            return None
        value = int(value)
    if not isinstance(value, int) or not 1 <= value <= 2_147_483_647:
        return None
    return value


@dataclass(frozen=True, slots=True)
class CornerstoneBoard:
    tenant: str
    site_id: int
    corp: str
    domain: str = "csod.com"

    @property
    def host(self) -> str:
        return f"{self.tenant}.{self.domain}"

    def listing_url(self) -> str:
        return (
            f"https://{self.host}/ux/ats/careersite/{self.site_id}/home"
            f"?c={quote(self.corp, safe='')}"
        )

    def job_url(self, requisition_id: int) -> str:
        return (
            f"https://{self.host}/ux/ats/careersite/{self.site_id}/home/"
            f"requisition/{requisition_id}?c={quote(self.corp, safe='')}"
        )


def cornerstone_board_from_metadata(metadata: Mapping[str, object]) -> CornerstoneBoard | None:
    tenant = normalize_cornerstone_tenant(metadata.get("tenant"))
    site_id = normalize_cornerstone_site_id(metadata.get("site_id"))
    corp = normalize_cornerstone_corp(metadata.get("corp"))
    domain = normalize_cornerstone_domain(metadata.get("domain", "csod.com"))
    if tenant is None or site_id is None or corp is None or domain is None:
        return None
    return CornerstoneBoard(tenant=tenant, site_id=site_id, corp=corp, domain=domain)


def cornerstone_board_from_url(
    url: str,
    *,
    validate_query: bool = True,
) -> CornerstoneBoard | None:
    """Parse only canonical public Cornerstone listing/detail URLs."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    domain = next(
        (candidate for candidate in _TRUSTED_DOMAINS if host.endswith(f".{candidate}")),
        None,
    )
    if (
        parsed.scheme != "https"
        or domain is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None

    tenant = normalize_cornerstone_tenant(host.removesuffix(f".{domain}"))
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) not in {5, 7} or [part.lower() for part in segments[:3]] != [
        "ux",
        "ats",
        "careersite",
    ]:
        return None
    site_id = normalize_cornerstone_site_id(segments[3])
    if segments[4].lower() != "home":
        return None
    if len(segments) == 7 and (
        segments[5].lower() != "requisition"
        or not segments[6].isdigit()
        or not 1 <= len(segments[6]) <= 20
        or int(segments[6]) <= 0
    ):
        return None

    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    corp_values = [value for key, value in pairs if key == "c"]
    corp = normalize_cornerstone_corp(corp_values[0]) if len(corp_values) == 1 else None
    if (
        tenant is None
        or site_id is None
        or corp is None
        or (validate_query and pairs != [("c", corp_values[0])])
    ):
        return None
    return CornerstoneBoard(tenant=tenant, site_id=site_id, corp=corp, domain=domain)


def _trusted_api_base(value: object, *, domain: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Cornerstone bootstrap omitted its API origin")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Cornerstone bootstrap returned an invalid API origin") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    trusted_api_host = host == f"api.{domain}" or host.endswith(f".api.{domain}")
    if (
        parsed.scheme != "https"
        or not trusted_api_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Cornerstone bootstrap returned an untrusted API origin")
    return f"https://{host}/"


@dataclass(frozen=True, slots=True, repr=False)
class CornerstoneContext:
    api_base: str
    token: str
    culture_id: int
    culture_name: str

    @property
    def search_url(self) -> str:
        return f"{self.api_base}rec-job-search/external/jobs"

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "CSOD-Accept-Language": self.culture_name,
        }


def extract_cornerstone_context(page: str, board: CornerstoneBoard) -> CornerstoneContext:
    """Parse and validate the short-lived public context embedded in a listing."""
    marker = _CONTEXT_MARKER_RE.search(page)
    if marker is None:
        raise CornerstoneContextMissingError("Cornerstone listing omitted csod.context")
    try:
        raw, _end = json.JSONDecoder().raw_decode(page, marker.end())
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Cornerstone listing returned invalid context JSON") from exc
    if not isinstance(raw, dict):
        raise ValueError("Cornerstone listing context is not an object")

    corp = normalize_cornerstone_corp(raw.get("corp"))
    if corp != board.corp:
        raise ValueError("Cornerstone listing context does not match the configured corporation")
    token = raw.get("token")
    if not isinstance(token, str) or not 64 <= len(token) <= 20_000 or len(token.split(".")) != 3:
        raise ValueError("Cornerstone listing returned a malformed session token")
    culture_id = raw.get("cultureID")
    if isinstance(culture_id, bool) or not isinstance(culture_id, int) or culture_id <= 0:
        raise ValueError("Cornerstone listing returned an invalid culture ID")
    culture_name = raw.get("cultureName")
    if not isinstance(culture_name, str) or _CULTURE_RE.fullmatch(culture_name) is None:
        raise ValueError("Cornerstone listing returned an invalid culture name")
    endpoints = raw.get("endpoints")
    if not isinstance(endpoints, dict):
        raise ValueError("Cornerstone listing omitted endpoint metadata")

    return CornerstoneContext(
        api_base=_trusted_api_base(endpoints.get("cloud"), domain=board.domain),
        token=token,
        culture_id=culture_id,
        culture_name=culture_name,
    )
