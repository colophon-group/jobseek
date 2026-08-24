from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.workspace.codex_routine_runner import (
    LABELLER_POSTGRES_ENV,
    DailyRoutineRunner,
    DailyRunResult,
    ReportedRoutineOutcome,
    _compose_routine_error,
    _read_reported_routine_outcome,
    build_daily_prompt,
)
from src.workspace.codex_runner import RunnerConfig, RunnerLedger, SchedulerDecision


def _config(tmp_path: Path, *, dry_run: bool = False) -> RunnerConfig:
    root = tmp_path / "runner"
    repo = tmp_path / "repo"
    (repo / "apps" / "crawler").mkdir(parents=True)
    return RunnerConfig(
        root=root,
        repo_dir=repo,
        dry_run=dry_run,
        codex_args=("python3", "-c", "raise SystemExit(0)"),
        min_disk_free_gib=0,
        min_mem_available_gib=0,
        max_load_per_cpu=999,
    ).resolved()


def test_error_review_prompt_uses_bundle_without_host_widening() -> None:
    prompt = build_daily_prompt(
        "error-review",
        run_date="2026-07-09",
        count=10,
        error_bundle=Path("/srv/jobseek-codex/inputs/error-review/latest"),
    )

    assert ".agents/skills/jobseek-error-review/SKILL.md" in prompt
    assert "/srv/jobseek-codex/inputs/error-review/latest" in prompt
    assert "Do not attempt to read Docker directly" in prompt
    assert "Do not print, copy, upload, or commit secrets" in prompt


def test_annotation_prompt_requires_first_causal_failure_marker() -> None:
    prompt = build_daily_prompt("annotations", run_date="2026-07-22", count=10)
    compact = " ".join(prompt.split())

    assert "JOBSEEK_ROUTINE_RESULT=" in prompt
    assert "first causal error" in compact
    assert "missing downstream" in compact


