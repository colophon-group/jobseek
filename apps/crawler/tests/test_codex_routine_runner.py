from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.workspace.codex_routine_runner import (
    DailyRoutineRunner,
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
    assert "metrics/historical-prometheus.json" in prompt
    assert "## Metrics evidence" in prompt
    assert "required_complete is false" in prompt
    assert "Do not print, copy, upload, or commit secrets" in prompt


def test_error_review_report_requires_metrics_coverage_when_bundle_is_configured(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    report = tmp_path / "dev" / "claude" / "review-jobseek-errors" / "2026-07-09.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "# Daily error review - 2026-07-09\nWindow: 2026-07-08 09:00 UTC -> 2026-07-09 09:00 UTC\n",
        encoding="utf-8",
    )
    bundle_path = tmp_path / "bundle"
    (bundle_path / "metrics").mkdir(parents=True)
    (bundle_path / "metrics" / "historical-prometheus.json").write_text(
        json.dumps(
            {
                "required_complete": False,
                "queries": [{"id": "scrape_targets", "status": "missing"}],
            }
        ),
        encoding="utf-8",
    )
    runner = DailyRoutineRunner(
        _config(tmp_path),
        routine="error-review",
        run_date="2026-07-09",
        error_bundle=bundle_path,
    )

    error = runner._verify_output(tmp_path, started_at=0)

    assert error is not None
    assert "missing metrics coverage/freshness" in error

    report.write_text(
        report.read_text() + "\n## Metrics evidence\nRequired evidence complete: unknown\n",
        encoding="utf-8",
    )
    assert runner._verify_output(tmp_path, started_at=0) is not None

    report.write_text(
        report.read_text().replace("complete: unknown", "complete: no")
        + "| query | status | series | newest sample | freshness |\n"
        + "|---|---|---:|---|---:|\n"
        + "| scrape_targets | missing | 0 | none | n/a |\n"
        + "\n## Filed issues\n"
        + "https://github.com/colophon-group/jobseek/issues/5948\n",
        encoding="utf-8",
    )
    assert runner._verify_output(tmp_path, started_at=0) is None

    report.write_text(
        report.read_text().replace("complete: no", "complete: yes"),
        encoding="utf-8",
    )
    error = runner._verify_output(tmp_path, started_at=0)
    assert error is not None
    assert "contradicts metrics evidence completeness" in error


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
