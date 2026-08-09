"""Shared identity helpers for Recruiterbox / Trakstar Hire boards."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse

_TENANT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_JOB_TOKEN_RE = re.compile(r"^[a-z0-9]{3,64}$")
_TOTAL_JOBS_RE = re.compile(
    r"(?:\btotal_jobs|['\"]total_jobs['\"])\s*:\s*['\"]?([0-9]{1,9})",
    re.IGNORECASE,
)
_RESERVED_TENANTS = frozenset({"api", "app", "help", "static", "support", "www"})


def normalize_recruiterbox_tenant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    tenant = value.strip().lower()
    return (
        tenant
        if tenant not in _RESERVED_TENANTS and _TENANT_RE.fullmatch(tenant) is not None
        else None
    )


def normalize_recruiterbox_job_token(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    return token if _JOB_TOKEN_RE.fullmatch(token) is not None else None


@dataclass(frozen=True, slots=True)
class RecruiterboxBoard:
    tenant: str

    def listing_url(self) -> str:
        return f"https://{self.tenant}.hire.trakstar.com/"

    def page_url(self, page: int, *, page_size: int = 100) -> str:
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError(f"Invalid Recruiterbox page: {page!r}")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
            raise ValueError(f"Invalid Recruiterbox page size: {page_size!r}")
        return f"{self.listing_url()}?{urlencode({'limit': page_size, 'p': page})}"

    def job_url(self, token: str) -> str:
        normalized = normalize_recruiterbox_job_token(token)
        if normalized is None:
            raise ValueError(f"Invalid Recruiterbox job token: {token!r}")
        return f"https://{self.tenant}.hire.trakstar.com/jobs/{normalized}/"


def recruiterbox_board_from_metadata(
    metadata: Mapping[str, object],
) -> RecruiterboxBoard | None:
    tenant = normalize_recruiterbox_tenant(metadata.get("tenant"))
    return RecruiterboxBoard(tenant) if tenant is not None else None


def _tenant_from_host(host: str) -> str | None:
    suffixes = (".recruiterbox.com", ".hire.trakstar.com")
    for suffix in suffixes:
        if host.endswith(suffix):
            return normalize_recruiterbox_tenant(host[: -len(suffix)])
    return None


def recruiterbox_board_from_url(url: str) -> RecruiterboxBoard | None:
    """Parse an unfiltered listing, pagination, or job-detail URL."""
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=8)
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

    params: dict[str, str] = {}
    for name, value in pairs:
        if name in params:
            return None
        params[name] = value

    path = parsed.path.rstrip("/") or "/"
    if path in {"/", "/jobs"}:
        if not set(params).issubset({"limit", "p"}):
            return None
        if any(not value.isdigit() or int(value) < 1 for value in params.values()):
            return None
        return RecruiterboxBoard(tenant)

    match = re.fullmatch(r"/jobs/([a-z0-9]{3,64})", path, re.IGNORECASE)
    if match is None or not set(params).issubset({"source"}):
        return None
    if "source" in params and not params["source"]:
        return None
    return RecruiterboxBoard(tenant)


def recruiterbox_job_token(url: str, board: RecruiterboxBoard) -> str | None:
    parsed_board = recruiterbox_board_from_url(url)
    if parsed_board != board:
        return None
    try:
        path = urlparse(url).path.rstrip("/")
    except ValueError:
        return None
    match = re.fullmatch(r"/jobs/([a-z0-9]{3,64})", path, re.IGNORECASE)
    return normalize_recruiterbox_job_token(match.group(1)) if match else None


def recruiterbox_total_from_html(html: str) -> int | None:
    """Return the authoritative listing total embedded by active boards."""
    match = _TOTAL_JOBS_RE.search(html)
    return int(match.group(1)) if match is not None else None


def recruiterbox_inactive_from_html(html: str) -> bool:
    """Recognize Trakstar's branded HTTP-200 inactive-account tombstone."""
    folded = html.casefold()
    return (
        "recruiterbox.com/inactive-ats" in folded
        and "inactive account" in folded
        and "no longer using trakstar hire" in folded
    )
