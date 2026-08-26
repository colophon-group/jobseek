#!/usr/bin/env python3
"""Verify the staged Typesense memory policy across every production consumer."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
EXPANDED_POLICY = (6 * 1024**3, 5 * 1024**3, 6 * 1024**3)
LEGACY_POLICY = (3 * 1024**3, 2560 * 1024**2, 3 * 1024**3)


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def require_all(text: str, snippets: tuple[str, ...], source: str) -> None:
    missing = [snippet for snippet in snippets if snippet not in text]
    if missing:
        raise AssertionError(f"{source} is missing memory-contract evidence: {missing}")


def main() -> int:
    backup_runner_path = ROOT / "scripts/jobseek-data-backup.py"
    backup_runner = load_module(
        "jobseek_data_backup_memory_contract",
        backup_runner_path,
    )
    policies = backup_runner.TYPESENSE_REVIEWED_MEMORY_POLICIES
    if policies != frozenset({LEGACY_POLICY, EXPANDED_POLICY}):
        raise AssertionError(f"backup runner has unexpected reviewed policies: {policies}")
    require_all(
        backup_runner_path.read_text(encoding="utf-8"),
        (
            "not in TYPESENSE_REVIEWED_MEMORY_POLICIES",
            "A follow-up removes the legacy tuple after the promotion is proven.",
        ),
        "backup runner",
    )

    host_verifier = load_module(
        "jobseek_typesense_host_verifier_memory_contract",
        ROOT / "scripts/verify-typesense-host-credentials.py",
    )
    if host_verifier.TYPESENSE_MEMORY_POLICIES != {
        "expanded": EXPANDED_POLICY,
        "legacy": LEGACY_POLICY,
    }:
        raise AssertionError("live host verifier disagrees with the staged memory policies")

    host_installer = (ROOT / "deploy/typesense-host/install-host.sh").read_text(
        encoding="utf-8"
    )
    require_all(
        host_installer,
        (
            "TYPESENSE_MEMORY_POLICY=${JOBSEEK_TYPESENSE_MEMORY_POLICY:-expanded}",
            "TYPESENSE_MEMORY_LIMIT_BYTES=6442450944",
            "TYPESENSE_MEMORY_RESERVATION_BYTES=5368709120",
            "TYPESENSE_MEMORY_SWAP_BYTES=6442450944",
            '"$REPO_ROOT/deploy/typesense-host/check-memory-capacity.sh"',
        ),
        "host installer",
    )
    memory_preflight = host_installer.index(
        '"$REPO_ROOT/deploy/typesense-host/check-memory-capacity.sh"'
    )
    state_mutation = host_installer.index(
        'install -d -o root -g root -m 0700 "$STATE_DIR" "$CREDENTIAL_DIR"'
    )
    if memory_preflight >= state_mutation:
        raise AssertionError("host capacity check must run before state mutation")
    exit_trap = host_installer.index("trap typesense_host_exit EXIT")
    prepare_call = host_installer.index("prepare_host_deployment", exit_trap)
    component_dispatch = host_installer.index('case "$COMPONENT" in', prepare_call)
    if not exit_trap < prepare_call < component_dispatch:
        raise AssertionError("host preparation must run before component dispatch")

    backup_installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    require_all(
        backup_installer,
        (
            'get("Memory") or 0) == 6442450944',
            'get("MemoryReservation") or 0) == 5368709120',
            'get("MemorySwap") or 0) == 6442450944',
        ),
        "backup installer",
    )

    workflow = (ROOT / ".github/workflows/deploy-typesense-host.yml").read_text(
        encoding="utf-8"
    )
    require_all(
        workflow,
        (
            "deploy/typesense-host/check-memory-capacity.sh --self-test",
            "python3 deploy/typesense-host/verify-memory-contract.py",
            "--memory 6g",
            "--memory-reservation 5g",
            "--memory-swap 6g",
            'get("Memory") or 0) == 6442450944',
            'get("MemoryReservation") or 0) == 5368709120',
            'get("MemorySwap") or 0) == 6442450944',
            "JOBSEEK_TYPESENSE_MEMORY_POLICY: expanded",
            "envs: JOBSEEK_TYPESENSE_HOST_DEPLOY_SHA,JOBSEEK_TYPESENSE_MEMORY_POLICY,",
        ),
        "host workflow",
    )

    print("Typesense staged memory contract verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
