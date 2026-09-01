"""Shared identity helpers for UKG Pro public recruiting boards."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse
from uuid import UUID

_HOST_RE = re.compile(
    r"^(?:recruiting(?:[2-9])?\.ultipro\.com|recruiting\.ultipro\.ca|"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.rec\.pro\.ukg\.net)$"
)
_TENANT_RE = re.compile(r"^[A-Za-z0-9]{3,64}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def normalize_ukg_host(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    host = value.strip().lower().rstrip(".")
    return host if _HOST_RE.fullmatch(host) is not None else None


def normalize_ukg_tenant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    tenant = value.strip()
    return tenant if _TENANT_RE.fullmatch(tenant) is not None else None


def normalize_ukg_uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if _UUID_RE.fullmatch(value) is None:
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError):
        return None
    return str(parsed)


@dataclass(frozen=True, slots=True)
class UKGBoard:
    host: str
    tenant: str
    board_id: str

    def listing_url(self) -> str:
        return f"https://{self.host}/{self.tenant}/JobBoard/{self.board_id}"

    def search_url(self) -> str:
        return f"{self.listing_url()}/JobBoardView/LoadSearchResults"

    def job_url(self, opportunity_id: object) -> str:
        normalized = normalize_ukg_uuid(opportunity_id)
        if normalized is None:
            raise ValueError(f"Invalid UKG opportunity ID: {opportunity_id!r}")
        return f"{self.listing_url()}/OpportunityDetail?opportunityId={normalized}"


def ukg_board_from_metadata(metadata: Mapping[str, object]) -> UKGBoard | None:
    listing_url = metadata.get("listing_url")
    if isinstance(listing_url, str):
        parsed = ukg_board_from_url(listing_url)
        if parsed is not None:
            return parsed

    host = normalize_ukg_host(metadata.get("host"))
    tenant = normalize_ukg_tenant(metadata.get("tenant"))
    board_id = normalize_ukg_uuid(metadata.get("board_id"))
    if host is None or tenant is None or board_id is None:
        return None
    return UKGBoard(host, tenant, board_id)


def ukg_board_from_url(url: str) -> UKGBoard | None:
    """Parse an unscoped UKG board listing or canonical detail URL."""
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
        query = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=1)
    except ValueError:
        return None
    host = normalize_ukg_host(parsed.hostname)
    if (
        parsed.scheme != "https"
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if (
        len(parts) not in {3, 4}
        or parts[1].casefold() != "jobboard"
        or (tenant := normalize_ukg_tenant(parts[0])) is None
        or (board_id := normalize_ukg_uuid(parts[2])) is None
    ):
        return None

    if len(parts) == 3:
        if query:
            return None
    elif (
        parts[3].casefold() != "opportunitydetail"
        or len(query) != 1
        or query[0][0].casefold() != "opportunityid"
        or normalize_ukg_uuid(query[0][1]) is None
    ):
        return None
    return UKGBoard(host, tenant, board_id)


def is_ukg_url(url: str) -> bool:
    return ukg_board_from_url(url) is not None
