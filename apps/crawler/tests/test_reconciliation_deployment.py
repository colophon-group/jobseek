"""Static safety contracts for the Hetzner reconciliation scheduler."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.cli import _await_task_or_shutdown, parse_args

ROOT = Path(__file__).resolve().parents[3]
RUNNER = ROOT / "deploy/reconciliation/run.sh"
INSTALLER = ROOT / "deploy/reconciliation/install-host.sh"
STATE = ROOT / "deploy/reconciliation/state.py"
SERVICE = ROOT / "deploy/systemd/jobseek-crawler-reconciliation.service"
TIMER = ROOT / "deploy/systemd/jobseek-crawler-reconciliation.timer"
WORKFLOW = ROOT / ".github/workflows/deploy-crawler-reconciliation.yml"
CI_WORKFLOW = ROOT / ".github/workflows/ci.yml"
DEPLOY = ROOT / "apps/crawler/deploy.sh"
MAINTENANCE = ROOT / ".github/workflows/crawler-scheduled-maintenance.yml"
SYNC_DATA = ROOT / ".github/workflows/sync-data.yml"
REFRESH_CURRENCY = ROOT / ".github/workflows/refresh-currency-rates.yml"

STATE_SPEC = importlib.util.spec_from_file_location("reconciliation_state", STATE)
assert STATE_SPEC is not None and STATE_SPEC.loader is not None
state = importlib.util.module_from_spec(STATE_SPEC)
STATE_SPEC.loader.exec_module(state)


def test_reconciliation_shell_surfaces_parse() -> None:
    for path in (RUNNER, INSTALLER):
        result = subprocess.run(
            ["bash", "-n", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_revision_state_recovers_from_missing_and_corrupt_files(tmp_path: Path) -> None:
    revision = "a" * 40

    state.install_revision(tmp_path, revision)
    assert state.read_revision(tmp_path) == revision


def test_wrapper_digest_state_is_atomic_validated_and_group_readable(tmp_path: Path) -> None:
    revision = "a" * 40
    wrapper_sha256 = "b" * 64

    state.install_revision(tmp_path, revision)
    state.install_wrapper_sha256(tmp_path, wrapper_sha256)
    assert state.read_wrapper_sha256(tmp_path) == wrapper_sha256
    assert os.stat(tmp_path / "wrapper-sha256").st_mode & 0o777 == 0o640

    (tmp_path / "wrapper-sha256").write_text("corrupt\n", encoding="ascii")
    with pytest.raises(state.StateError, match="invalid"):
        state.read_wrapper_sha256(tmp_path)
    state.install_wrapper_sha256(tmp_path, wrapper_sha256)
    assert state.read_wrapper_sha256(tmp_path) == wrapper_sha256

    (tmp_path / "wrapper-sha256").unlink()
    with pytest.raises(state.StateError, match="unavailable"):
        state.read_wrapper_sha256(tmp_path)
    assert os.stat(tmp_path).st_mode & 0o777 == 0o750
    assert os.stat(tmp_path / "deployed-sha").st_mode & 0o777 == 0o640

    (tmp_path / "deployed-sha").write_text("corrupt\n", encoding="ascii")
    with pytest.raises(state.StateError, match="invalid"):
        state.read_revision(tmp_path)
    state.install_revision(tmp_path, revision)
    assert state.read_revision(tmp_path) == revision

    (tmp_path / "deployed-sha").unlink()
    with pytest.raises(state.StateError, match="unavailable"):
        state.read_revision(tmp_path)
    state.install_revision(tmp_path, revision)
    assert state.read_revision(tmp_path) == revision


def test_runner_is_bounded_immutable_and_fail_closed() -> None:
    source = RUNNER.read_text(encoding="utf-8")

    assert "CRAWLER_IMAGE_REF" in source
    assert "jobseek-crawler@sha256:[0-9a-f]{64}" in source
    assert 'image="${image_refs[0]}"' in source
    assert "ghcr.io/colophon-group/jobseek-crawler:latest" not in source
    assert "--read-only" in source
    assert "--memory 1g" in source
    assert "--cpus 1.0" in source
    assert "--pids-limit 256" in source
    assert "timeout --foreground --signal=TERM --kill-after=90s 50m" in source
    assert '--env-file "$RUNTIME_ENV"' in source
    assert '--env-file "$ENV_FILE"' not in source
    assert "required_env=(" in source
    for key in (
        "LOCAL_DATABASE_URL",
        "TYPESENSE_HOST",
        "TYPESENSE_PORT",
        "TYPESENSE_PROTOCOL",
        "TYPESENSE_OPERATIONS_KEY",
    ):
        assert key in source
    assert re.search(r"\bDATABASE_URL\b", source) is None
    assert "WEB_DATABASE_URL" not in source
    assert "chmod 0600" in source
    assert 'rm -f "$RUNTIME_ENV"' in source
    assert "reconciliation_args=(--repair --max-partitions 16 --target typesense)" in source
    assert '"--full"' in source
    assert "reconciliation_args=(--repair --full --target typesense)" in source
    assert "supabase" not in source.lower()
    assert '/app/.venv/bin/crawler reconcile "${reconciliation_args[@]}"' in source
    assert "uv run" not in source
    assert "jobseek-crawler-mutation.lock" in source
    assert "flock -w 7200" in source
    assert "/usr/local/sbin/jobseek-reconciliation-state check" in source
    assert '[[ "$revision" =~ ^[0-9a-f]{40}$ ]]' in source
    for label in (
        "com.docker.compose.project=deploy",
        "com.docker.compose.service=cross-store-reconciliation",
        "com.docker.compose.oneoff=True",
        "jobseek.maintenance.operation=cross-store-reconciliation",
        "jobseek.maintenance.issue=5930",
        "jobseek.maintenance.revision=${revision}",
        "jobseek.maintenance.budget-seconds=3000",
    ):
        assert label in source
    assert "jobseek.maintenance=cross-store-reconciliation" not in source


def test_ci_smokes_the_entrypoint_on_a_read_only_root() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "Smoke-test read-only crawler entry point" in workflow
    assert "--read-only" in workflow
    assert "--tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m" in workflow
    assert "--entrypoint /app/.venv/bin/crawler" in workflow


def test_runner_rejects_an_unbounded_or_combined_full_target() -> None:
    result = subprocess.run(
        ["bash", str(RUNNER), "--full-target", "all"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_crawler_reconciliation_cli_cannot_select_supabase(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "reconcile", "--target", "typesense"])
    assert parse_args().target == "typesense"

    monkeypatch.setattr(sys, "argv", ["crawler", "reconcile", "--target", "supabase"])
    with pytest.raises(SystemExit):
        parse_args()


async def test_reconciliation_task_is_cancelled_on_process_shutdown() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    shutdown = asyncio.Event()

    async def blocked_work() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    task = asyncio.create_task(blocked_work())
    waiter = asyncio.create_task(_await_task_or_shutdown(task, shutdown))
    await started.wait()

    shutdown.set()

    assert await waiter is None
    assert task.cancelled()
    assert cancelled.is_set()


def test_all_crawler_mutation_entrypoints_share_the_host_lock() -> None:
    for path in (RUNNER, DEPLOY, MAINTENANCE):
        source = path.read_text(encoding="utf-8")
        assert "/run/lock/jobseek-crawler-mutation.lock" in source
        assert "flock -w 7200" in source
    for path in (SYNC_DATA, REFRESH_CURRENCY):
        source = path.read_text(encoding="utf-8")
        assert "/usr/local/sbin/jobseek-maintenance oneoff" in source


def test_scheduled_oneoffs_filter_database_credentials_by_command() -> None:
    maintenance = MAINTENANCE.read_text(encoding="utf-8")
    currency = REFRESH_CURRENCY.read_text(encoding="utf-8")

    assert "--env-file /home/deploy/.env" not in maintenance
    assert '--env-file "$RUNTIME_ENV"' in maintenance
    assert 'if [[ "$TASK" == "refresh-typesense" ]]' in maintenance
    assert "required_env+=(WEB_DATABASE_URL)" in maintenance
    assert re.findall(r"\bDATABASE_URL\b", maintenance) == ["DATABASE_URL"]
    assert "grep -Eq '^(DATABASE_URL|DATABASE_URL_UNPOOLED|WEB_DATABASE_URL)$'" in maintenance

    assert "--env-file /home/deploy/.env" not in currency
    assert '--env-file "$RUNTIME_ENV"' in currency
    assert "^LOCAL_DATABASE_URL=" in currency
    assert "WEB_DATABASE_URL" not in currency
    assert re.search(r"\bDATABASE_URL\b", currency) is None


def test_systemd_unit_has_separate_wait_and_runtime_budget() -> None:
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")

    assert "User=deploy" in service
    assert "TimeoutStartSec=3h" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "RestrictAddressFamilies=AF_UNIX" in service
    assert "ReadWritePaths=/run/lock" in service
    assert "OnActiveSec=20m" in timer
    assert "OnUnitInactiveSec=1h" in timer
    assert "OnUnitActiveSec" not in timer
    assert "RandomizedDelaySec=10m" in timer


def test_install_and_workflow_preserve_rollback_and_privilege_boundary() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert '[[ "$(id -u)" -eq 0 ]]' in installer
    assert "TIMER_WAS_ENABLED" in installer
    assert "TIMER_WAS_ACTIVE" in installer
    assert "systemctl disable --now jobseek-crawler-reconciliation.timer" in installer
    assert "restore_previous" in installer
    assert 'if [[ "$path" == "$STATE_ROOT/"* ]]' in installer
    assert 'install -o root -g "$group"' in installer
    assert "systemd-analyze verify" in installer
    assert "systemctl enable --now jobseek-crawler-reconciliation.timer" in installer
    assert "install -d -o root -g deploy -m 0750" in installer
    assert "jobseek-reconciliation-state install" in installer
    assert '--revision "$DEPLOY_SHA"' in installer
    assert "--wrapper-sha256" in installer
    assert "/var/lib/jobseek-reconciliation/wrapper-sha256" in installer
    assert "runuser -u deploy" in installer
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert "JOBSEEK_RECONCILIATION_WRAPPER_SHA256" in deploy
    assert "sha256sum /usr/local/sbin/jobseek-crawler-reconciliation" in deploy
    assert "--expected-wrapper-sha256" in deploy
    assert "environment: production" in workflow
    assert "username: root" in workflow
    assert "JOBSEEK_RECONCILIATION_DEPLOY_SHA" in workflow
    assert "JOBSEEK_RECONCILIATION_WRAPPER_SHA256" in workflow
    assert "systemctl start --no-block jobseek-crawler-reconciliation.service" in workflow
    assert "! systemctl is-failed --quiet jobseek-crawler-reconciliation.service" in workflow
    for action in ("actions/checkout", "appleboy/scp-action", "appleboy/ssh-action"):
        matching = [line for line in workflow.splitlines() if f"uses: {action}@" in line]
        assert matching and all("@v" not in line for line in matching)


def test_exact_revision_dispatches_attest_the_live_deployment() -> None:
    maintenance = MAINTENANCE.read_text(encoding="utf-8")

    assert "- verify-typesense-taxonomies" in maintenance
    assert "expected_crawler_revision:" in maintenance
    assert "^[0-9a-f]{40}$" in maintenance
    assert (
        'if [[ "$task" == backfill-typesense || '
        '"$task" == verify-typesense-taxonomies ]]' in maintenance
    )
    assert (
        'if [[ "$TASK" == backfill-typesense || '
        '"$TASK" == verify-typesense-taxonomies ]]' in maintenance
    )
    assert "EXPECTED_CRAWLER_REVISION: ${{ steps.task.outputs.expected_revision }}" in maintenance
    assert "read_exact_env JOBSEEK_DEPLOY_REVISION" in maintenance
    assert '[[ "$active_revision" == "$EXPECTED_CRAWLER_REVISION" ]]' in maintenance
    assert "jobseek-crawler@sha256:[0-9a-f]{64}" in maintenance
    assert "Expected exactly one live exporter container" in maintenance
    assert "com.docker.compose.service=exporter" in maintenance
    assert "Live exporter image does not match the active crawler digest" in maintenance
    assert "Live exporter still has a relational mirror credential" in maintenance


def test_taxonomy_verification_dispatch_does_not_backfill_or_reconcile() -> None:
    maintenance = MAINTENANCE.read_text(encoding="utf-8")

    branch_start = maintenance.index('elif [[ "$TASK" == verify-typesense-taxonomies ]]')
    branch_end = maintenance.index("              fi", branch_start)
    branch = maintenance[branch_start:branch_end]

    assert (
        branch.count("operation_command=(uv run --no-sync crawler verify-typesense-taxonomies)")
        == 1
    )
    assert "crawler backfill-typesense" not in branch
    assert "crawler reconcile" not in branch
    assert "LOCAL_DATABASE_URL" in maintenance
    assert "TYPESENSE_OPERATIONS_KEY" in maintenance
    assert "verify-typesense-taxonomies)-'" in maintenance


def test_backfill_proof_is_one_locked_fail_closed_chain() -> None:
    maintenance = MAINTENANCE.read_text(encoding="utf-8")
    chain = (
        "uv run --no-sync crawler backfill-typesense && "
        "uv run --no-sync crawler reconcile --repair --full --fresh-cycle "
        "--target typesense && "
        "uv run --no-sync crawler verify-typesense-taxonomies"
    )

    lock = maintenance.index("exec 9>/run/lock/jobseek-crawler-mutation.lock")
    proof = maintenance.index(chain)
    hygiene = maintenance.index('crawler-host-hygiene.py"')

    assert maintenance.count(chain) == 1
    assert lock < proof < hygiene
    assert "operation_budget=14400" in maintenance
    assert 'timeout --foreground --signal=TERM --kill-after=90s "$operation_budget"' in maintenance
    assert "command_timeout: 8h" in maintenance


def test_scheduled_refresh_resolves_the_committed_digest_before_dispatch() -> None:
    maintenance = MAINTENANCE.read_text(encoding="utf-8")

    image_ref = maintenance.index('image="$(read_exact_env CRAWLER_IMAGE_REF)"')
    exact_revision_gate = maintenance.index(
        'if [[ "$TASK" == backfill-typesense || "$TASK" == verify-typesense-taxonomies ]]'
    )
    image = maintenance.index('                "$image" \\')

    assert image_ref < exact_revision_gate < image
    assert "jobseek-crawler@sha256:[0-9a-f]{64}" in maintenance
