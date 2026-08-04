"""Shared identity helpers for Beisen recruitment portals."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse

_TENANT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_PORTAL_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_JOB_ID_RE = re.compile(r"^[1-9]\d{0,18}$")
_RESERVED_TENANTS = frozenset({"api", "help", "static", "support", "www"})
_VARIANTS = frozenset({"modern", "legacy"})
_LEGACY_TEMPLATES = frozenset({"standard", "inline"})
_BOOTSTRAP_MARKER = "var BSGlobal = "


def normalize_beisen_tenant(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    tenant = value.strip().lower()
    return (
        tenant
        if tenant not in _RESERVED_TENANTS and _TENANT_RE.fullmatch(tenant) is not None
        else None
    )


def normalize_beisen_portal_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    portal_id = value.strip().lower()
    return portal_id if _PORTAL_ID_RE.fullmatch(portal_id) is not None else None


def normalize_beisen_job_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    job_id = str(value).strip() if isinstance(value, (int, str)) else ""
    return job_id if _JOB_ID_RE.fullmatch(job_id) is not None else None


def _normalize_listing_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    path = "/" + value.strip().strip("/")
    if path.casefold() == "/social":
        return "/Social"
    if path.casefold() == "/index":
        return "/index"
    return None


@dataclass(frozen=True, slots=True)
class BeisenBoard:
    tenant: str
    variant: str | None = None
    portal_id: str | None = None
    tenant_id: int | None = None
    listing_path: str | None = None
    legacy_template: str | None = None

    def root_url(self) -> str:
        return f"https://{self.tenant}.zhiye.com/"

    def listing_url(self) -> str:
        if self.variant == "modern":
            return f"https://{self.tenant}.zhiye.com/jobs"
        path = self.listing_path or "/Social"
        return f"https://{self.tenant}.zhiye.com{path}"

    def api_url(self) -> str:
        return f"https://{self.tenant}.zhiye.com/api/Jobad/GetJobAdPageList"

    def modern_job_url(self, public_id: str, category_id: str) -> str:
        portal_id = normalize_beisen_portal_id(public_id)
        if portal_id is None:
            raise ValueError(f"Invalid Beisen public job ID: {public_id!r}")
        category = category_id.strip() if isinstance(category_id, str) else ""
        route = {"1": "social", "2": "campus", "3": "intern"}.get(category)
        if route is None:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", category):
                raise ValueError(f"Invalid Beisen job category: {category_id!r}")
            route = category
        return f"https://{self.tenant}.zhiye.com/{route}/detail?jobAdId={portal_id}"

    def legacy_job_url(self, job_id: object) -> str:
        normalized = normalize_beisen_job_id(job_id)
        if normalized is None:
            raise ValueError(f"Invalid Beisen legacy job ID: {job_id!r}")
        if self.legacy_template == "inline":
            return f"https://{self.tenant}.zhiye.com/zwxq?jobId={normalized}"
        return f"https://{self.tenant}.zhiye.com/zpdetail/{normalized}"


def extract_beisen_bootstrap(page: str, tenant: str) -> tuple[BeisenBoard, bool] | None:
    """Parse the public modern-portal identity and enabled state."""
    marker = page.find(_BOOTSTRAP_MARKER)
    if marker < 0:
        return None
    start = marker + len(_BOOTSTRAP_MARKER)
    try:
        payload, _end = json.JSONDecoder().raw_decode(page, start)
    except (json.JSONDecodeError, TypeError):
        raise ValueError(f"Beisen tenant {tenant!r} exposed malformed bootstrap JSON") from None
    if not isinstance(payload, dict):
        raise ValueError(f"Beisen tenant {tenant!r} exposed a non-object bootstrap")
    portal_id = normalize_beisen_portal_id(payload.get("PortalId"))
    tenant_info = payload.get("tenantInfo")
    if portal_id is None or not isinstance(tenant_info, dict):
        raise ValueError(f"Beisen tenant {tenant!r} bootstrap omitted portal identity")
    tenant_id = tenant_info.get("Id")
    if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id < 1:
        raise ValueError(f"Beisen tenant {tenant!r} bootstrap omitted tenant identity")
    status = tenant_info.get("Status")
    if isinstance(status, bool) or not isinstance(status, int):
        raise ValueError(f"Beisen tenant {tenant!r} bootstrap omitted portal status")
    return (
        BeisenBoard(
            tenant=tenant,
            variant="modern",
            portal_id=portal_id,
            tenant_id=tenant_id,
        ),
        status == 1,
    )


def beisen_tenant_from_url(url: str) -> str | None:
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlparse(url)
        port = parsed.port
        _ = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=16)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    suffix = ".zhiye.com"
    tenant = normalize_beisen_tenant(host[: -len(suffix)]) if host.endswith(suffix) else None
    if (
        parsed.scheme != "https"
        or tenant is None
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        return None
    return tenant


def beisen_board_from_url(url: str) -> BeisenBoard | None:
    tenant = beisen_tenant_from_url(url)
    return BeisenBoard(tenant) if tenant is not None else None


def beisen_board_from_metadata(metadata: Mapping[str, object]) -> BeisenBoard | None:
    tenant = normalize_beisen_tenant(metadata.get("tenant"))
    variant = metadata.get("variant")
    if tenant is None or not isinstance(variant, str) or variant not in _VARIANTS:
        return None
    if variant == "modern":
        portal_id = normalize_beisen_portal_id(metadata.get("portal_id"))
        tenant_id = metadata.get("tenant_id")
        if (
            portal_id is None
            or isinstance(tenant_id, bool)
            or not isinstance(tenant_id, int)
            or tenant_id < 1
        ):
            return None
        return BeisenBoard(
            tenant=tenant,
            variant=variant,
            portal_id=portal_id,
            tenant_id=tenant_id,
        )

    listing_path = _normalize_listing_path(metadata.get("listing_path"))
    legacy_template = metadata.get("legacy_template")
    if (
        listing_path is None
        or not isinstance(legacy_template, str)
        or legacy_template not in _LEGACY_TEMPLATES
    ):
        return None
    if (listing_path == "/index") != (legacy_template == "inline"):
        return None
    return BeisenBoard(
        tenant=tenant,
        variant=variant,
        listing_path=listing_path,
        legacy_template=legacy_template,
    )
