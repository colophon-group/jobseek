"""Hetzner-local Codex runner for daily Jobseek routines.

The company-request governor owns issue selection and GitHub claim comments.
Daily routines are simpler: systemd owns cadence, this runner gates the start
against host health and Codex usage, creates a fresh worktree, and records the
``codex exec --json`` trace in the shared ledger.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from src.workspace.codex_runner import (
    CompanyResolverGovernor,
    RunnerConfig,
    RunnerLedger,
    SchedulerDecision,
    _safe_env,
    _terminate_process_group,
    parse_codex_usage_jsonl,
)


@dataclass(frozen=True)
class RoutineSpec:
    name: str
    active_slot: str
    worktree_prefix: str
    description: str


ROUTINES = {
    "error-review": RoutineSpec(
        name="error-review",
        active_slot="daily-error-review",
        worktree_prefix="daily-error-review",
        description="daily crawler error review",
    ),
    "annotations": RoutineSpec(
        name="annotations",
        active_slot="daily-annotations",
        worktree_prefix="daily-annotations",
        description="daily labelled-postings annotation run",
    ),
    "label-daily": RoutineSpec(
        name="annotations",
        active_slot="daily-annotations",
        worktree_prefix="daily-annotations",
        description="daily labelled-postings annotation run",
    ),
}


@dataclass(frozen=True)
class DailyRunResult:
    run_id: str
    routine: str
    run_date: str
    state: str
    exit_code: int | None = None
    trace_path: Path | None = None
    stderr_path: Path | None = None
    worktree_path: Path | None = None
    error: str | None = None


def utc_run_date(value: str | None = None) -> str:
    if not value or value == "today":
        return datetime.now(tz=UTC).strftime("%Y-%m-%d")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d")


def build_daily_prompt(
    routine: str,
    *,
    run_date: str,
    count: int,
    error_bundle: Path | None = None,
) -> str:
    if routine == "error-review":
        bundle_text = (
            f"\nA root-owned preflight collector has already written a redacted "
            f"read-only evidence bundle at:\n\n    {error_bundle}\n\n"
            "Use that bundle as the primary host/log input. Do not attempt to "
            "read Docker directly, use sudo, mutate host state, or inspect "
            "production env files. If the bundle is missing or insufficient, "
            "write the gap in the report and fail closed rather than widening "
            "host access.\n"
            if error_bundle
            else ""
        )
        return f"""You are running Jobseek's daily crawler error review for {run_date}.

Read and follow the repo skill at:

    .agents/skills/jobseek-error-review/SKILL.md

Also read the routine source doc:

    docs/14-error-review-routine.md
{bundle_text}
Run exactly one daily error-review pass. Use an explicit 24-hour UTC window,
write or append the dated report, deduplicate against GitHub issues, and file
or update GitHub issues only when the skill's criteria are met.

Hard limits:
- Do not process company-request issues.
- Do not push code changes.
- Do not run destructive host, Docker, database, or filesystem commands.
- Do not print, copy, upload, or commit secrets.
- Stop after the report and any justified GitHub issue updates are complete.
"""

    return f"""You are running Jobseek's daily labelled-postings annotation routine for {run_date}.

Read and follow the repo skill at:

    .agents/skills/jobseek-label-daily/SKILL.md

Also read the routine source doc:

    docs/15-data-sampling-routine.md

Run exactly one annotation run for UTC date {run_date}. Target exactly {count}
accepted records unless the remote HuggingFace dataset already has {count}
accepted rows for this date. Use the committed Codex agent contracts under
`.codex/agents/` and `.agents/labeller/` for normalizer, splitter, and
extractor calls.

Operational details:
- Run deterministic labeller commands from `apps/crawler` with `uv run labeller`.
- Use `LABELLER_DATA_ROOT` when it is present in the environment.
- The labeller CLI may load DB settings through `JOBSEEK_LABELLER_ENV_FILE`;
  do not print or open that file.
- Upload only accepted records after schema validation, QA validation, and a
  targeted quality review.

