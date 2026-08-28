"""Pure Jobvite public-career-site identity helpers.

Jobvite uses one shared host with several first-party listing routes.  Keep the
URL and page-identity rules here so crawler runtime, ``ws``, and lightweight
board probes agree without importing any upstream scraper implementation.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlparse

_HOST = "jobs.jobvite.com"
_TENANT_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,62})")
_JOB_ID_RE = re.compile(r"[A-Za-z0-9_-]{6,64}")
_PAGE_TENANT_RE = re.compile(
    r"\bcareersiteName\s*:\s*(['\"])([a-z0-9](?:[a-z0-9-]{0,62}))\1",
    re.IGNORECASE,
)
_LISTING_APP_MARKER = 'ng-app="jv.careersite.desktop.app"'
_RESERVED_TENANTS = frozenset({"api", "careers", "help", "support", "www"})


def _safe_parts(url: str):
    try:
        parsed = urlparse(unescape(url))
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold().rstrip(".") != _HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return parsed


def _tenant(value: str) -> str | None:
    normalized = value.casefold()
    if normalized in _RESERVED_TENANTS or _TENANT_RE.fullmatch(normalized) is None:
        return None
    return normalized


@dataclass(frozen=True, slots=True)
class JobviteBoard:
    """Stable tenant plus one validated public listing route."""

    tenant: str
    listing_path: str

    @property
    def listing_url(self) -> str:
        return f"https://{_HOST}{self.listing_path}"


def jobvite_board_from_url(url: str) -> JobviteBoard | None:
    """Parse a Jobvite listing or detail URL into its board identity."""
    parsed = _safe_parts(url)
    if parsed is None or parsed.fragment:
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if not segments:
        return None

    tenant: str | None = None
    listing_path: str | None = None
    lowered = [segment.casefold() for segment in segments]

    if len(segments) == 1:
        tenant = _tenant(segments[0])
        if tenant is not None and not parsed.query:
            listing_path = f"/{tenant}"
    elif len(segments) == 2 and lowered[0] == "careers":
        tenant = _tenant(segments[1])
        if tenant is not None and not parsed.query:
            listing_path = f"/careers/{tenant}"
    elif len(segments) == 2 and lowered[1] == "jobs":
        tenant = _tenant(segments[0])
        if tenant is not None:
            listing_path = f"/{tenant}/jobs"
    elif len(segments) == 3 and lowered[1:] == ["jobs", "positions"]:
        tenant = _tenant(segments[0])
        if tenant is not None:
            listing_path = f"/{tenant}/jobs/positions"
    elif len(segments) == 3 and lowered[0] == "careers" and lowered[2] == "jobs":
        tenant = _tenant(segments[1])
        if tenant is not None:
            listing_path = f"/careers/{tenant}/jobs"
    elif (
        len(segments) == 3 and lowered[1] == "job" and _JOB_ID_RE.fullmatch(segments[2]) is not None
    ):
        tenant = _tenant(segments[0])
        if tenant is not None:
            listing_path = f"/{tenant}"

    if tenant is None or listing_path is None:
        return None
    return JobviteBoard(tenant=tenant, listing_path=listing_path)


def jobvite_board_from_metadata(metadata: Mapping[str, object]) -> JobviteBoard | None:
    """Resolve a configured listing, checking an optional tenant assertion."""
    listing_url = metadata.get("listing_url")
    configured_tenant = metadata.get("tenant")
    has_tenant = "tenant" in metadata
    expected = _tenant(configured_tenant) if isinstance(configured_tenant, str) else None
    if has_tenant and expected is None:
        return None

    if "listing_url" in metadata:
        if not isinstance(listing_url, str):
            return None
        board = jobvite_board_from_url(listing_url)
        if board is None or (expected is not None and expected != board.tenant):
            return None
        return board
    if expected is not None:
        return JobviteBoard(expected, f"/{expected}")
    return None


def jobvite_page_tenant(body: str) -> str | None:
    """Return the first-party tenant marker from a Jobvite listing page."""
    if _LISTING_APP_MARKER not in body:
        return None
    match = _PAGE_TENANT_RE.search(body)
    return _tenant(match.group(2)) if match is not None else None


def jobvite_job_url(url: str, tenant: str) -> str | None:
    """Canonicalize one detail URL only when it belongs to ``tenant``."""
    parsed = _safe_parts(url)
    if parsed is None:
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    if (
        len(segments) != 3
        or segments[0].casefold() != tenant.casefold()
        or segments[1].casefold() != "job"
        or _JOB_ID_RE.fullmatch(segments[2]) is None
    ):
        return None
    return f"https://{_HOST}/{tenant.casefold()}/job/{segments[2]}"


def is_jobvite_invalid_redirect(location: str | None) -> bool:
    """Recognize Jobvite's explicit retired/unknown-tenant redirect."""
    if not location:
        return False
    parsed = urlparse(location)
    return (
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold().rstrip(".") == "www.jobvite.com"
        and parsed.path.rstrip("/") == "/support/job-seeker-support"
        and parsed.query == "invalid=1"
    )
