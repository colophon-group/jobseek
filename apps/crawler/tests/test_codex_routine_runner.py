from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from src.workspace.codex_routine_runner import (
    DailyRoutineRunner,
    ReportedRoutineOutcome,
    _compose_routine_error,
    _read_reported_routine_outcome,
    build_daily_prompt,
)
from src.workspace.codex_runner import RunnerConfig, RunnerLedger


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