def test_daily_runner_skips_date_after_completed_ledger_row(tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    ledger = RunnerLedger(config.ledger_path)
    assert ledger.acquire(
        run_id="daily-error-review-2026-07-09-123",
        issue=None,
        active_slot="daily-error-review",
    )
    ledger.finish("daily-error-review-2026-07-09-123", "completed")
    runner = DailyRoutineRunner(
        config,
        routine="error-review",
        run_date="2026-07-09",
        ledger=ledger,
    )

    result = runner.run_once()

    assert result.state == "skipped"
    assert result.error == "daily routine already completed for date"


def test_daily_runner_retries_failed_exports_before_completed_date_skip(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(tmp_path, dry_run=True)
    ledger = RunnerLedger(config.ledger_path)
    run_id = "daily-error-review-2026-07-09-123"
    assert ledger.acquire(run_id=run_id, issue=None, active_slot="daily-error-review")
    ledger.finish(run_id, "completed")
    calls: list[str] = []
    monkeypatch.setattr(
        "src.workspace.codex_routine_runner.retry_failed_trace_exports",
        lambda **kwargs: calls.append("retry"),
    )
    runner = DailyRoutineRunner(
        config,
        routine="error-review",
        run_date="2026-07-09",
        ledger=ledger,
    )

    result = runner.run_once()

    assert result.state == "skipped"
    assert calls == ["retry"]


@pytest.mark.parametrize("state", ["completed", "failed", "timeout"])
def test_daily_terminal_states_use_shared_verified_export(
    monkeypatch, tmp_path: Path, state: str
) -> None:
    config = _config(tmp_path, dry_run=False)
    runner = DailyRoutineRunner(
        config,
        routine="error-review",
        run_date="2099-02-01",
    )
    monkeypatch.setattr(
        runner,
        "should_start",
        lambda: SchedulerDecision(True, "ready", 1, 0),
    )
    monkeypatch.setattr(
        "src.workspace.codex_routine_runner.retry_failed_trace_exports",
        lambda **kwargs: None,
    )
    assert config.traces_dir is not None

    def fake_execute(run_id: str) -> DailyRunResult:
        runner.ledger.finish(run_id, state, error=None if state == "completed" else state)
        return DailyRunResult(
            run_id=run_id,
            routine="error-review",
            run_date="2099-02-01",
            state=state,
            trace_path=config.traces_dir / f"{run_id}.jsonl",
        )

    monkeypatch.setattr(runner, "_execute", fake_execute)
    exported: list[str] = []

    def fake_export(*, config, ledger, result):
        exported.append(result.state)
        result.trace_export_status = "cleaned"
        result.trace_export_tier = "gold"
        result.trace_export_remote_dir = f"training-bundles/v2/gold/{result.run_id}"
        return result

    monkeypatch.setattr("src.workspace.codex_routine_runner.export_terminal_trace", fake_export)

    result = runner.run_once()

    assert result.state == state
    assert result.trace_export_status == "cleaned"
    assert result.trace_export_tier == "gold"
    assert exported == [state]


def test_daily_final_guard_failure_uses_shared_export(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    runner = DailyRoutineRunner(
        config,
        routine="annotations",
        run_date="2099-02-02",
    )
    monkeypatch.setattr(
        runner,
        "should_start",
        lambda: SchedulerDecision(True, "ready", 1, 0),
    )
    monkeypatch.setattr(
        "src.workspace.codex_routine_runner.retry_failed_trace_exports",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        runner,
        "_execute",
        lambda run_id: (_ for _ in ()).throw(RuntimeError("final guard")),
    )
    exported: list[str] = []

    def fake_export(*, config, ledger, result):
        exported.append(result.state)
        result.trace_export_status = "unavailable"
        return result

    monkeypatch.setattr("src.workspace.codex_routine_runner.export_terminal_trace", fake_export)

    result = runner.run_once()

    assert result.state == "failed"
    assert result.error == "final guard"
    assert result.trace_export_status == "unavailable"
    assert exported == ["failed"]


def test_error_review_missing_report_fails_even_when_codex_exits_zero(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(tmp_path, dry_run=False)
    runner = DailyRoutineRunner(
        config,
        routine="error-review",
        run_date="2099-01-01",
    )
    monkeypatch.setattr(runner, "_prepare_worktree", lambda run_id: config.repo_dir)
    monkeypatch.setattr("src.workspace.codex_runner._mem_available_gib", lambda: 99)
    monkeypatch.setattr("src.workspace.codex_runner.os.getloadavg", lambda: (0, 0, 0))
    monkeypatch.setattr("src.workspace.codex_runner._missing_git_identity", lambda: [])

    result = runner.run_once()

    assert result.state == "failed"
    assert result.error is not None
    assert "expected error-review report missing" in result.error


def test_annotation_verifier_uses_safe_env_and_labeller_data_root(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(tmp_path, dry_run=False)
    runner = DailyRoutineRunner(
        config,
        routine="annotations",
        run_date="2026-07-09",
    )
    worktree = config.repo_dir
    captured: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="data/2026-07-09.jsonl rows=10\n", stderr="")

    monkeypatch.setenv("LABELLER_DATA_ROOT", "/srv/jobseek-codex/data/postings-labelled")
    monkeypatch.setenv("LOCAL_DATABASE_URL", "postgresql://should-not-leak")
    monkeypatch.setenv("HF_TOKEN", "hf_should_not_leak")
    monkeypatch.setattr("src.workspace.codex_routine_runner.subprocess.run", fake_run)

    error = runner._verify_annotation_upload(worktree)

    assert error is None
    assert captured["LABELLER_DATA_ROOT"] == "/srv/jobseek-codex/data/postings-labelled"
    assert "LOCAL_DATABASE_URL" not in captured
    assert "HF_TOKEN" not in captured


def test_annotation_child_injects_exact_postgresql_budget_without_secret_rewrite(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(tmp_path, dry_run=False)
    runner = DailyRoutineRunner(
        config,
        routine="annotations",
        run_date="2026-07-09",
    )
    monkeypatch.setenv("JOBSEEK_LABELLER_ENV_FILE", "/etc/jobseek-codex/labeller.env")
    monkeypatch.setenv("LOCAL_DATABASE_URL", "postgresql://must-not-leak")

    env = runner._codex_env(trace_path=tmp_path / "trace.jsonl", run_id="annotations-1")

    assert LABELLER_POSTGRES_ENV == {
        "CRAWLER_DB_ROLE": "labeller",
        "CRAWLER_DB_POOL_MIN": "0",
        "CRAWLER_DB_POOL_MAX": "2",
        "CRAWLER_DB_POOL_IDLE_SECONDS": "60",
    }
    assert {key: env[key] for key in LABELLER_POSTGRES_ENV} == LABELLER_POSTGRES_ENV
    assert env["JOBSEEK_LABELLER_DB_LOCK_FILE"] == str(
        config.state_dir / "labeller-postgresql.lock"
    )
    assert env["JOBSEEK_LABELLER_DB_LOCK_TIMEOUT_SECONDS"] == "300"
    assert env["JOBSEEK_LABELLER_ENV_FILE"] == "/etc/jobseek-codex/labeller.env"
    assert "LOCAL_DATABASE_URL" not in env


def test_annotation_verifier_reports_bounded_timeout(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    runner = DailyRoutineRunner(
        config,
        routine="annotations",
        run_date="2026-07-09",
    )

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr("src.workspace.codex_routine_runner.subprocess.run", fake_run)

    error = runner._verify_annotation_upload(config.repo_dir)

    assert error == "annotation upload verification timed out after 120 seconds"


def test_annotation_failure_preserves_primary_error_before_upload_symptom(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace.jsonl"
    marker = {
        "status": "failed",
        "phase": "sampling",
        "primary_error": "PostgreSQL canceled the query at the 30-second statement timeout",
    }
    trace.write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": "Fail closed.\nJOBSEEK_ROUTINE_RESULT=" + json.dumps(marker),
                },
            }
        )
        + "\n"
    )

    outcome = _read_reported_routine_outcome(trace)
    error = _compose_routine_error(
        routine="annotations",
        exit_code=0,
        verification_error=(
            "annotation upload verification failed: could not download data/2026-07-22.jsonl"
        ),
        reported_outcome=outcome,
    )

    assert outcome == ReportedRoutineOutcome(
        status="failed",
        phase="sampling",
        primary_error="PostgreSQL canceled the query at the 30-second statement timeout",
    )
    assert error is not None
    assert error.startswith("annotation routine failed in sampling: PostgreSQL canceled")
    assert "; downstream verification: annotation upload verification failed" in error


def test_annotation_failure_redacts_credentials_from_reported_outcome(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace.jsonl"
    marker = {
        "status": "failed",
        "phase": "sampling",
        "primary_error": "DATABASE_URL=postgresql://crawler:not-a-real-password@db/crawler",
    }
    trace.write_text(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": "JOBSEEK_ROUTINE_RESULT=" + json.dumps(marker),
                },
            }
        )
        + "\n"
    )

    outcome = _read_reported_routine_outcome(trace)

    assert outcome is not None
    assert outcome.primary_error is not None
    assert "not-a-real-password" not in outcome.primary_error
    assert "<REDACTED_CREDENTIAL>" in outcome.primary_error
