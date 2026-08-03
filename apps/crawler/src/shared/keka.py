"""Shared identity helpers for public Keka career portals."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urljoin, urlparse

_TENANT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PORTAL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62})?$")
_IDENTIFIER_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_DOCUMENT_RE = re.compile(
    r"fetch\(\s*['\"](/ats/documents/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})"
    r"/careerportal/[0-9a-f]{32}\.html)['\"]\s*\)",
    re.IGNORECASE,
)
_RESERVED_TENANTS = frozenset({"api", "app", "help", "static", "support", "www"})
_RESERVED_PORTALS = frozenset({"api", "content", "jobdetails"})


def normalize_keka_tenant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    tenant = value.strip().lower()
    return (
        tenant
        if tenant not in _RESERVED_TENANTS and _TENANT_RE.fullmatch(tenant) is not None
        else None
    )


def normalize_keka_portal(value: object) -> str | None:
    if value is None:
        return "default"
    if not isinstance(value, str):
        return None
    portal = value.strip().lower() or "default"
    if portal == "default":
        return portal
    return (
        portal
        if portal not in _RESERVED_PORTALS and _PORTAL_RE.fullmatch(portal) is not None
        else None
    )


def normalize_keka_identifier(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    identifier = value.strip().lower()
    return identifier if _IDENTIFIER_RE.fullmatch(identifier) is not None else None


@dataclass(frozen=True, slots=True)
class KekaBoard:
    tenant: str
    portal: str = "default"
    identifier: str | None = None

    def listing_url(self) -> str:
        suffix = "" if self.portal == "default" else f"/{self.portal}"
        return f"https://{self.tenant}.keka.com/careers{suffix}"

    def jobs_url(self) -> str:
        if self.identifier is None:
            raise ValueError(f"Keka tenant {self.tenant!r} is missing its identifier")
        return (
            f"https://{self.tenant}.keka.com/careers/api/embedjobs/"
            f"{self.portal}/active/{self.identifier}"
        )

    def job_url(self, job_id: int) -> str:
        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
            raise ValueError(f"Invalid Keka job ID: {job_id!r}")
        return f"{self.listing_url()}/jobdetails/{job_id}"

    def with_identifier(self, identifier: str) -> KekaBoard:
        normalized = normalize_keka_identifier(identifier)
        if normalized is None:
            raise ValueError(f"Invalid Keka identifier: {identifier!r}")
        return KekaBoard(self.tenant, self.portal, normalized)


def keka_board_from_metadata(metadata: Mapping[str, object]) -> KekaBoard | None:
    tenant = normalize_keka_tenant(metadata.get("tenant"))
    portal = normalize_keka_portal(metadata.get("portal"))
    raw_identifier = metadata.get("identifier")
    identifier = normalize_keka_identifier(raw_identifier) if raw_identifier is not None else None
    if tenant is None or portal is None or (raw_identifier is not None and identifier is None):
        return None
    return KekaBoard(tenant, portal, identifier)


def _tenant_from_host(host: str) -> str | None:
    suffix = ".keka.com"
    return normalize_keka_tenant(host[: -len(suffix)]) if host.endswith(suffix) else None


def keka_board_from_url(url: str) -> KekaBoard | None:
    """Parse a Keka listing or canonical job-detail URL."""
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=2)
    except ValueError:
        return None
    tenant = _tenant_from_host((parsed.hostname or "").lower())
    if (
        parsed.scheme != "https"
        or tenant is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    if len(query) != len(dict(query)) or any(key != "source" or not value for key, value in query):
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if not parts or parts[0].casefold() != "careers":
        return None
    tail = parts[1:]
    portal = "default"
    if len(tail) == 1:
        portal = normalize_keka_portal(tail[0]) or ""
    elif len(tail) == 2 and tail[0].casefold() == "jobdetails" and tail[1].isdigit():
        if int(tail[1]) < 1:
            return None
    elif (
        len(tail) == 3
        and tail[1].casefold() == "jobdetails"
        and tail[2].isdigit()
        and int(tail[2]) > 0
    ):
        portal = normalize_keka_portal(tail[0]) or ""
    elif tail:
        return None
    if not portal:
        return None
    return KekaBoard(tenant, portal)


def extract_keka_identifier(page: str) -> str | None:
    match = _DOCUMENT_RE.search(page)
    return normalize_keka_identifier(match.group(2)) if match is not None else None


def is_keka_forbidden_redirect(board: KekaBoard, location: str | None) -> bool:
    if not location:
        return False
    try:
        parsed = urlparse(urljoin(board.listing_url(), location))
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").casefold() == f"{board.tenant}.keka.com"
        and port in {None, 443}
        and parsed.username is None
        and parsed.password is None
        and parsed.path.casefold() == "/careers/content/403.html"
        and not parsed.query
        and not parsed.fragment
    )
