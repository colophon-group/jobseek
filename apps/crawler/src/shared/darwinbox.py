"""Shared identity helpers for Darwinbox public career portals."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

_TENANT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_COMPANY_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,62}[A-Za-z0-9])?$")
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$")
_HOST_SUFFIXES = (".darwinbox.in", ".darwinbox.com")
_RESERVED_TENANTS = frozenset({"api", "app", "help", "static", "support", "www"})


def normalize_darwinbox_host(value: object) -> str | None:
    """Return a strict single-tenant Darwinbox hostname."""
    if not isinstance(value, str):
        return None
    host = value.strip().lower().rstrip(".")
    for suffix in _HOST_SUFFIXES:
        if not host.endswith(suffix):
            continue
        tenant = host[: -len(suffix)]
        if (
            tenant not in _RESERVED_TENANTS
            and "." not in tenant
            and _TENANT_RE.fullmatch(tenant) is not None
        ):
            return f"{tenant}{suffix}"
    return None


def normalize_darwinbox_company_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    company_id = value.strip()
    return company_id if _COMPANY_ID_RE.fullmatch(company_id) is not None else None


def normalize_darwinbox_job_id(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        value = str(value)
    if not isinstance(value, str):
        return None
    job_id = value.strip()
    return job_id if _JOB_ID_RE.fullmatch(job_id) is not None else None


@dataclass(frozen=True, slots=True)
class DarwinboxBoard:
    host: str
    company_id: str = "main"

    def listing_url(self) -> str:
        return f"https://{self.host}/ms/candidatev2/{self.company_id}/careers"

    def jobs_url(self) -> str:
        return f"https://{self.host}/ms/candidateapi/job/alljobs"

    def job_url(self, job_id: object) -> str:
        normalized = normalize_darwinbox_job_id(job_id)
        if normalized is None:
            raise ValueError(f"Invalid Darwinbox job ID: {job_id!r}")
        return f"{self.listing_url()}/jobDetails/{normalized}"


def darwinbox_board_from_metadata(metadata: Mapping[str, object]) -> DarwinboxBoard | None:
    host = normalize_darwinbox_host(metadata.get("host"))
    company_id = normalize_darwinbox_company_id(metadata.get("company_id", "main"))
    if host is None or company_id is None:
        return None
    return DarwinboxBoard(host, company_id)


def darwinbox_board_from_url(url: str) -> DarwinboxBoard | None:
    """Parse unscoped legacy/current Darwinbox listing and detail URLs."""
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = normalize_darwinbox_host(parsed.hostname)
    if (
        parsed.scheme != "https"
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        return None

    parts = [part for part in parsed.path.split("/") if part]
    if (len(parts) < 3 or [part.casefold() for part in parts[:2]] != ["ms", "candidate"]) and (
        len(parts) < 4
        or [part.casefold() for part in parts[:2]]
        != [
            "ms",
            "candidatev2",
        ]
    ):
        return None

    candidate_version = parts[1].casefold()
    tail = parts[2:]
    if candidate_version == "candidate":
        if tail and tail[0].casefold() == "careers":
            company_id = "main"
            tail = tail[1:]
        elif len(tail) >= 2 and tail[1].casefold() == "careers":
            company_id = normalize_darwinbox_company_id(tail[0]) or ""
            tail = tail[2:]
        else:
            return None
    else:
        if len(tail) < 2 or tail[1].casefold() != "careers":
            return None
        company_id = normalize_darwinbox_company_id(tail[0]) or ""
        tail = tail[2:]

    if not company_id:
        return None
    if tail and (
        len(tail) != 2
        or tail[0].casefold() != "jobdetails"
        or normalize_darwinbox_job_id(tail[1]) is None
    ):
        return None
    return DarwinboxBoard(host, company_id)