Hard limits:
- Do not process company-request issues.
- Do not add provider SDK calls or call model APIs from repository code.
- Do not print, copy, upload, or commit secrets.
- Stop after upload verification for {run_date} is complete, or after a clear
  fail-closed report if prerequisites are missing.
"""


class DailyRoutineRunner:
    def __init__(
        self,
        config: RunnerConfig,
        *,
        routine: str,
        run_date: str,
        count: int = 10,
        error_bundle: Path | None = None,
        ledger: RunnerLedger | None = None,
    ):
        if routine not in ROUTINES:
            raise ValueError(f"unknown routine: {routine}")
        self.spec = ROUTINES[routine]
        self.run_date = run_date
        self.count = count
        self.error_bundle = error_bundle
        self.config = RunnerConfig(
            root=config.root,
            repo_dir=config.repo_dir,
            worktrees_dir=config.worktrees_dir,
            traces_dir=config.traces_dir,
            logs_dir=config.logs_dir,
            state_dir=config.state_dir,
            ledger_path=config.ledger_path,
            codex_args=config.codex_args,
            max_runtime_s=config.max_runtime_s,
            kill_grace_s=config.kill_grace_s,
            dry_run=config.dry_run,
            cleanup_success_worktree=config.cleanup_success_worktree,
            label=config.label,
            active_slot=self.spec.active_slot,
            max_runs_per_5h=config.max_runs_per_5h,
            conservative_runs_per_5h=config.conservative_runs_per_5h,
            fast_weekly_remaining_percent=config.fast_weekly_remaining_percent,
            fast_min_start_interval_s=config.fast_min_start_interval_s,
            conservative_min_start_interval_s=config.conservative_min_start_interval_s,
            min_five_hour_remaining_percent=config.min_five_hour_remaining_percent,
            min_weekly_remaining_percent=config.min_weekly_remaining_percent,
            min_disk_free_gib=config.min_disk_free_gib,
            min_mem_available_gib=config.min_mem_available_gib,
            max_load_per_cpu=config.max_load_per_cpu,
            usage_probe_path=config.usage_probe_path,
            lease_timeout_s=config.lease_timeout_s,
        ).resolved()
        ledger_path = self.config.ledger_path or self.config.root / "state/ledger.sqlite"
        self.ledger = ledger or RunnerLedger(ledger_path)

    def run_once(self) -> DailyRunResult:
        if self.ledger.completed_run_with_prefix(
            active_slot=self.config.active_slot,
            run_id_prefix=self._run_prefix(),
        ):
            return DailyRunResult(
                run_id="",
                routine=self.spec.name,
                run_date=self.run_date,
                state="skipped",
                error="daily routine already completed for date",
            )

        decision = self.should_start()
        if not decision.should_run:
            return DailyRunResult(
                run_id="",
                routine=self.spec.name,
                run_date=self.run_date,
                state="skipped",
                error=decision.reason,
            )

        run_id = self._new_run_id()
        lease_expires_at = int(time.time()) + self.config.lease_timeout_s
        if not self.ledger.acquire(
            run_id=run_id,
            issue=None,
            active_slot=self.config.active_slot,
            lease_expires_at=lease_expires_at,
        ):
            return DailyRunResult(
                run_id="",
                routine=self.spec.name,
                run_date=self.run_date,
                state="skipped",
                error="active routine already leased",
            )

        if self.config.dry_run:
            self.ledger.finish(run_id, "skipped", error="dry run")
            return DailyRunResult(
                run_id=run_id,
                routine=self.spec.name,
                run_date=self.run_date,
                state="skipped",
                error="dry run",
            )

        try:
            return self._execute(run_id)
        except Exception as exc:  # noqa: BLE001 - final guard for leased routines
            self.ledger.finish(run_id, "failed", error=str(exc))
            return DailyRunResult(
                run_id=run_id,
                routine=self.spec.name,
                run_date=self.run_date,
                state="failed",
                error=str(exc),
            )

    def should_start(self) -> SchedulerDecision:
        governor = CompanyResolverGovernor(self.config, ledger=self.ledger)
        return governor.should_start()

    def _execute(self, run_id: str) -> DailyRunResult:
        cfg = self.config
        cfg.traces_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        cfg.logs_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        cfg.worktrees_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        trace_path = cfg.traces_dir / f"{run_id}.jsonl"  # type: ignore[operator]
        stderr_path = cfg.logs_dir / f"{run_id}.stderr.log"  # type: ignore[operator]
        worktree = self._prepare_worktree(run_id)

        env = _safe_env()
        env["CODEX_EXEC_JSONL"] = str(trace_path)
        env["JOBSEEK_CODEX_RUN_ID"] = run_id
        env["JOBSEEK_CODEX_ROUTINE"] = self.spec.name
        env["JOBSEEK_CODEX_RUN_DATE"] = self.run_date
        env["LABELLER_DATA_ROOT"] = os.environ.get(
            "LABELLER_DATA_ROOT",
            str(cfg.root / "data" / "postings-labelled"),
        )
        for key in ("JOBSEEK_LABELLER_ENV_FILE", "JOBSEEK_ERROR_REVIEW_BUNDLE"):
            if os.environ.get(key):
                env[key] = os.environ[key]

        prompt = build_daily_prompt(
            self.spec.name,
            run_date=self.run_date,
            count=self.count,
            error_bundle=self.error_bundle,
        )
        cmd = [*cfg.codex_args, prompt]
        started_at = time.time()
        with trace_path.open("w") as stdout, stderr_path.open("w") as stderr:
            proc = subprocess.Popen(
                cmd,
                cwd=worktree,
                env=env,
                stdout=stdout,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
            now = int(time.time())
            self.ledger.update(
                run_id,
                state="running",
                pid=proc.pid,
                trace_path=str(trace_path),
                stderr_path=str(stderr_path),
                worktree_path=str(worktree),
                started_at=now,
                heartbeat_at=now,
                lease_expires_at=now + cfg.lease_timeout_s,
            )
            try:
                exit_code = proc.wait(timeout=cfg.max_runtime_s)
            except subprocess.TimeoutExpired:
                _terminate_process_group(proc, cfg.kill_grace_s)
                summary = parse_codex_usage_jsonl(trace_path)
                self.ledger.ingest_trace_once(run_id, trace_path, summary)
                self.ledger.finish(run_id, "timeout", error="codex runtime exceeded")
                return DailyRunResult(
                    run_id=run_id,
                    routine=self.spec.name,
                    run_date=self.run_date,
                    state="timeout",
                    trace_path=trace_path,
                    stderr_path=stderr_path,
                    worktree_path=worktree,
                    error="codex runtime exceeded",
                )

        summary = parse_codex_usage_jsonl(trace_path)
        self.ledger.ingest_trace_once(run_id, trace_path, summary)
        output_error = None
        if exit_code == 0:
            output_error = self._verify_output(worktree, started_at=started_at)
        state = "completed" if exit_code == 0 and output_error is None else "failed"
        error = output_error if output_error else (None if exit_code == 0 else f"exit {exit_code}")
        self.ledger.finish(run_id, state, error=error)
        if state == "completed" and cfg.cleanup_success_worktree:
            self._cleanup_worktree(worktree)
        return DailyRunResult(
            run_id=run_id,
            routine=self.spec.name,
            run_date=self.run_date,
            state=state,
            exit_code=exit_code,
            trace_path=trace_path,
            stderr_path=stderr_path,
            worktree_path=worktree,
            error=error,
        )

    def _verify_output(self, worktree: Path, *, started_at: float) -> str | None:
        if self.spec.name == "error-review":
            report = (
                Path.home() / "dev" / "claude" / "review-jobseek-errors" / f"{self.run_date}.md"
            )
            if not report.exists():
                return f"expected error-review report missing: {report}"
            if report.stat().st_mtime + 60 < started_at:
                return f"expected error-review report was not updated: {report}"
            text = report.read_text(errors="replace")
            if f"Daily error review - {self.run_date}" not in text or "Window:" not in text:
                return f"error-review report missing required header/window: {report}"
            return None
        if self.spec.name == "annotations":
            return self._verify_annotation_upload(worktree)
        return None

    def _verify_annotation_upload(self, worktree: Path) -> str | None:
        script = f"""
