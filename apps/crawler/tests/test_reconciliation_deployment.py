"""Static safety contracts for the Hetzner reconciliation scheduler."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

from src.cli import _await_task_or_shutdown

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

    assert "CRAWLER_IMAGE_TAG" in source
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in source
    assert 'image="ghcr.io/colophon-group/jobseek-crawler:${tag}"' in source
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
        "DATABASE_URL",
        "LOCAL_DATABASE_URL",
        "TYPESENSE_HOST",
        "TYPESENSE_PORT",
        "TYPESENSE_PROTOCOL",
        "TYPESENSE_OPERATIONS_KEY",
    ):
        assert key in source
    assert "chmod 0600" in source
    assert 'rm -f "$RUNTIME_ENV"' in source
    assert "reconciliation_args=(--repair --max-partitions 16)" in source
    assert '"--full-target"' in source
    assert 'reconciliation_args=(--repair --full --target "$2")' in source
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
    assert "full target must be supabase or typesense" in result.stderr


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
    assert "systemd-analyze verify" in installer
    assert "systemctl enable --now jobseek-crawler-reconciliation.timer" in installer
    assert "install -d -o root -g deploy -m 0750" in installer
    assert "jobseek-reconciliation-state install --revision" in installer
    assert "runuser -u deploy" in installer
    assert "grep -Eq '^[0-9a-f]{40}$' /var/lib/jobseek-reconciliation/deployed-sha" in (
        DEPLOY.read_text(encoding="utf-8")
    )
    assert "environment: production" in workflow
    assert "username: root" in workflow
    assert "JOBSEEK_RECONCILIATION_DEPLOY_SHA" in workflow
    assert "systemctl start --no-block jobseek-crawler-reconciliation.service" in workflow
    assert "! systemctl is-failed --quiet jobseek-crawler-reconciliation.service" in workflow
    for action in ("actions/checkout", "appleboy/scp-action", "appleboy/ssh-action"):
        matching = [line for line in workflow.splitlines() if f"uses: {action}@" in line]
        assert matching and all("@v" not in line for line in matching)
