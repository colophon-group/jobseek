"""Pure JazzHR tenant identity helpers shared by runtime and probes."""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlparse

_TENANT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_DIRECT_PATH_RE = re.compile(
    r"^(?:/apply(?:/jobs(?:/details/[A-Za-z0-9_-]+)?)?)?/?$",
    re.IGNORECASE,
)
_RESERVED_TENANTS = frozenset({"app", "developers", "login", "portal", "support", "www"})


def normalize_jazzhr_tenant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    tenant = value.strip().lower()
    if tenant in _RESERVED_TENANTS:
        return None
    return tenant if _TENANT_RE.fullmatch(tenant) else None


def jazzhr_tenant_from_url(url: str) -> str | None:
    """Return the tenant from a strict first-party listing or detail URL."""

    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    host = (parsed.hostname or "").lower()
    suffix = ".applytojob.com"
    if (
        parsed.scheme != "https"
        or not host.endswith(suffix)
        or host.count(".") != 2
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or _DIRECT_PATH_RE.fullmatch(parsed.path) is None
    ):
        return None
    return normalize_jazzhr_tenant(host.removesuffix(suffix))


def resolve_jazzhr_tenant(
    board_url: str,
    metadata: Mapping[str, object],
) -> str | None:
    """Resolve one tenant and reject contradictory configured identity."""

    direct = jazzhr_tenant_from_url(board_url)
    has_configured = "tenant" in metadata
    configured = normalize_jazzhr_tenant(metadata.get("tenant"))
    if has_configured and configured is None:
        raise ValueError("Configured JazzHR tenant is invalid")
    if direct is not None and configured is not None and direct != configured:
        raise ValueError(
            f"Configured JazzHR tenant {configured!r} does not match "
            f"the board URL tenant {direct!r}"
        )
    return direct or configured


def jazzhr_listing_url(tenant: str) -> str:
    return f"https://{tenant}.applytojob.com/apply/jobs"