from __future__ import annotations
import sys
from pathlib import Path
from huggingface_hub import HfApi
from huggingface_hub.utils import get_token

repo = "viktoroo/jobseek-postings-labelled"
filename = "data/{self.run_date}.jsonl"
token = get_token()
if not token:
    print("missing HuggingFace token", file=sys.stderr)
    raise SystemExit(2)
api = HfApi(token=token)
try:
    local = api.hf_hub_download(repo_id=repo, repo_type="dataset", filename=filename)
except Exception as exc:
    print(f"could not download {{filename}}: {{type(exc).__name__}}", file=sys.stderr)
    raise SystemExit(3)
rows = sum(1 for line in Path(local).read_text().splitlines() if line.strip())
print(f"{{filename}} rows={{rows}}")
raise SystemExit(0 if rows == {self.count} else 4)
"""
        env = _safe_env()
        env["LABELLER_DATA_ROOT"] = os.environ.get(
            "LABELLER_DATA_ROOT",
            str(self.config.root / "data" / "postings-labelled"),
        )
        result = subprocess.run(
            ["uv", "run", "python", "-c", script],
            cwd=worktree / "apps" / "crawler",
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode == 0:
            return None
        detail_lines = (result.stderr or result.stdout or "").strip().splitlines()
        detail = detail_lines[-1] if detail_lines else f"exit {result.returncode}"
        return f"annotation upload verification failed: {detail}"

    def _prepare_worktree(self, run_id: str) -> Path:
        cfg = self.config
        repo = cfg.repo_dir
        worktree = cfg.worktrees_dir / f"{self.spec.worktree_prefix}-{self.run_date}-{run_id}"  # type: ignore[operator]
        subprocess.run(["git", "fetch", "origin"], cwd=repo, check=True)
        subprocess.run(["git", "worktree", "prune"], cwd=repo, check=True)
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), "origin/main"],
            cwd=repo,
            check=True,
        )
        return worktree

    def _cleanup_worktree(self, worktree: Path) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=self.config.repo_dir,
            check=False,
        )
        shutil.rmtree(worktree, ignore_errors=True)

    def _run_prefix(self) -> str:
        return f"{self.spec.active_slot}-{self.run_date}-"

    def _new_run_id(self) -> str:
        return f"{self._run_prefix()}{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one daily Codex routine.")
    parser.add_argument(
        "--routine",
        required=True,
        choices=sorted(ROUTINES),
        help="Daily routine to run.",
    )
    parser.add_argument("--date", default=os.environ.get("JOBSEEK_CODEX_RUN_DATE", "today"))
    parser.add_argument(
        "--count",
        type=int,
        default=int(os.environ.get("JOBSEEK_CODEX_DAILY_COUNT", "10")),
        help="Accepted annotation target for the annotations routine.",
    )
    parser.add_argument(
        "--error-bundle",
        type=Path,
        default=(
            Path(os.environ["JOBSEEK_ERROR_REVIEW_BUNDLE"])
            if os.environ.get("JOBSEEK_ERROR_REVIEW_BUNDLE")
            else None
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    config = RunnerConfig.from_env()
    runner = DailyRoutineRunner(
        config,
        routine=args.routine,
        run_date=utc_run_date(args.date),
        count=args.count,
        error_bundle=args.error_bundle,
    )
    result = runner.run_once()
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "routine": result.routine,
                "run_date": result.run_date,
                "state": result.state,
                "exit_code": result.exit_code,
                "trace_path": str(result.trace_path) if result.trace_path else None,
                "error": result.error,
            },
            sort_keys=True,
        )
    )
    return 0 if result.state in {"completed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
