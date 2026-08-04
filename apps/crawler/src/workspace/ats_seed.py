"""Validated ``ats-inventory`` issue seeds for the ``ws`` fast path.

The upstream repository is inventory evidence only.  This module accepts the
machine-readable marker emitted by Jobseek's queue and turns it into a native
Jobseek monitor selection after cross-checking every readable field against
the local compatibility registry.  A missing or invalid seed never changes
the ordinary human-request workflow.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from src.ats_inventory.candidates import (
    Candidate,
    DedupEvidence,
    LocalRegistryIndex,
    candidate_marker,
    candidate_tenant_key,
    hash_text,
    normalize_board_url,
    parse_candidate_markers,
)
from src.ats_inventory.compat import compatibility_for
from src.ats_inventory.constants import ATS_INVENTORY_LABEL
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.state import Board, Workspace

INVENTORY_BOARD_ALIAS = "careers"
INVENTORY_CONFIG_NAME = "inventory-seed"
_MARKER_PREFIX = "<!-- ats-inventory-candidate:v1"
_FAMILY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_FIELD_RE = re.compile(
    r"^- (?P<label>Source key|Upstream inventory family|Jobseek-native ATS identity|"
    r"Exact tenant|Normalized board URL): `(?P<value>[^`\n]+)`\s*$",
    re.MULTILINE,
)
_ACTIVE_JOBS_RE = re.compile(r"^- Published active jobs: (?P<value>unknown|\d+)\s*$", re.MULTILINE)


class InventorySeedInvalid(ValueError):
    """The issue claims an inventory seed, but its evidence is inconsistent."""


@dataclass(frozen=True, slots=True)
class AtsInventorySeed:
    source_key: str
    family: str
    native_ats: str
    tenant: str
    board_url: str
    board_sha256: str
    monitor_type: str
    monitor_config: dict[str, Any]
    marker: str
    published_active_jobs: int | None = None

    def to_workspace_state(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "family": self.family,
            "native_ats": self.native_ats,
            "tenant": self.tenant,
            "board_url": self.board_url,
            "board_sha256": self.board_sha256,
            "monitor_type": self.monitor_type,
            "monitor_config": copy.deepcopy(self.monitor_config),
            "marker": self.marker,
            "published_active_jobs": self.published_active_jobs,
            "board_alias": INVENTORY_BOARD_ALIAS,
            "config_name": INVENTORY_CONFIG_NAME,
            "status": "pending",
        }


def parse_inventory_seed(body: str) -> AtsInventorySeed | None:
    """Parse and authenticate the inventory block in a company issue.

    ``None`` means the issue is an ordinary human request.  If the marker is
    present but malformed or its redundant evidence disagrees, the caller is
    given :class:`InventorySeedInvalid` and must use normal discovery.
    """

    body = body or ""
    markers = parse_candidate_markers(body)
    if not markers:
        if _MARKER_PREFIX in body:
            raise InventorySeedInvalid("the inventory candidate marker is malformed")
        return None
    if len(markers) != 1:
        raise InventorySeedInvalid("the issue must contain exactly one inventory candidate marker")

    fields: dict[str, str] = {}
    for match in _FIELD_RE.finditer(body):
        label = match.group("label")
        if label in fields:
            raise InventorySeedInvalid(f"duplicate inventory field: {label}")
        fields[label] = match.group("value").strip()
    required = {
        "Source key",
        "Upstream inventory family",
        "Jobseek-native ATS identity",
        "Exact tenant",
        "Normalized board URL",
    }
    missing = sorted(required - fields.keys())
    if missing:
        raise InventorySeedInvalid(f"missing inventory field(s): {', '.join(missing)}")

    marker_source, marker_board_hash = markers[0]
    source_key = fields["Source key"]
    family = fields["Upstream inventory family"]
    native_ats = fields["Jobseek-native ATS identity"]
    tenant = fields["Exact tenant"]
    raw_board_url = fields["Normalized board URL"]

    if source_key != marker_source:
        raise InventorySeedInvalid("the readable source key does not match the marker")
    if not _FAMILY_RE.fullmatch(family):
        raise InventorySeedInvalid("the upstream family is invalid")

    compatibility = compatibility_for(family)
    if (
        compatibility is None
        or not compatibility.candidate_eligible
        or not compatibility.seedable
        or compatibility.monitor_type is None
    ):
        raise InventorySeedInvalid(f"ATS family {family!r} is not seedable")

    expected_native = (
        f"native:{compatibility.monitor_type}"
        if compatibility.kind == "native"
        else f"family:{family}"
    )
    if native_ats != expected_native:
        raise InventorySeedInvalid("the native ATS identity does not match local compatibility")

    try:
        board_url = normalize_board_url(raw_board_url)
    except ValueError as exc:
        raise InventorySeedInvalid(f"the board URL is invalid: {exc}") from exc
    if board_url != raw_board_url:
        raise InventorySeedInvalid("the board URL is not in canonical normalized form")
    if hash_text(board_url) != marker_board_hash:
        raise InventorySeedInvalid("the board URL does not match the marker hash")

    monitor_config = compatibility.monitor_config or {}
    expected_tenant = candidate_tenant_key(family, board_url, config=monitor_config)
    if expected_tenant is None or len(expected_tenant) > 240:
        expected_tenant = f"url-sha256:{hash_text(board_url)}"
    if tenant != expected_tenant:
        raise InventorySeedInvalid("the exact tenant does not match the family and board URL")
    source_component = quote(tenant, safe="")
    if len(source_component) > 400:
        source_component = f"tenant-sha256-{hash_text(tenant)}"
    expected_source = f"ats-scrapers:{family}:{source_component}"
    if source_key != expected_source:
        raise InventorySeedInvalid("the source key does not match the family and exact tenant")

    # Reject an obvious family/URL mismatch even if someone rewrote every
    # readable field and marker consistently.  Unknown first-party hosts are
    # allowed because many native monitors intentionally support custom hosts.
    detected_monitor = detect_ats_from_url(board_url)
    if detected_monitor is not None and detected_monitor != compatibility.monitor_type:
        raise InventorySeedInvalid(
            f"the board URL identifies monitor {detected_monitor!r}, not "
            f"{compatibility.monitor_type!r}"
        )

    # Fail closed if the compatibility registry got ahead of the runtime.
    from src.core.monitors import get_discoverer

    try:
        get_discoverer(compatibility.monitor_type)
    except ValueError as exc:
        raise InventorySeedInvalid(
            f"native monitor {compatibility.monitor_type!r} is unavailable"
        ) from exc

    active_jobs_match = _ACTIVE_JOBS_RE.search(body)
    published_active_jobs = None
    if active_jobs_match and active_jobs_match.group("value") != "unknown":
        published_active_jobs = int(active_jobs_match.group("value"))

    return AtsInventorySeed(
        source_key=source_key,
        family=family,
        native_ats=native_ats,
        tenant=tenant,
        board_url=board_url,
        board_sha256=marker_board_hash,
        monitor_type=compatibility.monitor_type,
        monitor_config=copy.deepcopy(monitor_config),
        marker=candidate_marker(source_key, board_url),
        published_active_jobs=published_active_jobs,
    )


def issue_has_inventory_label(issue: Mapping[str, Any]) -> bool:
    """Return whether GitHub attached the protected inventory source label."""

    labels = issue.get("labels")
    if not isinstance(labels, list):
        return False
    for label in labels:
        if isinstance(label, str) and label == ATS_INVENTORY_LABEL:
            return True
        if isinstance(label, Mapping) and label.get("name") == ATS_INVENTORY_LABEL:
            return True
    return False


def current_registry_hard_evidence(
    seed: AtsInventorySeed,
    *,
    companies_path: Path,
    boards_path: Path,
) -> tuple[DedupEvidence, ...]:
    """Recheck durable URL/tenant identities immediately before seeding."""

    index = LocalRegistryIndex.from_csv(companies_path, boards_path)
    candidate = Candidate(
        family=seed.family,
        native_ats=seed.native_ats,
        tenant=seed.tenant,
        source_key=seed.source_key,
        name="",
        slug="",
        board_url=seed.board_url,
        impact_unknown=True,
        active_jobs=seed.published_active_jobs or 0,
        remote_jobs=0,
        location_count=0,
        country_codes=(),
        latest_posted_at=None,
    )
    return tuple(index.hard_evidence(candidate))


def apply_inventory_seed(ws: Workspace, seed: AtsInventorySeed) -> Board:
    """Attach a selected native config and provenance to a new workspace."""

    config: dict[str, Any] = {
        "monitor_type": seed.monitor_type,
        "monitor_config": copy.deepcopy(seed.monitor_config),
        "status": "selected",
        "cost": {},
        "run": {},
        "feedback": None,
        "inventory_source_key": seed.source_key,
    }
    auto_scraper = auto_scraper_type(seed.monitor_type, seed.monitor_config)
    if auto_scraper is not None:
        config["scraper_type"] = auto_scraper[0]
        if auto_scraper[1]:
            config["scraper_config"] = copy.deepcopy(auto_scraper[1])

    board = Board(
        alias=INVENTORY_BOARD_ALIAS,
        slug=f"{ws.slug}-{INVENTORY_BOARD_ALIAS}",
        url=seed.board_url,
        active_config=INVENTORY_CONFIG_NAME,
        configs={INVENTORY_CONFIG_NAME: config},
    )
    ws.active_board = INVENTORY_BOARD_ALIAS
    ws.ats_inventory = seed.to_workspace_state()
    return board


def apply_inventory_fallback(ws: Workspace, seed: AtsInventorySeed, reason: str) -> None:
    """Preserve candidate provenance without preconfiguring a stale seed."""

    ws.ats_inventory = seed.to_workspace_state()
    ws.ats_inventory["status"] = "fallback"
    ws.ats_inventory["reason"] = reason[:500]


def inventory_seed_matches_run(
    ws: Workspace,
    board: Board,
    config_name: str | None,
    monitor_type: str,
    monitor_config: dict[str, Any],
) -> bool:
    """Return whether this invocation is testing the untouched seed config."""

    state = ws.ats_inventory
    if not state:
        return False
    selected_name = config_name or board.active_config
    return bool(
        board.alias == state.get("board_alias")
        and board.url == state.get("board_url")
        and selected_name == state.get("config_name")
        and monitor_type == state.get("monitor_type")
        and monitor_config == (state.get("monitor_config") or {})
    )


def set_inventory_seed_status(
    ws: Workspace,
    status: str,
    *,
    jobs: int | None = None,
    reason: str | None = None,
) -> None:
    """Record whether the fast path was verified or must fall back."""

    if not ws.ats_inventory:
        return
    ws.ats_inventory["status"] = status
    if jobs is not None:
        ws.ats_inventory["jobs"] = jobs
    if reason:
        ws.ats_inventory["reason"] = reason[:500]
    else:
        ws.ats_inventory.pop("reason", None)


def preverify_inventory_context(body: str) -> str:
    """Render concise pre-verification guidance without mutating state."""

    try:
        seed = parse_inventory_seed(body)
    except InventorySeedInvalid as exc:
        return (
            "## Inventory seed validation\n\n"
            f"The issue's inventory seed is invalid ({exc}). Do not trust or preconfigure it; "
            "use the normal board-discovery workflow.\n"
        )
    if seed is None:
        return ""
    return (
        "## Validated inventory seed\n\n"
        f"`ws new` will preselect Jobseek's `{seed.monitor_type}` monitor for "
        f"`{seed.board_url}` (source `{seed.source_key}`). This is only a fast start: still "
        "verify the company/tenant, check duplicates, research every official global or "
        "regional board, and complete all normal quality gates.\n"
    )


__all__ = [
    "AtsInventorySeed",
    "INVENTORY_BOARD_ALIAS",
    "INVENTORY_CONFIG_NAME",
    "InventorySeedInvalid",
    "apply_inventory_seed",
    "apply_inventory_fallback",
    "current_registry_hard_evidence",
    "inventory_seed_matches_run",
    "issue_has_inventory_label",
    "parse_inventory_seed",
    "preverify_inventory_context",
    "set_inventory_seed_status",
]
