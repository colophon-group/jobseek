#!/usr/bin/env python3
"""Audit fleet host hygiene and remove one identity-bound exited container."""

from __future__ import annotations

import argparse
import configparser
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROLES = ("crawler", "postgresql", "typesense")
POLICY_PATH = Path("/etc/systemd/journald.conf.d/60-jobseek-retention.conf")
CANONICAL_RECONCILIATION_TIMER = "jobseek-crawler-reconciliation.timer"
RETIRED_RECONCILIATION_UNITS = (
    "jobseek-reconciliation-typesense-catchup.service",
    "jobseek-reconciliation-typesense-catchup.timer",
)
EXPECTED_JOURNAL_POLICY = {
    "crawler": {
        "Storage": "persistent",
        "SystemMaxUse": "2G",
        "SystemKeepFree": "5G",
        "SystemMaxFileSize": "128M",
        "MaxRetentionSec": "7day",
        "RuntimeMaxUse": "256M",
        "RuntimeKeepFree": "512M",
    },
    "postgresql": {
        "Storage": "persistent",
        "SystemMaxUse": "2G",
        "SystemKeepFree": "5G",
        "SystemMaxFileSize": "128M",
        "MaxRetentionSec": "7day",
        "RuntimeMaxUse": "256M",
        "RuntimeKeepFree": "512M",
    },
    "typesense": {
        "Storage": "persistent",
        "SystemMaxUse": "1G",
        "SystemKeepFree": "5G",
        "SystemMaxFileSize": "128M",
        "MaxRetentionSec": "7day",
        "RuntimeMaxUse": "256M",
        "RuntimeKeepFree": "512M",
    },
}
EXPECTED_STANDALONE_CONTAINERS = {
    "postgresql": "postgres",
    "typesense": "typesense",
}
EXPECTED_LOG_DRIVER = "json-file"
EXPECTED_LOG_OPTIONS = {"max-size": "50m", "max-file": "3"}
MANAGED_LABELS = (
    "com.docker.compose.project",
    "jobseek.managed-by",
    "jobseek.maintenance.operation",
    "jobseek.backup.service",
)
PROTECTED_NAME_PREFIXES = ("crawler-", "deploy-", "jobseek-", "postgres-", "typesense-")
PROTECTED_NAMES = {"postgres", "typesense"}
FULL_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class HygieneError(RuntimeError):
    """Raised when a hygiene boundary cannot be inspected safely."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _run(command: Sequence[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HygieneError(f"required command failed: {command[0]}") from exc
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _parse_json_inventory(result: CommandResult, boundary: str) -> list[dict[str, Any]]:
    if result.returncode != 0:
        raise HygieneError(f"{boundary} inventory failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise HygieneError(f"{boundary} inventory returned invalid JSON") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise HygieneError(f"{boundary} inventory returned an unexpected shape")
    return payload


def _journal_findings(
    role: str,
    policy_path: Path,
    *,
    effective_config: str | None = None,
) -> list[dict[str, str]]:
    expected = EXPECTED_JOURNAL_POLICY[role]
    try:
        metadata = policy_path.stat()
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        with policy_path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, configparser.Error) as exc:
        return [{"kind": "journal_policy", "detail": f"unreadable: {type(exc).__name__}"}]

    findings: list[dict[str, str]] = []
    actual = dict(parser.items("Journal")) if parser.has_section("Journal") else {}
    if actual != expected:
        findings.append({"kind": "journal_policy", "detail": "settings differ from role budget"})
    if metadata.st_uid != 0 or metadata.st_gid != 0 or stat.S_IMODE(metadata.st_mode) != 0o644:
        findings.append(
            {
                "kind": "journal_policy",
                "detail": "ownership or mode is not root:root 0644",
            }
        )
    if effective_config is None:
        result = _run(["systemd-analyze", "cat-config", "systemd/journald.conf"])
        if result.returncode != 0:
            findings.append({"kind": "journal_policy", "detail": "effective config is unavailable"})
            return findings
        effective_config = result.stdout
    try:
        effective = configparser.ConfigParser(interpolation=None, strict=False)
        effective.optionxform = str
        effective.read_string(effective_config)
        effective_values = dict(effective.items("Journal"))
    except (configparser.Error, KeyError):
        findings.append({"kind": "journal_policy", "detail": "effective config is invalid"})
        return findings
    if any(effective_values.get(key) != value for key, value in expected.items()):
        findings.append({"kind": "journal_policy", "detail": "effective settings are overridden"})
    return findings


def _failed_unit_findings() -> list[dict[str, str]]:
    result = _run(
        [
            "systemctl",
            "list-units",
            "--state=failed",
            "--type=service",
            "--type=timer",
            "--no-legend",
            "--plain",
        ]
    )
    if result.returncode != 0:
        raise HygieneError("failed-unit inventory failed")
    return [
        {"kind": "failed_unit", "detail": line.split()[0]}
        for line in result.stdout.splitlines()
        if line.split()
    ]


def _retired_reconciliation_findings() -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    canonical = _run(
        [
            "systemctl",
            "show",
            CANONICAL_RECONCILIATION_TIMER,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=UnitFileState",
            "--no-pager",
        ]
    )
    if canonical.returncode != 0:
        raise HygieneError("canonical reconciliation timer inventory failed")
    canonical_state = _properties(canonical.stdout)
    if canonical_state != {
        "LoadState": "loaded",
        "ActiveState": "active",
        "UnitFileState": "enabled",
    }:
        findings.append(
            {
                "kind": "reconciliation_scheduler",
                "detail": "canonical timer is not loaded, active, and enabled",
            }
        )

    for unit in RETIRED_RECONCILIATION_UNITS:
        result = _run(
            [
                "systemctl",
                "show",
                unit,
                "--property=LoadState",
                "--property=ActiveState",
                "--property=UnitFileState",
                "--no-pager",
            ]
        )
        if result.returncode != 0:
            raise HygieneError(f"retired reconciliation unit inventory failed: {unit}")
        state = _properties(result.stdout)
        if state != {
            "LoadState": "masked",
            "ActiveState": "inactive",
            "UnitFileState": "masked",
        }:
            findings.append({"kind": "retired_reconciliation_unit", "detail": unit})

    timers = _run(["systemctl", "list-unit-files", "--type=timer", "--no-legend", "--plain"])
    if timers.returncode != 0:
        raise HygieneError("timer unit-file inventory failed")
    allowed = {CANONICAL_RECONCILIATION_TIMER, RETIRED_RECONCILIATION_UNITS[1]}
    for line in timers.stdout.splitlines():
        fields = line.split()
        if fields and "reconciliation" in fields[0] and fields[0] not in allowed:
            findings.append({"kind": "duplicate_reconciliation_timer", "detail": fields[0]})
    return findings


def _properties(output: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def _exited_container_findings() -> list[dict[str, str]]:
    listing = _run(["docker", "ps", "-aq", "--filter", "status=exited"])
    if listing.returncode != 0:
        raise HygieneError("exited-container inventory failed")
    container_ids = listing.stdout.split()
    if not container_ids:
        return []
    inventory = _parse_json_inventory(
        _run(["docker", "inspect", *container_ids]), "exited-container"
    )
    findings: list[dict[str, str]] = []
    for container in inventory:
        config = container.get("Config") or {}
        labels = config.get("Labels") or {}
        if not isinstance(labels, dict):
            labels = {}
        if any(labels.get(label) for label in MANAGED_LABELS):
            continue
        state = container.get("State") or {}
        if state.get("Status") != "exited" or state.get("Running") is not False:
            raise HygieneError("exited-container inventory changed during inspection")
        container_id = str(container.get("Id") or "")
        findings.append(
            {
                "kind": "unmanaged_exited_container",
                "detail": (
                    container_id[:12]
                    if FULL_CONTAINER_ID_RE.fullmatch(container_id)
                    else "invalid-id"
                ),
            }
        )
    return findings


def _standalone_log_findings(role: str) -> list[dict[str, str]]:
    name = EXPECTED_STANDALONE_CONTAINERS.get(role)
    if name is None:
        return []
    inventory = _parse_json_inventory(_run(["docker", "inspect", name]), name)
    if len(inventory) != 1:
        raise HygieneError(f"expected exactly one {name} container")
    container = inventory[0]
    state = container.get("State") or {}
    log_config = (container.get("HostConfig") or {}).get("LogConfig") or {}
    if (
        state.get("Running") is True
        and log_config.get("Type") == EXPECTED_LOG_DRIVER
        and log_config.get("Config") == EXPECTED_LOG_OPTIONS
    ):
        return []
    return [{"kind": "standalone_container_log", "detail": name}]


def audit(role: str, *, policy_path: Path = POLICY_PATH) -> dict[str, Any]:
    findings = [
        *_journal_findings(role, policy_path),
        *_failed_unit_findings(),
        *_exited_container_findings(),
        *_standalone_log_findings(role),
    ]
    if role == "crawler":
        findings.extend(_retired_reconciliation_findings())
    return {"role": role, "conformant": not findings, "findings": findings}


def _validated_cleanup_inventory(
    *,
    container_id: str,
    image_id: str,
    created_at: str,
    finished_at: str,
    exit_code: int,
) -> dict[str, Any]:
    if not FULL_CONTAINER_ID_RE.fullmatch(container_id):
        raise HygieneError("container ID must be 64 lowercase hexadecimal characters")
    if not FULL_IMAGE_ID_RE.fullmatch(image_id):
        raise HygieneError("image ID must be a full sha256 identity")
    inventory = _parse_json_inventory(
        _run(["docker", "inspect", container_id]), "cleanup-container"
    )
    if len(inventory) != 1:
        raise HygieneError("cleanup identity did not resolve to exactly one container")
    container = inventory[0]
    state = container.get("State") or {}
    config = container.get("Config") or {}
    labels = config.get("Labels") or {}
    name = str(container.get("Name") or "").lstrip("/")
    identity_matches = (
        container.get("Id") == container_id
        and container.get("Image") == image_id
        and container.get("Created") == created_at
        and state.get("FinishedAt") == finished_at
        and state.get("ExitCode") == exit_code
    )
    safely_stopped = (
        state.get("Status") == "exited"
        and state.get("Running") is False
        and state.get("Paused") is False
        and state.get("Restarting") is False
        and state.get("Dead") is False
    )
    protected = (
        name in PROTECTED_NAMES
        or name.startswith(PROTECTED_NAME_PREFIXES)
        or (isinstance(labels, dict) and any(labels.get(key) for key in MANAGED_LABELS))
    )
    if not identity_matches:
        raise HygieneError("container identity changed since the reviewed inventory")
    if not safely_stopped:
        raise HygieneError("container is not an ordinary exited container")
    if protected:
        raise HygieneError("container belongs to a protected or managed namespace")
    return container


def remove_exited_container(args: argparse.Namespace) -> dict[str, Any]:
    if args.role != "postgresql":
        raise HygieneError("the audited cleanup allowlist is limited to the PostgreSQL host")
    if args.execute and os.geteuid() != 0:
        raise HygieneError("identity-bound container removal must run as root")
    container = _validated_cleanup_inventory(
        container_id=args.container_id,
        image_id=args.image_id,
        created_at=args.created_at,
        finished_at=args.finished_at,
        exit_code=args.exit_code,
    )
    result = {
        "role": args.role,
        "container_id": args.container_id,
        "image_id": args.image_id,
        "name_observed": str(container.get("Name") or "").lstrip("/"),
        "removed": False,
    }
    if not args.execute:
        return result
    removal = _run(["docker", "rm", "--", args.container_id])
    if removal.returncode != 0:
        raise HygieneError("identity-bound container removal failed")
    if _run(["docker", "inspect", args.container_id]).returncode == 0:
        raise HygieneError("container still exists after Docker reported removal")
    result["removed"] = True
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--role", required=True, choices=ROLES)
    audit_parser.add_argument(
        "--policy-path", type=Path, default=POLICY_PATH, help=argparse.SUPPRESS
    )
    audit_parser.add_argument("--require-conformant", action="store_true")

    journal_parser = subparsers.add_parser("verify-journal")
    journal_parser.add_argument("--role", required=True, choices=ROLES)
    journal_parser.add_argument(
        "--policy-path", type=Path, default=POLICY_PATH, help=argparse.SUPPRESS
    )

    cleanup = subparsers.add_parser("remove-exited-container")
    cleanup.add_argument("--role", required=True, choices=ROLES)
    cleanup.add_argument("--container-id", required=True)
    cleanup.add_argument("--image-id", required=True)
    cleanup.add_argument("--created-at", required=True)
    cleanup.add_argument("--finished-at", required=True)
    cleanup.add_argument("--exit-code", required=True, type=int)
    cleanup.add_argument("--execute", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "verify-journal":
            findings = _journal_findings(args.role, args.policy_path)
            print(json.dumps({"role": args.role, "findings": findings}, sort_keys=True))
            return 1 if findings else 0
        if args.command == "audit":
            result = audit(args.role, policy_path=args.policy_path)
            print(json.dumps(result, sort_keys=True))
            return 1 if args.require_conformant and not result["conformant"] else 0
        result = remove_exited_container(args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except HygieneError as exc:
        print(f"host hygiene failed closed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
