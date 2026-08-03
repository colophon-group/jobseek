"""Shared validation for Gupy public career-page identifiers and URLs."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlparse

_TENANT_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    re.IGNORECASE,
)
_RESERVED_TENANTS = frozenset(
    {
        "app",
        "developers",
        "login",
        "portal",
        "support",
        "www",
    }
)


def normalize_gupy_tenant(value: object) -> str | None:
    """Return a canonical public tenant or ``None`` for invalid/reserved values."""
    if not isinstance(value, str):
        return None
    tenant = value.strip().lower()
    if tenant in _RESERVED_TENANTS or _TENANT_RE.fullmatch(tenant) is None:
        return None
    return tenant


def _allowed_query(path: str, query: str) -> bool:
    if not query:
        return True
    return bool(
        re.fullmatch(r"/jobs/[1-9]\d{0,19}/?", path)
        and parse_qsl(query, keep_blank_values=True) == [("jobBoardSource", "gupy_public_page")]
    )


def gupy_tenant_from_url(url: str, *, validate_query: bool = True) -> str | None:
    """Extract a tenant only from canonical public Gupy listing/detail URLs."""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host.endswith(".gupy.io"):
        return None
    tenant = normalize_gupy_tenant(host.removesuffix(".gupy.io"))
    if (
        tenant is None
        or parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or re.fullmatch(r"(?:/jobs/[1-9]\d{0,19}/?)?/?", parsed.path) is None
        or (validate_query and not _allowed_query(parsed.path, parsed.query))
    ):
        return None
    return tenant
