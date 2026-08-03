from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github/workflows/operate-web-postgresql-backup.yml"
RUNBOOK_PATH = ROOT / "docs/19-data-backup-recovery.md"


def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _job(workflow: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9_]*:\n|\Z)",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing workflow job {name}"
    return match.group("body")


def test_web_backup_operations_are_manual_main_only_and_explicitly_confirmed() -> None:
    workflow = _workflow()
    authorize = _job(workflow, "authorize")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "push:" not in workflow
    assert 'test "$DISPATCH_REF" = refs/heads/main' in authorize
    for mode, confirmation in (
        ("verify", "VERIFY-WEB-POSTGRESQL"),
        ("backup", "RUN-WEB-POSTGRESQL-BACKUP"),
        ("restore", "RUN-WEB-POSTGRESQL-RESTORE-DRILL"),
        ("enable-timer", "ENABLE-WEB-POSTGRESQL-TIMER"),
    ):
        assert f"- {mode}" in workflow
        assert f"expected={confirmation}" in authorize


def test_each_remote_mode_has_an_isolated_production_job_with_pinned_ssh() -> None:
    workflow = _workflow()

    assert "group: hetzner-data-backup-production-sync" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "strategy:" not in workflow
    for name, mode in (
        ("verify", "verify"),
        ("backup", "backup"),
        ("restore", "restore"),
        ("enable_timer", "enable-timer"),
    ):
        job = _job(workflow, name)
        assert "needs: authorize" in job
        assert f"if: inputs.mode == '{mode}'" in job
        assert "environment: production" in job
        assert "secrets.HETZNER_TYPESENSE_HOST" in job
        assert "secrets.HETZNER_SSH_KEY" in job
        assert ("uses: appleboy/ssh-action@0ff4204d59e8e51228ff73bce53f80d53301dee2 # v1") in job

    uses = re.findall(r"^\s+uses: ([^\s#]+)", workflow, flags=re.MULTILINE)
    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in uses)
    assert "secrets.DATABASE_URL" not in workflow
    assert "secrets.RESTIC" not in workflow


def test_backup_and_restore_preserve_timer_state() -> None:
    workflow = _workflow()
    backup = _job(workflow, "backup")
    restore = _job(workflow, "restore")

    for job in (backup, restore):
        assert "timer_enabled_before" in job
        assert "timer_active_before" in job
        assert 'test "$timer_enabled_after" = "$timer_enabled_before"' in job
        assert 'test "$timer_active_after" = "$timer_active_before"' in job
        assert "systemctl enable" not in job
        assert "systemctl disable" not in job
    assert "systemctl start jobseek-web-postgresql-backup.service" in backup
    assert "/usr/local/sbin/jobseek-web-postgresql-restore-drill" in restore
    assert "age > 9 * 60 * 60" in restore


def test_only_enable_job_can_activate_after_matching_fresh_evidence() -> None:
    workflow = _workflow()
    enable = _job(workflow, "enable_timer")
    other_jobs = "".join(
        _job(workflow, name) for name in ("authorize", "verify", "backup", "restore")
    )

    assert "systemctl enable --now jobseek-web-postgresql-backup.timer" in enable
    assert "systemctl enable --now jobseek-web-postgresql-backup.timer" not in other_jobs
    assert "restore_started < backup_finished" in enable
    assert 'restore.get("archive_sha256") != archive_sha256' in enable
    assert 'restore.get("table_count") != backup.get("table_count")' in enable
    assert 'restore.get("row_count") != backup.get("row_count")' in enable
    assert "9 * 60 * 60" in enable
    assert "NextElapseUSecRealtime" in enable


def test_runbook_documents_the_protected_activation_sequence() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    assert ".github/workflows/operate-web-postgresql-backup.yml" in runbook
    assert "Run the modes in that order for first activation" in runbook
    for confirmation in (
        "VERIFY-WEB-POSTGRESQL",
        "RUN-WEB-POSTGRESQL-BACKUP",
        "RUN-WEB-POSTGRESQL-RESTORE-DRILL",
        "ENABLE-WEB-POSTGRESQL-TIMER",
    ):
        assert confirmation in runbook
