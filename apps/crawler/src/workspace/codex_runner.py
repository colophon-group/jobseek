"""Hetzner-local Codex runner for company-request resolution.

The governor is intentionally small and stateful:

* it admits at most one active run through a SQLite ledger;
* it owns exactly one GitHub issue claim comment and deletes only that comment;
* it launches one noninteractive Codex process with one issue-specific prompt;
* it stores ``codex exec --json`` stdout as the canonical run trace.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.workspace.safe_cleanup import (
    claim_child_at,
    open_absolute_directory_no_follow,
    open_child_directory_no_follow,
    recover_pending_rmtree_claims,
    rmtree_child_at,
    unlink_child_at,
    unlink_claimed_child_at,
    validate_child_name,
)

ACTIVE_STATES = ("claimed", "running")
# The legacy states remain valid because the same ledger is also consumed by
# the daily routine runner.  Company resolver runs use the explicit outcome
# states below so a closed issue cannot be mistaken for a successful run.
TERMINAL_STATES = (
    "completed",
    "failed",
    "timeout",
    "submitted",
    "rejected",
    "escalated",
    "retryable",
    "interrupted",
    "skipped",
)
RESOLVED_OUTCOMES = ("submitted", "rejected", "escalated")
RETRY_OUTCOMES = ("retryable", "interrupted")
_AUTOMATION_RUN_SQL = (
    "(r.run_id LIKE 'issue-%' OR r.run_id LIKE 'daily-error-review-%' "
    "OR r.run_id LIKE 'daily-annotations-%')"
)
DEFAULT_ROOT = Path("/srv/jobseek-codex")
DEFAULT_RUNTIME_S = 90 * 60
DEFAULT_KILL_GRACE_S = 20
DEFAULT_RETRY_BACKOFF_S = 15 * 60
DEFAULT_MAX_RETRY_BACKOFF_S = 6 * 60 * 60
DEFAULT_CLAIM_MARKER = "<!-- ws-claim -->"
FIVE_HOURS_S = 5 * 60 * 60
ONE_WEEK_S = 7 * 24 * 60 * 60
UNKNOWN_USAGE_RETRY_S = 30 * 60
_TERMINAL_RECEIPT_MAX_BYTES = 64 * 1024
_TERMINAL_RECEIPT_CLAIM_PREFIX = ".jobseek-terminal-receipt-v1-"
DEFAULT_CODEX_ARGS = (
    "codex",
    "exec",
    "--json",
    "--dangerously-bypass-approvals-and-sandbox",
)
DEFAULT_CODEX_MODEL = "gpt-5.6-sol"
DEFAULT_CODEX_REASONING_EFFORT = "high"


class GitHubStateError(RuntimeError):
    """Raised when GitHub state needed for safe coordination is unknown."""


@dataclass(frozen=True)
class UsageWindow:
    name: str
    remaining_percent: float | None = None
    used_percent: float | None = None
    reset_in_seconds: int | None = None
    reset_at: int | None = None


@dataclass(frozen=True)
class UsageProbeResult:
    ok: bool
    windows: tuple[UsageWindow, ...] = ()
    error: str | None = None
    status: int | None = None


@dataclass(frozen=True)
class UsageSummary:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    events_with_usage: int = 0


@dataclass(frozen=True)
class HostHealth:
    ok: bool
    reason: str | None = None
    warning: str | None = None
    disk_free_bytes: int | None = None
    disk_total_bytes: int | None = None


@dataclass(frozen=True)
class SchedulerDecision:
    should_run: bool
    reason: str
    recent_limit: int
    recent_runs: int
    usage: UsageProbeResult | None = None
    retry_after_s: int | None = None
    pacing_interval_s: int | None = None
    last_started_at: int | None = None


@dataclass(frozen=True)
class ClaimComment:
    id: int
    body: str
    created_at: str | None = None


@dataclass(frozen=True)
class IssueResolution:
    state: str
    outcome: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class RunnerConfig:
    root: Path = DEFAULT_ROOT
    repo_dir: Path | None = None
    worktrees_dir: Path | None = None
    managed_repo_dir: Path | None = None
    managed_worktrees_dir: Path | None = None
    traces_dir: Path | None = None
    logs_dir: Path | None = None
    state_dir: Path | None = None
    ledger_path: Path | None = None
    codex_args: tuple[str, ...] = DEFAULT_CODEX_ARGS
    codex_model: str | None = DEFAULT_CODEX_MODEL
    codex_reasoning_effort: str | None = DEFAULT_CODEX_REASONING_EFFORT
    max_runtime_s: int = DEFAULT_RUNTIME_S
    kill_grace_s: int = DEFAULT_KILL_GRACE_S
    dry_run: bool = False
    cleanup_success_worktree: bool = True
    label: str = "company-request"
    active_slot: str = "company-resolver"
    max_runs_per_5h: int = 50
    conservative_runs_per_5h: int = 5
    fast_weekly_remaining_percent: float = 50.0
    fast_min_start_interval_s: int = 6 * 60
    conservative_min_start_interval_s: int = 60 * 60
    min_five_hour_remaining_percent: float = 20.0
    min_weekly_remaining_percent: float = 20.0
    min_disk_free_gib: float = 5.0
    disk_alert_margin_gib: float = 2.0
    max_quarantine_runs: int = 50
    max_quarantine_gib: float = 2.0
    max_retained_session_files: int = 500
    max_retained_session_gib: float = 2.0
    max_unlinked_session_age_days: float = 7.0
    max_terminal_worktrees: int = 3
    max_terminal_worktree_gib: float = 2.0
    min_mem_available_gib: float = 2.0
    max_load_per_cpu: float = 2.0
    usage_probe_path: Path | None = None
    lease_timeout_s: int = 4 * 60 * 60
    retry_backoff_s: int = DEFAULT_RETRY_BACKOFF_S
    max_retry_backoff_s: int = DEFAULT_MAX_RETRY_BACKOFF_S
    trace_export_enabled: bool = False
    trace_cleanup_enabled: bool = True
    trace_hf_repo: str = "viktoroo/jobseek-agent-traces"
    trace_hf_prefix: str = "training-bundles/v2"
    trace_retry_limit: int = 3
    codex_home: Path | None = None

    def resolved(self) -> RunnerConfig:
        root = self.root
        repo_dir = self.repo_dir or root / "repo"
        managed_root = Path.home() / ".jobseek" if root == DEFAULT_ROOT else root / "managed"
        codex_home = Path.home() / ".codex" if root == DEFAULT_ROOT else root / "codex-home"
        return RunnerConfig(
            root=root,
            repo_dir=repo_dir,
            worktrees_dir=self.worktrees_dir or root / "worktrees",
            managed_repo_dir=self.managed_repo_dir or managed_root / "repo",
            managed_worktrees_dir=self.managed_worktrees_dir or managed_root / "worktrees",
            traces_dir=self.traces_dir or root / "traces",
            logs_dir=self.logs_dir or root / "logs",
            state_dir=self.state_dir or root / "state",
            ledger_path=self.ledger_path or root / "state" / "ledger.sqlite",
            codex_args=self.codex_args,
            codex_model=self.codex_model,
            codex_reasoning_effort=self.codex_reasoning_effort,
            max_runtime_s=self.max_runtime_s,
            kill_grace_s=self.kill_grace_s,
            dry_run=self.dry_run,
            cleanup_success_worktree=self.cleanup_success_worktree,
            label=self.label,
            active_slot=self.active_slot,
            max_runs_per_5h=self.max_runs_per_5h,
            conservative_runs_per_5h=self.conservative_runs_per_5h,
            fast_weekly_remaining_percent=self.fast_weekly_remaining_percent,
            fast_min_start_interval_s=self.fast_min_start_interval_s,
            conservative_min_start_interval_s=self.conservative_min_start_interval_s,
            min_five_hour_remaining_percent=self.min_five_hour_remaining_percent,
            min_weekly_remaining_percent=self.min_weekly_remaining_percent,
            min_disk_free_gib=self.min_disk_free_gib,
            disk_alert_margin_gib=self.disk_alert_margin_gib,
            max_quarantine_runs=self.max_quarantine_runs,
            max_quarantine_gib=self.max_quarantine_gib,
            max_retained_session_files=self.max_retained_session_files,
            max_retained_session_gib=self.max_retained_session_gib,
            max_unlinked_session_age_days=self.max_unlinked_session_age_days,
            max_terminal_worktrees=self.max_terminal_worktrees,
            max_terminal_worktree_gib=self.max_terminal_worktree_gib,
            min_mem_available_gib=self.min_mem_available_gib,
            max_load_per_cpu=self.max_load_per_cpu,
            usage_probe_path=self.usage_probe_path or repo_dir / "scripts" / "codex-usage-probe.py",
            lease_timeout_s=self.lease_timeout_s,
            retry_backoff_s=self.retry_backoff_s,
            max_retry_backoff_s=self.max_retry_backoff_s,
            trace_export_enabled=self.trace_export_enabled,
            trace_cleanup_enabled=self.trace_cleanup_enabled,
            trace_hf_repo=self.trace_hf_repo,
            trace_hf_prefix=self.trace_hf_prefix,
            trace_retry_limit=self.trace_retry_limit,
            codex_home=self.codex_home or codex_home,
        )

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> RunnerConfig:
        env = environ if environ is not None else os.environ
        root = Path(env.get("JOBSEEK_CODEX_RUNNER_ROOT", str(DEFAULT_ROOT)))
        codex_args = tuple(
            part
            for part in env.get(
                "JOBSEEK_CODEX_ARGS",
                " ".join(DEFAULT_CODEX_ARGS),
            ).split()
            if part
        )
        return cls(
            root=root,
            repo_dir=(
                Path(env["JOBSEEK_CODEX_REPO_DIR"]) if env.get("JOBSEEK_CODEX_REPO_DIR") else None
            ),
            managed_repo_dir=(
                Path(env["JOBSEEK_CODEX_MANAGED_REPO_DIR"])
                if env.get("JOBSEEK_CODEX_MANAGED_REPO_DIR")
                else None
            ),
            managed_worktrees_dir=(
                Path(env["JOBSEEK_CODEX_MANAGED_WORKTREES_DIR"])
                if env.get("JOBSEEK_CODEX_MANAGED_WORKTREES_DIR")
                else None
            ),
            codex_model=env.get("JOBSEEK_CODEX_MODEL", DEFAULT_CODEX_MODEL) or None,
            codex_reasoning_effort=(
                env.get(
                    "JOBSEEK_CODEX_REASONING_EFFORT",
                    DEFAULT_CODEX_REASONING_EFFORT,
                )
                or None
            ),
            max_runtime_s=int(env.get("JOBSEEK_CODEX_MAX_RUNTIME_S", DEFAULT_RUNTIME_S)),
            kill_grace_s=int(env.get("JOBSEEK_CODEX_KILL_GRACE_S", DEFAULT_KILL_GRACE_S)),
            max_runs_per_5h=int(env.get("JOBSEEK_CODEX_MAX_RUNS_PER_5H", "50")),
            conservative_runs_per_5h=int(env.get("JOBSEEK_CODEX_CONSERVATIVE_RUNS_PER_5H", "5")),
            fast_weekly_remaining_percent=float(
                env.get("JOBSEEK_CODEX_FAST_WEEKLY_REMAINING_PERCENT", "50")
            ),
            fast_min_start_interval_s=int(
                env.get("JOBSEEK_CODEX_FAST_MIN_START_INTERVAL_S", "360")
            ),
            conservative_min_start_interval_s=int(
                env.get("JOBSEEK_CODEX_CONSERVATIVE_MIN_START_INTERVAL_S", "3600")
            ),
            min_five_hour_remaining_percent=float(
                env.get("JOBSEEK_CODEX_MIN_5H_REMAINING_PERCENT", "20")
            ),
            min_weekly_remaining_percent=float(
                env.get("JOBSEEK_CODEX_MIN_WEEKLY_REMAINING_PERCENT", "20")
            ),
            min_disk_free_gib=float(env.get("JOBSEEK_CODEX_MIN_DISK_FREE_GIB", "5")),
            disk_alert_margin_gib=float(env.get("JOBSEEK_CODEX_DISK_ALERT_MARGIN_GIB", "2")),
            max_quarantine_runs=int(env.get("JOBSEEK_CODEX_MAX_QUARANTINE_RUNS", "50")),
            max_quarantine_gib=float(env.get("JOBSEEK_CODEX_MAX_QUARANTINE_GIB", "2")),
            max_retained_session_files=int(
                env.get("JOBSEEK_CODEX_MAX_RETAINED_SESSION_FILES", "500")
            ),
            max_retained_session_gib=float(env.get("JOBSEEK_CODEX_MAX_RETAINED_SESSION_GIB", "2")),
            max_unlinked_session_age_days=float(
                env.get("JOBSEEK_CODEX_MAX_UNLINKED_SESSION_AGE_DAYS", "7")
            ),
            max_terminal_worktrees=int(env.get("JOBSEEK_CODEX_MAX_TERMINAL_WORKTREES", "3")),
            max_terminal_worktree_gib=float(
                env.get("JOBSEEK_CODEX_MAX_TERMINAL_WORKTREE_GIB", "2")
            ),
            min_mem_available_gib=float(env.get("JOBSEEK_CODEX_MIN_MEM_AVAILABLE_GIB", "2")),
            max_load_per_cpu=float(env.get("JOBSEEK_CODEX_MAX_LOAD_PER_CPU", "2")),
            lease_timeout_s=int(env.get("JOBSEEK_CODEX_LEASE_TIMEOUT_S", str(4 * 60 * 60))),
            retry_backoff_s=int(
                env.get("JOBSEEK_CODEX_RETRY_BACKOFF_S", str(DEFAULT_RETRY_BACKOFF_S))
            ),
            max_retry_backoff_s=int(
                env.get(
                    "JOBSEEK_CODEX_MAX_RETRY_BACKOFF_S",
                    str(DEFAULT_MAX_RETRY_BACKOFF_S),
                )
            ),
            dry_run=env.get("JOBSEEK_CODEX_DRY_RUN", "").lower() in {"1", "true", "yes"},
            cleanup_success_worktree=env.get("JOBSEEK_CODEX_KEEP_SUCCESS_WORKTREE", "").lower()
            not in {"1", "true", "yes"},
            trace_export_enabled=env.get("JOBSEEK_CODEX_TRACE_EXPORT_ENABLED", "true").lower()
            in {"1", "true", "yes"},
            trace_cleanup_enabled=env.get("JOBSEEK_CODEX_TRACE_CLEANUP_ENABLED", "true").lower()
            in {"1", "true", "yes"},
            trace_hf_repo=env.get("JOBSEEK_CODEX_TRACE_HF_REPO", "viktoroo/jobseek-agent-traces"),
            trace_hf_prefix=env.get("JOBSEEK_CODEX_TRACE_HF_PREFIX", "training-bundles/v2"),
            trace_retry_limit=int(env.get("JOBSEEK_CODEX_TRACE_RETRY_LIMIT", "3")),
            codex_home=Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else None,
            codex_args=codex_args,
        ).resolved()


class RunnerLedger:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    issue INTEGER,
                    active_slot TEXT,
                    state TEXT NOT NULL,
                    claim_comment_id INTEGER,
                    pid INTEGER,
                    trace_path TEXT,
                    stderr_path TEXT,
                    worktree_path TEXT,
                    pr_url TEXT,
                    pr_number INTEGER,
                    branch TEXT,
                    error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    started_at INTEGER,
                    heartbeat_at INTEGER,
                    lease_expires_at INTEGER,
                    completed_at INTEGER,
                    outcome_reason TEXT,
                    retry_after_at INTEGER,
                    attempt INTEGER NOT NULL DEFAULT 1
                );
                CREATE UNIQUE INDEX IF NOT EXISTS runs_one_active_slot
                    ON runs(active_slot)
                    WHERE state IN ('claimed', 'running') AND active_slot IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS runs_one_active_issue
                    ON runs(issue)
                    WHERE state IN ('claimed', 'running') AND issue IS NOT NULL;
                CREATE TABLE IF NOT EXISTS run_worktree_cleanup_fences (
                    run_id TEXT NOT NULL,
                    worktree_path TEXT NOT NULL,
                    token TEXT NOT NULL UNIQUE,
                    claimed_at INTEGER NOT NULL,
                    removed_at INTEGER,
                    PRIMARY KEY(run_id, worktree_path),
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS run_worktree_cleanup_fences_run
                    ON run_worktree_cleanup_fences(run_id);
                CREATE TABLE IF NOT EXISTS trace_ingestions (
                    trace_path TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cached_input_tokens INTEGER NOT NULL,
                    events_with_usage INTEGER NOT NULL,
                    ingested_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS codex_session_links (
                    session_path TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    parent_thread_id TEXT,
                    role TEXT NOT NULL,
                    is_root INTEGER NOT NULL,
                    source_bytes INTEGER NOT NULL,
                    linked_at INTEGER NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES runs(run_id)
                );
                CREATE INDEX IF NOT EXISTS codex_session_links_run
                    ON codex_session_links(run_id);
                CREATE TABLE IF NOT EXISTS usage_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at INTEGER NOT NULL,
                    active_slot TEXT NOT NULL,
                    decision_reason TEXT NOT NULL,
                    should_run INTEGER NOT NULL,
                    recent_limit INTEGER NOT NULL,
                    recent_runs INTEGER NOT NULL,
                    usage_ok INTEGER,
                    usage_error TEXT,
                    usage_status INTEGER,
                    window_name TEXT,
                    remaining_percent REAL,
                    used_percent REAL,
                    reset_in_seconds INTEGER,
                    reset_at INTEGER,
                    pacing_interval_s INTEGER,
                    last_started_at INTEGER,
                    retry_after_s INTEGER
                );
                CREATE INDEX IF NOT EXISTS usage_snapshots_slot_time
                    ON usage_snapshots(active_slot, observed_at);
                CREATE TABLE IF NOT EXISTS trace_bundle_export_attempts (
                    run_id TEXT PRIMARY KEY,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    quality_tier TEXT,
                    remote_dir TEXT,
                    error TEXT,
                    retained_bytes INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worktree_reconciliation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at INTEGER NOT NULL,
                    worktree_path TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'runner',
                    run_id TEXT,
                    issue INTEGER,
                    state TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    action TEXT NOT NULL,
                    bytes_before INTEGER NOT NULL,
                    dirty_entries INTEGER NOT NULL,
                    remote_proof_json TEXT,
                    archive_path TEXT,
                    archive_sha256 TEXT,
                    reclaimed_bytes INTEGER NOT NULL DEFAULT 0,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS worktree_reconciliation_path_time
                    ON worktree_reconciliation_events(worktree_path, observed_at);
                CREATE TABLE IF NOT EXISTS worktree_archive_retention_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    cursor_name TEXT,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            self._ensure_columns(conn)

    def _ensure_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        for name, ddl in {
            "pr_number": "ALTER TABLE runs ADD COLUMN pr_number INTEGER",
            "heartbeat_at": "ALTER TABLE runs ADD COLUMN heartbeat_at INTEGER",
            "lease_expires_at": "ALTER TABLE runs ADD COLUMN lease_expires_at INTEGER",
            "outcome_reason": "ALTER TABLE runs ADD COLUMN outcome_reason TEXT",
            "retry_after_at": "ALTER TABLE runs ADD COLUMN retry_after_at INTEGER",
            "attempt": "ALTER TABLE runs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 1",
        }.items():
            if name not in existing:
                conn.execute(ddl)
        snapshot_existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(usage_snapshots)").fetchall()
        }
        for name, ddl in {
            "pacing_interval_s": "ALTER TABLE usage_snapshots ADD COLUMN pacing_interval_s INTEGER",
            "last_started_at": "ALTER TABLE usage_snapshots ADD COLUMN last_started_at INTEGER",
            "retry_after_s": "ALTER TABLE usage_snapshots ADD COLUMN retry_after_s INTEGER",
        }.items():
            if name not in snapshot_existing:
                conn.execute(ddl)
        attempt_existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(trace_bundle_export_attempts)").fetchall()
        }
        if "retained_bytes" not in attempt_existing:
            conn.execute(
                "ALTER TABLE trace_bundle_export_attempts "
                "ADD COLUMN retained_bytes INTEGER NOT NULL DEFAULT 0"
            )
        reconciliation_existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(worktree_reconciliation_events)").fetchall()
        }
        if "source" not in reconciliation_existing:
            conn.execute(
                "ALTER TABLE worktree_reconciliation_events "
                "ADD COLUMN source TEXT NOT NULL DEFAULT 'runner'"
            )

    def acquire(
        self,
        *,
        run_id: str,
        issue: int | None,
        active_slot: str,
        lease_expires_at: int | None = None,
        attempt: int = 1,
    ) -> bool:
        now = int(time.time())
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, issue, active_slot, state, created_at,
                        updated_at, heartbeat_at, lease_expires_at, attempt
                    ) VALUES (?, ?, ?, 'claimed', ?, ?, ?, ?, ?)
                    """,
                    (run_id, issue, active_slot, now, now, now, lease_expires_at, attempt),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def update(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = int(time.time())
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [*fields.values(), run_id]
        with self._connect() as conn:
            updated = conn.execute(
                f"""
                UPDATE runs
                SET {assignments}
                WHERE run_id = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM run_worktree_cleanup_fences AS fence
                      WHERE fence.run_id = runs.run_id
                  )
                """,
                values,
            )
            if updated.rowcount == 0:
                fence = conn.execute(
                    "SELECT 1 FROM run_worktree_cleanup_fences WHERE run_id = ? LIMIT 1",
                    (run_id,),
                ).fetchone()
                if fence is not None:
                    raise RuntimeError(
                        f"run {run_id} is cleanup-fenced; refusing a late ledger update"
                    )

    def finish(
        self,
        run_id: str,
        state: str,
        *,
        error: str | None = None,
        outcome_reason: str | None = None,
        retry_after_at: int | None = None,
    ) -> None:
        if state not in TERMINAL_STATES:
            raise ValueError(f"invalid terminal state: {state}")
        self.update(
            run_id,
            state=state,
            completed_at=int(time.time()),
            error=error,
            outcome_reason=outcome_reason,
            retry_after_at=retry_after_at,
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def worktree_runs(self) -> list[dict[str, Any]]:
        """Return every ledger run with a worktree and its trace disposition."""
        with self._connect() as conn:
            export_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trace_bundle_exports'"
            ).fetchone()
            export_join = (
                "LEFT JOIN trace_bundle_exports AS e ON e.run_id = r.run_id" if export_table else ""
            )
            verified_select = "e.run_id IS NOT NULL" if export_table else "0"
            rows = conn.execute(
                f"""
                SELECT r.*, a.status AS export_status,
                       {verified_select} AS trace_verified
                FROM runs AS r
                LEFT JOIN trace_bundle_export_attempts AS a ON a.run_id = r.run_id
                {export_join}
                WHERE r.worktree_path IS NOT NULL
                ORDER BY r.updated_at, r.run_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def _worktree_execution_lock_path(self) -> Path:
        lock_dir = self.path.parent / "worktree-execution-leases"
        lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_dir_stat = lock_dir.lstat()
        if (
            stat.S_ISLNK(lock_dir_stat.st_mode)
            or not stat.S_ISDIR(lock_dir_stat.st_mode)
            or lock_dir_stat.st_uid != os.geteuid()
        ):
            raise RuntimeError(f"unsafe worktree execution lease directory: {lock_dir}")
        os.chmod(lock_dir, 0o700)
        return lock_dir / "supported-writers.lock"

    @contextmanager
    def worktree_execution_lease(
        self,
        run_id: str,
        *,
        exclusive: bool = False,
        blocking: bool = True,
    ) -> Iterator[None]:
        """Fence the supported runner lifecycle outside the removable worktree."""
        lock_path = self._worktree_execution_lock_path()
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags, 0o600)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.geteuid():
                raise RuntimeError(f"unsafe worktree execution lease file: {lock_path}")
            os.fchmod(fd, 0o600)
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            if not blocking:
                operation |= fcntl.LOCK_NB
            try:
                fcntl.flock(fd, operation)
            except BlockingIOError as exc:
                raise RuntimeError(f"run {run_id} still holds its execution lease") from exc
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @contextmanager
    def worktree_removal_lease(
        self,
        *,
        run_id: str | None,
        worktree_path: Path,
    ) -> Iterator[None]:
        """Durably fence late run transitions before an atomic cleanup claim."""
        execution_lease = self.worktree_execution_lease(
            run_id or f"managed:{hashlib.sha256(os.fsencode(worktree_path)).hexdigest()}",
            exclusive=True,
            blocking=False,
        )
        with execution_lease:
            token: str | None = None
            if run_id is not None:
                with self._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    run = conn.execute(
                        "SELECT state FROM runs WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()
                    if run is None:
                        raise RuntimeError("cleanup fence run disappeared")
                    if str(run["state"]) not in TERMINAL_STATES:
                        raise RuntimeError("cleanup fence run became active")
                    existing = conn.execute(
                        """
                        SELECT token, removed_at
                        FROM run_worktree_cleanup_fences
                        WHERE run_id = ? AND worktree_path = ?
                        """,
                        (run_id, str(worktree_path)),
                    ).fetchone()
                    if existing is not None:
                        if existing["removed_at"] is not None:
                            raise RuntimeError("worktree cleanup was already completed")
                        token = str(existing["token"])
                    else:
                        token = uuid.uuid4().hex
                        conn.execute(
                            """
                            INSERT INTO run_worktree_cleanup_fences (
                                run_id, worktree_path, token, claimed_at
                            ) VALUES (?, ?, ?, ?)
                            """,
                            (run_id, str(worktree_path), token, int(time.time())),
                        )
            yield
            if run_id is not None and token is not None:
                with self._connect() as conn:
                    completed = conn.execute(
                        """
                        UPDATE run_worktree_cleanup_fences
                        SET removed_at = ?
                        WHERE run_id = ? AND worktree_path = ? AND token = ?
                          AND removed_at IS NULL
                        """,
                        (int(time.time()), run_id, str(worktree_path), token),
                    )
                    if completed.rowcount != 1:
                        raise RuntimeError("could not complete durable worktree cleanup fence")

    def record_worktree_reconciliation(self, **fields: Any) -> None:
        columns = (
            "observed_at",
            "worktree_path",
            "source",
            "run_id",
            "issue",
            "state",
            "classification",
            "reason",
            "action",
            "bytes_before",
            "dirty_entries",
            "remote_proof_json",
            "archive_path",
            "archive_sha256",
            "reclaimed_bytes",
            "error",
        )
        values = [fields.get(column) for column in columns]
        placeholders = ", ".join("?" for _ in columns)
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO worktree_reconciliation_events "
                f"({', '.join(columns)}) VALUES ({placeholders})",
                values,
            )

    def worktree_reconciliation_events(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM worktree_reconciliation_events ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def worktree_archive_events(self, *, worktree_path: Path) -> list[dict[str, Any]]:
        """Return durable archive evidence for one exact worktree path."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM worktree_reconciliation_events
                WHERE worktree_path = ?
                  AND archive_path IS NOT NULL
                  AND archive_sha256 IS NOT NULL
                ORDER BY id
                """,
                (str(worktree_path),),
            ).fetchall()
        return [dict(row) for row in rows]

    def worktree_archive_recovery_events(self) -> list[dict[str, Any]]:
        """Return durable identities needed to restore interrupted archive claims."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT archive_path, archive_sha256
                FROM worktree_reconciliation_events
                WHERE archive_path IS NOT NULL
                  AND archive_sha256 IS NOT NULL
                  AND action IN (
                      'archive_compaction_started',
                      'archive_retention_prune_started'
                  )
                ORDER BY id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def worktree_archive_retention_cursor(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cursor_name FROM worktree_archive_retention_state WHERE singleton = 1"
            ).fetchone()
        value = row["cursor_name"] if row else None
        return str(value) if isinstance(value, str) and value else None

    def set_worktree_archive_retention_cursor(self, cursor_name: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO worktree_archive_retention_state (
                    singleton, cursor_name, updated_at
                ) VALUES (1, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    cursor_name = excluded.cursor_name,
                    updated_at = excluded.updated_at
                """,
                (cursor_name, int(time.time())),
            )

    def count_recent_runs(self, *, active_slot: str, since: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM runs
                WHERE active_slot = ?
                  AND created_at >= ?
                  AND state NOT IN ('skipped')
                """,
                (active_slot, since),
            ).fetchone()
        return int(row["count"]) if row else 0

    def last_run_started_at(self, *, active_slot: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(started_at) AS started_at
                FROM runs
                WHERE active_slot = ?
                  AND started_at IS NOT NULL
                  AND state NOT IN ('claimed', 'skipped')
                """,
                (active_slot,),
            ).fetchone()
        value = row["started_at"] if row else None
        return int(value) if isinstance(value, int) else None

    def expired_active_runs(self, *, active_slot: str, now: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM runs
                WHERE active_slot = ?
                  AND state IN ('claimed', 'running')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (active_slot, now),
            ).fetchall()
        return [dict(row) for row in rows]

    def next_attempt(self, issue: int) -> int:
        """Return the next resolver attempt number for an issue."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MAX(attempt) AS attempt
                FROM runs
                WHERE issue = ? AND state IN ('retryable', 'interrupted')
                """,
                (issue,),
            ).fetchone()
        previous = row["attempt"] if row else None
        return int(previous or 0) + 1

    def retry_after_for_issue(self, issue: int) -> int | None:
        """Return a current backoff deadline, ignoring superseded outcomes."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT state, retry_after_at
                FROM runs
                WHERE issue = ? AND state NOT IN ('claimed', 'running', 'skipped')
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (issue,),
            ).fetchone()
        if not row or row["state"] not in RETRY_OUTCOMES:
            return None
        value = row["retry_after_at"]
        return int(value) if isinstance(value, int) else None

    def ingest_trace_once(self, run_id: str, trace_path: Path, summary: UsageSummary) -> bool:
        now = int(time.time())
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO trace_ingestions (
                        trace_path, run_id, input_tokens, output_tokens,
                        cached_input_tokens, events_with_usage, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(trace_path),
                        run_id,
                        summary.input_tokens,
                        summary.output_tokens,
                        summary.cached_input_tokens,
                        summary.events_with_usage,
                        now,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def record_codex_session_links(self, run_id: str, sessions: list[Any]) -> int:
        """Durably link one terminal automation's root and subagent sessions."""
        now = int(time.time())
        rows = []
        for source in sessions:
            entry = source.path.lstat()
            if not stat.S_ISREG(entry.st_mode):
                raise RuntimeError(f"Codex session source is unsafe: {source.path}")
            rows.append(
                (
                    str(source.path),
                    run_id,
                    str(source.thread_id),
                    source.parent_thread_id,
                    str(source.role),
                    int(source.is_root),
                    int(entry.st_size),
                    now,
                )
            )
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO codex_session_links (
                    session_path, run_id, thread_id, parent_thread_id,
                    role, is_root, source_bytes, linked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_path) DO UPDATE SET
                    run_id = excluded.run_id,
                    thread_id = excluded.thread_id,
                    parent_thread_id = excluded.parent_thread_id,
                    role = excluded.role,
                    is_root = excluded.is_root,
                    source_bytes = excluded.source_bytes,
                    linked_at = excluded.linked_at
                """,
                rows,
            )
        return len(rows)

    def codex_session_links(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM codex_session_links WHERE run_id = ? ORDER BY is_root DESC, role",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_trace_bundle_attempt(
        self,
        run_id: str,
        *,
        status: str,
        quality_tier: str | None = None,
        remote_dir: str | None = None,
        error: str | None = None,
        retained_bytes: int = 0,
    ) -> None:
        now = int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO trace_bundle_export_attempts (
                    run_id, attempts, status, quality_tier,
                    remote_dir, error, retained_bytes, last_attempt_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    attempts = trace_bundle_export_attempts.attempts + 1,
                    status = excluded.status,
                    quality_tier = excluded.quality_tier,
                    remote_dir = excluded.remote_dir,
                    error = excluded.error,
                    retained_bytes = excluded.retained_bytes,
                    last_attempt_at = excluded.last_attempt_at
                """,
                (run_id, status, quality_tier, remote_dir, error, retained_bytes, now),
            )

    def trace_quarantine_totals(self) -> tuple[int, int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS runs, COALESCE(SUM(retained_bytes), 0) AS bytes "
                "FROM trace_bundle_export_attempts WHERE status = 'quarantined'"
            ).fetchone()
        return (int(row["runs"]), int(row["bytes"])) if row else (0, 0)

    def failed_trace_bundle_exports(
        self,
        *,
        limit: int,
        include_pending_cleanup: bool,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._connect() as conn:
            export_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trace_bundle_exports'"
            ).fetchone()
            if export_table and include_pending_cleanup:
                rows = conn.execute(
                    f"""
                    SELECT r.run_id, r.issue, r.state, r.trace_path,
                           r.stderr_path, r.worktree_path, r.error
                    FROM runs AS r
                    LEFT JOIN trace_bundle_export_attempts AS a ON a.run_id = r.run_id
                    LEFT JOIN trace_bundle_exports AS e ON e.run_id = r.run_id
                    WHERE (
                          (e.run_id IS NOT NULL AND e.cleaned_at IS NULL)
                          OR (a.status = 'failed' AND e.run_id IS NULL)
                          OR (
                              a.run_id IS NULL AND e.run_id IS NULL
                              AND {_AUTOMATION_RUN_SQL}
                          )
                      )
                      AND r.state IN (
                          'completed', 'failed', 'timeout',
                          'submitted', 'rejected', 'escalated',
                          'retryable', 'interrupted'
                      )
                    ORDER BY COALESCE(a.last_attempt_at, e.verified_at), r.created_at
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""
                    SELECT r.run_id, r.issue, r.state, r.trace_path,
                           r.stderr_path, r.worktree_path, r.error
                    FROM runs AS r
                    LEFT JOIN trace_bundle_export_attempts AS a ON a.run_id = r.run_id
                    WHERE (
                          a.status = 'failed'
                          OR (a.run_id IS NULL AND {_AUTOMATION_RUN_SQL})
                      )
                      AND r.state IN (
                          'completed', 'failed', 'timeout',
                          'submitted', 'rejected', 'escalated',
                          'retryable', 'interrupted'
                      )
                    ORDER BY COALESCE(a.last_attempt_at, r.completed_at), r.created_at
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def record_usage_snapshot(
        self,
        *,
        active_slot: str,
        decision: SchedulerDecision,
        observed_at: int | None = None,
    ) -> None:
        now = observed_at or int(time.time())
        usage = decision.usage
        windows = usage.windows if usage and usage.windows else (None,)
        usage_ok = None if usage is None else int(usage.ok)
        usage_error = usage.error if usage else None
        usage_status = usage.status if usage else None
        rows = []
        for window in windows:
            rows.append(
                (
                    now,
                    active_slot,
                    decision.reason,
                    int(decision.should_run),
                    decision.recent_limit,
                    decision.recent_runs,
                    usage_ok,
                    usage_error,
                    usage_status,
                    window.name if window else None,
                    window.remaining_percent if window else None,
                    window.used_percent if window else None,
                    window.reset_in_seconds if window else None,
                    window.reset_at if window else None,
                    decision.pacing_interval_s,
                    decision.last_started_at,
                    decision.retry_after_s,
                )
            )
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO usage_snapshots (
                    observed_at, active_slot, decision_reason, should_run,
                    recent_limit, recent_runs, usage_ok, usage_error,
                    usage_status, window_name, remaining_percent, used_percent,
                    reset_in_seconds, reset_at, pacing_interval_s,
                    last_started_at, retry_after_s
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def recent_usage_snapshots(self, *, active_slot: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM usage_snapshots
                WHERE active_slot = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT ?
                """,
                (active_slot, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def completed_run_with_prefix(self, *, active_slot: str, run_id_prefix: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM runs
                WHERE active_slot = ?
                  AND state = 'completed'
                  AND run_id LIKE ?
                LIMIT 1
                """,
                (active_slot, f"{run_id_prefix}%"),
            ).fetchone()
        return row is not None


class GitHubCoordinator:
    """Thin wrapper around existing GitHub helpers plus owned claim comments."""

    def check_auth(self) -> bool:
        from src.workspace.git import check_gh_auth

        return check_gh_auth()

    def fetch_oldest_open_issue(self, label: str) -> int | None:
        from src.workspace.git import fetch_oldest_open_issue

        # The governor must see linked drafts so every existing PR blocks a
        # new cross-run resolver takeover.
        return fetch_oldest_open_issue(label=label, skip_open_prs=False)

    def list_open_issues(self, label: str) -> list[int]:
        import json

        from src.workspace import git

        result = git._run(  # noqa: SLF001
            [
                "gh",
                "issue",
                "list",
                *git._gh_repo_flag(),  # noqa: SLF001
                "--label",
                label,
                "--state",
                "open",
                "--search",
                "sort:created-asc",
                "--limit",
                "100",
                "--json",
                "number",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise GitHubStateError(f"could not list open {label!r} issues: {result.stderr}")
        try:
            issues = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise GitHubStateError(f"could not parse open {label!r} issues: {exc}") from exc
        if not isinstance(issues, list):
            raise GitHubStateError(f"unexpected issue list shape for {label!r}")
        return [
            number
            for item in issues
            if isinstance(item, dict) and isinstance((number := item.get("number")), int)
        ]

    def check_existing_prs(self, issue: int) -> list[dict[str, str]]:
        import json

        from src.workspace import git

        result = git._run(  # noqa: SLF001 - fail-closed variant of workspace helper.
            [
                "gh",
                "pr",
                "list",
                *git._gh_repo_flag(),  # noqa: SLF001
                "--state",
                "open",
                "--search",
                f"Closes #{issue}",
                "--limit",
                "100",
                "--json",
                "number,title,url,headRefName,isDraft",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise GitHubStateError(f"could not list PRs for issue #{issue}: {result.stderr}")
        try:
            prs = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise GitHubStateError(f"could not parse PR list for issue #{issue}: {exc}") from exc
        if not isinstance(prs, list):
            raise GitHubStateError(f"unexpected PR list shape for issue #{issue}")
        return [pr for pr in prs if isinstance(pr, dict)]

    def issue_resolution(
        self,
        issue: int,
        *,
        repository: str | None = None,
    ) -> IssueResolution:
        import json

        from src.workspace import git

        result = git._run(  # noqa: SLF001
            [
                "gh",
                "issue",
                "view",
                str(issue),
                *(
                    ["--repo", repository] if repository is not None else git._gh_repo_flag()  # noqa: SLF001
                ),
                "--json",
                "state,comments,closedByPullRequestsReferences",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise GitHubStateError(f"could not view issue #{issue}: {result.stderr}")
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise GitHubStateError(f"could not parse issue #{issue}: {exc}") from exc
        state = data.get("state") if isinstance(data, dict) else None
        if not isinstance(state, str):
            raise GitHubStateError(f"unexpected issue state shape for issue #{issue}")
        normalized_state = state.upper()
        if normalized_state != "CLOSED":
            return IssueResolution(state=normalized_state)

        closed_by = data.get("closedByPullRequestsReferences", [])
        if isinstance(closed_by, list) and closed_by:
            return IssueResolution(
                state=normalized_state,
                outcome="submitted",
                reason="issue was closed by a linked pull request",
            )

        comments = data.get("comments", [])
        bodies = [
            comment.get("body", "")
            for comment in comments
            if isinstance(comment, dict) and isinstance(comment.get("body"), str)
        ]
        for body in reversed(bodies):
            if body.startswith("<!-- validation-failed:"):
                marker = body.split("-->", 1)[0].removeprefix("<!-- ").strip()
                return IssueResolution(
                    state=normalized_state,
                    outcome="rejected",
                    reason=marker,
                )
            if body.startswith("<!-- resolver-outcome: escalated -->"):
                reason = _comment_field(body, "Reason") or "terminal escalation"
                return IssueResolution(
                    state=normalized_state,
                    outcome="escalated",
                    reason=reason,
                )
        # Closure by itself is deliberately not a resolver outcome.  This is
        # the guard that prevents an abandoned placeholder from looking done.
        return IssueResolution(state=normalized_state)

    def record_run_outcome(
        self,
        issue: int,
        *,
        run_id: str,
        outcome: str,
        reason: str,
        retry_after_at: int | None = None,
    ) -> None:
        from src.workspace import git

        retry = ""
        if retry_after_at is not None:
            retry_iso = datetime.fromtimestamp(
                retry_after_at,
                tz=timezone.utc,  # noqa: UP017 - Python 3.10 deployment.
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
            retry = f"\nRetry after: {retry_iso}"
        body = (
            f"<!-- resolver-run-outcome: {run_id} -->\n"
            f"Resolver outcome: **{outcome}**\n"
            f"Reason: {reason}{retry}"
        )
        try:
            git.comment_on_issue_once(
                issue,
                f"<!-- resolver-run-outcome: {run_id} -->",
                body,
            )
        except Exception as exc:
            raise GitHubStateError(f"could not record outcome for issue #{issue}: {exc}") from exc

    def list_claims(self, issue: int) -> list[ClaimComment]:
        import json

        from src.workspace import git

        result = git._run(  # noqa: SLF001 - workspace git exposes no owned-claim helper yet.
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{git._resolve_repo()}/issues/{issue}/comments",  # noqa: SLF001
            ],
            check=False,
        )
        if result.returncode != 0:
            raise GitHubStateError(f"could not list claims for issue #{issue}: {result.stderr}")
        if not result.stdout.strip():
            raise GitHubStateError(f"empty claims response for issue #{issue}")
        try:
            comments = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise GitHubStateError(f"could not parse claims for issue #{issue}: {exc}") from exc
        if not isinstance(comments, list):
            raise GitHubStateError(f"unexpected claims shape for issue #{issue}")
        if all(isinstance(page, list) for page in comments):
            comments = [comment for page in comments for comment in page]
        claims: list[ClaimComment] = []
        for comment in comments:
            body = comment.get("body", "")
            if isinstance(body, str) and body.startswith(DEFAULT_CLAIM_MARKER):
                claims.append(
                    ClaimComment(
                        id=int(comment["id"]),
                        body=body,
                        created_at=comment.get("created_at"),
                    )
                )
        return claims

    def claim_issue(self, issue: int, run_id: str) -> int | None:
        from src.workspace import git

        body = f"{DEFAULT_CLAIM_MARKER}\nWorking on it via Hetzner Codex runner\nrun: {run_id}"
        result = git._run(  # noqa: SLF001
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{git._resolve_repo()}/issues/{issue}/comments",  # noqa: SLF001
                "-f",
                f"body={body}",
                "--jq",
                ".id",
            ],
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            return int(result.stdout.strip())
        except ValueError:
            return None

    def delete_claim(self, comment_id: int) -> None:
        from src.workspace import git

        git._run(  # noqa: SLF001
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                f"repos/{git._resolve_repo()}/issues/comments/{comment_id}",  # noqa: SLF001
            ],
            check=False,
        )

    def prune_stale_runner_claims(self, label: str, *, older_than_s: int) -> None:
        import json

        from src.workspace import git

        result = git._run(  # noqa: SLF001
            [
                "gh",
                "issue",
                "list",
                *git._gh_repo_flag(),  # noqa: SLF001
                "--label",
                label,
                "--state",
                "open",
                "--limit",
                "100",
                "--json",
                "number",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise GitHubStateError(f"could not list open {label!r} issues: {result.stderr}")
        try:
            issues = json.loads(result.stdout or "[]")
        except json.JSONDecodeError as exc:
            raise GitHubStateError(f"could not parse open {label!r} issues: {exc}") from exc
        if not isinstance(issues, list):
            raise GitHubStateError(f"unexpected issue list shape for {label!r}")
        for issue in issues:
            number = issue.get("number") if isinstance(issue, dict) else None
            if not isinstance(number, int):
                continue
            for claim in self.list_claims(number):
                if _is_stale_runner_claim(claim, older_than_s=older_than_s):
                    self.delete_claim(claim.id)


@dataclass
class Admission:
    run_id: str
    issue: int
    claim_comment_id: int


@dataclass
class RunResult:
    run_id: str
    issue: int | None
    state: str
    exit_code: int | None = None
    trace_path: Path | None = None
    stderr_path: Path | None = None
    worktree_path: Path | None = None
    error: str | None = None
    trace_export_status: str | None = None
    trace_export_tier: str | None = None
    trace_export_remote_dir: str | None = None
    trace_export_error: str | None = None


def build_codex_prompt(issue: int) -> str:
    """Return the single prompt passed to ``codex exec`` for one issue."""
    return f"""You are running the Jobseek company resolver for exactly one GitHub issue.

From the repository's apps/crawler directory, run:

    uv run ws task --issue {issue}

Then follow the instructions printed by ws. Treat ws output as the runtime
source of truth. Use AGENTS.md only as supporting repository guidance.

Hard limits:
- Process only issue #{issue}.
- Do not run `ws task --pick` or select another issue.
- Do not process a second issue after completion or rejection.
- Do not push directly to main.
- `ws task fail` enters coding mode; keep following the instructions it prints.
- Stop only after `ws task complete`, `ws reject`, `ws task escalate`, or a
  linked `fix-crawler/` PR records a terminal submitted outcome.
"""


def build_codex_command(config: RunnerConfig, prompt: str) -> list[str]:
    """Build one Codex invocation with the production model policy layered on top."""
    command = list(config.codex_args)
    if config.codex_model:
        command.extend(("--model", config.codex_model))
    if config.codex_reasoning_effort:
        command.extend(
            (
                "--config",
                f"model_reasoning_effort={config.codex_reasoning_effort}",
            )
        )
    command.append(prompt)
    return command


def _safe_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = base if base is not None else os.environ
    allowed = {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "TERM",
        "SSH_AUTH_SOCK",
        "SSL_CERT_FILE",
        "CODEX_HOME",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "WS_REPO",
        "WS_REPO_URL",
        "WS_ACTIVE_SCOPE",
    }
    return {key: value for key, value in env.items() if key in allowed}


def run_usage_probe(
    script_path: Path,
    *,
    python: str = "python3",
    ca_file: Path | None = None,
    timeout_s: int = 20,
) -> UsageProbeResult:
    cmd = [python, str(script_path)]
    if ca_file:
        cmd += ["--ca-file", str(ca_file)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return UsageProbeResult(ok=False, error=str(exc))
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return UsageProbeResult(ok=False, error=f"invalid JSON: {exc}", status=result.returncode)
    if not isinstance(payload, dict):
        return UsageProbeResult(
            ok=False,
            error="unexpected probe response shape",
            status=result.returncode,
        )
    if payload.get("ok") is not True:
        reset = _int_or_none(payload.get("resets_in_seconds"))
        return UsageProbeResult(
            ok=False,
            error=str(payload.get("error") or payload.get("transport_error") or "probe failed"),
            status=payload.get("status") if isinstance(payload.get("status"), int) else None,
            windows=(
                (UsageWindow(name="rate_limit", reset_in_seconds=reset),)
                if reset is not None
                else ()
            ),
        )
    windows: list[UsageWindow] = []
    for raw in payload.get("windows", []):
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if not isinstance(name, str):
            continue
        windows.append(
            UsageWindow(
                name=name,
                remaining_percent=_float_or_none(raw.get("remaining_percent")),
                used_percent=_float_or_none(raw.get("used_percent")),
                reset_in_seconds=_int_or_none(raw.get("reset_in_seconds")),
                reset_at=_int_or_none(raw.get("reset_at")),
            )
        )
    return UsageProbeResult(ok=True, windows=tuple(windows))


def check_host_health(config: RunnerConfig) -> HostHealth:
    disk = shutil.disk_usage(config.root)
    free_gib = disk.free / (1024**3)
    if free_gib < config.min_disk_free_gib:
        return HostHealth(
            False,
            f"disk free {free_gib:.1f}GiB below threshold",
            disk_free_bytes=disk.free,
            disk_total_bytes=disk.total,
        )
    warning = None
    alert_threshold = config.min_disk_free_gib + config.disk_alert_margin_gib
    if free_gib < alert_threshold:
        warning = (
            f"disk free {free_gib:.1f}GiB is within "
            f"{config.disk_alert_margin_gib:.1f}GiB of the admission floor"
        )

    mem_available = _mem_available_gib()
    if mem_available is not None and mem_available < config.min_mem_available_gib:
        return HostHealth(
            False,
            f"memory available {mem_available:.1f}GiB below threshold",
            warning=warning,
            disk_free_bytes=disk.free,
            disk_total_bytes=disk.total,
        )

    try:
        load1, _, _ = os.getloadavg()
    except OSError:
        return HostHealth(
            True,
            warning=warning,
            disk_free_bytes=disk.free,
            disk_total_bytes=disk.total,
        )
    cpus = max(1, os.cpu_count() or 1)
    max_load = cpus * config.max_load_per_cpu
    if load1 > max_load:
        return HostHealth(
            False,
            f"load {load1:.2f} above threshold {max_load:.2f}",
            warning=warning,
            disk_free_bytes=disk.free,
            disk_total_bytes=disk.total,
        )

    if not config.dry_run and _uses_codex_cli(config.codex_args):
        missing_identity = _missing_git_identity()
        if missing_identity:
            return HostHealth(
                False,
                f"git identity missing: {', '.join(missing_identity)}",
                warning=warning,
                disk_free_bytes=disk.free,
                disk_total_bytes=disk.total,
            )
    return HostHealth(
        True,
        warning=warning,
        disk_free_bytes=disk.free,
        disk_total_bytes=disk.total,
    )


def _mem_available_gib() -> float | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    for line in meminfo.read_text(errors="replace").splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2:
                return int(parts[1]) / (1024**2)
    return None


def _uses_codex_cli(codex_args: tuple[str, ...]) -> bool:
    if not codex_args:
        return False
    return Path(codex_args[0]).name == "codex"


def _missing_git_identity() -> list[str]:
    missing = []
    for key in ("user.name", "user.email"):
        try:
            result = subprocess.run(
                ["git", "config", "--global", "--get", key],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return ["git"]
        if result.returncode != 0 or not result.stdout.strip():
            missing.append(key)
    return missing


def parse_codex_usage_jsonl(path: Path) -> UsageSummary:
    total_input = 0
    total_output = 0
    total_cached = 0
    events = 0
    if not path.exists():
        return UsageSummary()
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = _usage_for_event(event)
        if not usage:
            continue
        input_tokens = _first_int(usage, "input_tokens", "prompt_tokens")
        output_tokens = _first_int(usage, "output_tokens", "completion_tokens")
        cached_tokens = _first_int(usage, "cached_input_tokens", "cached_prompt_tokens")
        if input_tokens or output_tokens or cached_tokens:
            total_input += input_tokens
            total_output += output_tokens
            total_cached += cached_tokens
            events += 1
    return UsageSummary(
        input_tokens=total_input,
        output_tokens=total_output,
        cached_input_tokens=total_cached,
        events_with_usage=events,
    )


def _usage_for_event(event: Any) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    usage = event.get("usage")
    if isinstance(usage, dict):
        return usage
    for key in ("turn", "item", "data", "payload"):
        child = event.get(key)
        if isinstance(child, dict) and isinstance(child.get("usage"), dict):
            return child["usage"]
    return _first_usage_dict(event)


def _first_usage_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        usage = value.get("usage")
        if isinstance(usage, dict):
            return usage
        for child in value.values():
            found = _first_usage_dict(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _first_usage_dict(child)
            if found:
                return found
    return None


def _first_int(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, int):
            return value
    return 0


def _float_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _int_from_value(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


class CompanyResolverGovernor:
    def __init__(
        self,
        config: RunnerConfig,
        *,
        ledger: RunnerLedger | None = None,
        github: GitHubCoordinator | None = None,
    ):
        self.config = config.resolved()
        ledger_path = self.config.ledger_path or self.config.root / "state/ledger.sqlite"
        self.ledger = ledger or RunnerLedger(ledger_path)
        self.github = github or GitHubCoordinator()

    def admit_one(self) -> Admission | None:
        if not self.github.check_auth():
            return None
        try:
            self.github.prune_stale_runner_claims(
                self.config.label,
                older_than_s=self.config.lease_timeout_s,
            )
        except GitHubStateError:
            return None
        try:
            issues = self.github.list_open_issues(self.config.label)
        except (AttributeError, GitHubStateError):
            # Compatibility for small test/dry-run coordinators and older
            # integrators.  Production uses the full candidate list so one
            # backed-off issue never blocks the queue behind it.
            issue = self.github.fetch_oldest_open_issue(self.config.label)
            issues = [issue] if issue is not None else []
        issue = self._select_candidate(issues)
        if issue is None:
            return None

        run_id = self._new_run_id(issue)
        lease_expires_at = int(time.time()) + self.config.lease_timeout_s
        if not self.ledger.acquire(
            run_id=run_id,
            issue=issue,
            active_slot=self.config.active_slot,
            lease_expires_at=lease_expires_at,
            attempt=self.ledger.next_attempt(issue),
        ):
            return None

        claim_id = self.github.claim_issue(issue, run_id)
        if claim_id is None:
            self.ledger.finish(run_id, "skipped", error="could not post claim")
            return None
        self.ledger.update(run_id, claim_comment_id=claim_id)

        try:
            from src.workspace.git import classify_issue_prs

            pr_class = classify_issue_prs(self.github.check_existing_prs(issue))
            if pr_class != "none":
                self.github.delete_claim(claim_id)
                self.ledger.finish(
                    run_id,
                    "skipped",
                    error=f"linked PR became {pr_class} before launch",
                    outcome_reason=f"linked PR classification: {pr_class}",
                )
                return None

            claims = self.github.list_claims(issue)
            claim_ids = sorted(claim.id for claim in claims)
            if claim_ids and claim_ids[0] != claim_id:
                self.github.delete_claim(claim_id)
                self.ledger.finish(run_id, "skipped", error="lost claim race")
                return None
        except GitHubStateError as exc:
            self.github.delete_claim(claim_id)
            self.ledger.finish(run_id, "skipped", error=str(exc))
            return None

        return Admission(run_id=run_id, issue=issue, claim_comment_id=claim_id)

    def _select_candidate(self, issues: list[int]) -> int | None:
        from src.workspace.git import classify_issue_prs

        now = int(time.time())
        for issue in issues:
            retry_after = self.ledger.retry_after_for_issue(issue)
            if retry_after is not None and retry_after > now:
                continue
            try:
                pr_class = classify_issue_prs(self.github.check_existing_prs(issue))
                if pr_class != "none":
                    continue
                if self.github.list_claims(issue):
                    continue
            except GitHubStateError:
                # Unknown GitHub state is unsafe for this candidate, but does
                # not prevent us from considering a later independent issue.
                continue
            return issue
        return None

    def run_once(self) -> RunResult:
        self._retry_failed_trace_exports()
        decision = self.should_start()
        if not decision.should_run:
            return RunResult(run_id="", issue=None, state="skipped", error=decision.reason)

        admission = self.admit_one()
        if admission is None:
            return RunResult(run_id="", issue=None, state="skipped", error="no admitted issue")
        if self.config.dry_run:
            self.github.delete_claim(admission.claim_comment_id)
            self.ledger.finish(admission.run_id, "skipped", error="dry run")
            return RunResult(run_id=admission.run_id, issue=admission.issue, state="skipped")
        try:
            result = self._execute_admission(admission)
        except Exception as exc:  # noqa: BLE001 - final guard for claimed issues
            self._release_claim_if_unresolved(admission)
            reason = f"runner interrupted: {exc}"
            self._finish_outcome(admission, "interrupted", reason)
            result = RunResult(
                run_id=admission.run_id,
                issue=admission.issue,
                state="interrupted",
                error=str(exc),
            )
        result = self._export_terminal_trace(result)
        self._cleanup_terminal_worktree(result)
        return result

    def _retry_failed_trace_exports(self) -> None:
        retry_failed_trace_exports(config=self.config, ledger=self.ledger)

    def _export_terminal_trace(self, result: RunResult) -> RunResult:
        return export_terminal_trace(config=self.config, ledger=self.ledger, result=result)

    def _cleanup_terminal_worktree(self, result: RunResult) -> None:
        """Reconcile after export; only verified traces permit workspace discard."""
        if not self.config.cleanup_success_worktree or not result.run_id:
            return
        worktree = result.worktree_path
        if worktree is None:
            run = self.ledger.get_run(result.run_id)
            raw_worktree = run.get("worktree_path") if run else None
            if isinstance(raw_worktree, str) and raw_worktree:
                worktree = Path(raw_worktree)
        if worktree is not None:
            self.reconcile_worktrees(apply=True, only_paths={worktree})

    def should_start(self) -> SchedulerDecision:
        self.reconcile_stale_runs()
        worktrees = self.reconcile_worktrees(apply=not self.config.dry_run)
        if not worktrees.within_bounds:
            return self._record_scheduler_decision(
                SchedulerDecision(
                    should_run=False,
                    reason=(
                        "terminal worktree retention limit reached: "
                        f"{worktrees.remaining_terminal_directories} directories, "
                        f"{worktrees.remaining_terminal_bytes} bytes"
                    ),
                    recent_limit=0,
                    recent_runs=0,
                )
            )
        from src.workspace.trace_backfill import session_retention_status

        session_limit = session_retention_status(
            runner_root=self.config.root,
            codex_home=self.config.codex_home or Path.home() / ".codex",
            max_files=self.config.max_retained_session_files,
            max_bytes=int(self.config.max_retained_session_gib * 1024**3),
            max_unlinked_age_s=int(self.config.max_unlinked_session_age_days * 24 * 60 * 60),
        )
        if session_limit.over_limit:
            return self._record_scheduler_decision(
                SchedulerDecision(
                    should_run=False,
                    reason=session_limit.reason or "Codex session retention limit reached",
                    recent_limit=0,
                    recent_runs=0,
                )
            )
        max_session_bytes = int(self.config.max_retained_session_gib * 1024**3)
        max_unlinked_age_s = int(self.config.max_unlinked_session_age_days * 24 * 60 * 60)
        if (
            (
                self.config.max_retained_session_files > 0
                and session_limit.files >= int(self.config.max_retained_session_files * 0.8)
            )
            or (max_session_bytes > 0 and session_limit.bytes >= int(max_session_bytes * 0.8))
            or (
                max_unlinked_age_s > 0
                and session_limit.oldest_unlinked_age_s >= int(max_unlinked_age_s * 0.8)
            )
        ):
            print(
                json.dumps(
                    {
                        "event": "codex_retention_warning",
                        "kind": "session_growth",
                        **asdict(session_limit),
                        "max_files": self.config.max_retained_session_files,
                        "max_bytes": max_session_bytes,
                        "max_unlinked_age_s": max_unlinked_age_s,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        health = check_host_health(self.config)
        if health.warning:
            print(
                json.dumps(
                    {
                        "event": "codex_retention_warning",
                        "kind": "disk_headroom",
                        "message": health.warning,
                        "disk_free_bytes": health.disk_free_bytes,
                        "disk_total_bytes": health.disk_total_bytes,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
        if not health.ok:
            return self._record_scheduler_decision(
                SchedulerDecision(
                    should_run=False,
                    reason=health.reason or "host health gate failed",
                    recent_limit=0,
                    recent_runs=0,
                )
            )

        quarantine_runs, quarantine_bytes = self.ledger.trace_quarantine_totals()
        max_quarantine_bytes = int(self.config.max_quarantine_gib * 1024**3)
        quarantine_over_limit = (
            quarantine_runs >= self.config.max_quarantine_runs
            or quarantine_bytes >= max_quarantine_bytes
        )
        if quarantine_over_limit:
            return self._record_scheduler_decision(
                SchedulerDecision(
                    should_run=False,
                    reason=(
                        "trace quarantine retention limit reached: "
                        f"{quarantine_runs} runs, {quarantine_bytes} bytes"
                    ),
                    recent_limit=0,
                    recent_runs=0,
                )
            )
        if quarantine_runs >= max(
            1, int(self.config.max_quarantine_runs * 0.8)
        ) or quarantine_bytes >= int(max_quarantine_bytes * 0.8):
            print(
                json.dumps(
                    {
                        "event": "codex_retention_warning",
                        "kind": "quarantine_growth",
                        "quarantine_runs": quarantine_runs,
                        "quarantine_bytes": quarantine_bytes,
                        "max_quarantine_runs": self.config.max_quarantine_runs,
                        "max_quarantine_bytes": max_quarantine_bytes,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )

        usage = self._probe_usage()
        recent_limit = self._recent_run_limit(usage)
        pacing_interval = self._pacing_interval(usage)
        last_started_at = self.ledger.last_run_started_at(active_slot=self.config.active_slot)
        now = int(time.time())
        recent_runs = self.ledger.count_recent_runs(
            active_slot=self.config.active_slot,
            since=now - FIVE_HOURS_S,
        )
        if recent_runs >= recent_limit:
            return self._record_scheduler_decision(
                SchedulerDecision(
                    should_run=False,
                    reason="five-hour run budget exhausted",
                    recent_limit=recent_limit,
                    recent_runs=recent_runs,
                    usage=usage,
                    pacing_interval_s=pacing_interval,
                    last_started_at=last_started_at,
                )
            )

        retry_after = self._usage_retry_after(usage)
        if retry_after is not None:
            return self._record_scheduler_decision(
                SchedulerDecision(
                    should_run=False,
                    reason="Codex usage window below threshold",
                    recent_limit=recent_limit,
                    recent_runs=recent_runs,
                    usage=usage,
                    retry_after_s=retry_after,
                    pacing_interval_s=pacing_interval,
                    last_started_at=last_started_at,
                )
            )

        if last_started_at is not None and pacing_interval > 0:
            elapsed = now - last_started_at
            if elapsed < pacing_interval:
                return self._record_scheduler_decision(
                    SchedulerDecision(
                        should_run=False,
                        reason="start pacing interval active",
                        recent_limit=recent_limit,
                        recent_runs=recent_runs,
                        usage=usage,
                        retry_after_s=pacing_interval - elapsed,
                        pacing_interval_s=pacing_interval,
                        last_started_at=last_started_at,
                    )
                )

        return self._record_scheduler_decision(
            SchedulerDecision(
                should_run=True,
                reason="admitted",
                recent_limit=recent_limit,
                recent_runs=recent_runs,
                usage=usage,
                pacing_interval_s=pacing_interval,
                last_started_at=last_started_at,
            )
        )

    def _record_scheduler_decision(self, decision: SchedulerDecision) -> SchedulerDecision:
        self.ledger.record_usage_snapshot(
            active_slot=self.config.active_slot,
            decision=decision,
        )
        return decision

    def _probe_usage(self) -> UsageProbeResult | None:
        probe = self.config.usage_probe_path
        if not probe or not probe.exists():
            return UsageProbeResult(ok=False, error="usage probe not found")
        return run_usage_probe(probe)

    def _recent_run_limit(self, usage: UsageProbeResult | None) -> int:
        cfg = self.config
        conservative = max(1, min(cfg.conservative_runs_per_5h, cfg.max_runs_per_5h))
        if self._fast_mode_enabled(usage):
            return cfg.max_runs_per_5h
        return conservative

    def _pacing_interval(self, usage: UsageProbeResult | None) -> int:
        if self._fast_mode_enabled(usage):
            return max(0, self.config.fast_min_start_interval_s)
        return max(0, self.config.conservative_min_start_interval_s)

    def _fast_mode_enabled(self, usage: UsageProbeResult | None) -> bool:
        if not usage or not usage.ok:
            return False
        weekly = _window(usage, "weekly")
        return (
            weekly is not None
            and weekly.remaining_percent is not None
            and weekly.remaining_percent >= self.config.fast_weekly_remaining_percent
        )

    def _usage_retry_after(self, usage: UsageProbeResult | None) -> int | None:
        if not usage:
            return None
        if not usage.ok:
            resets = [
                window.reset_in_seconds
                for window in usage.windows
                if window.reset_in_seconds is not None
            ]
            return min(resets) if resets else None
        for window_name, threshold in (
            ("five_hour", self.config.min_five_hour_remaining_percent),
            ("weekly", self.config.min_weekly_remaining_percent),
        ):
            window = _window(usage, window_name)
            if (
                window
                and window.remaining_percent is not None
                and window.remaining_percent < threshold
            ):
                return window.reset_in_seconds or UNKNOWN_USAGE_RETRY_S
        return None

    def reconcile_stale_runs(self) -> None:
        now = int(time.time())
        for run in self.ledger.expired_active_runs(active_slot=self.config.active_slot, now=now):
            pid = run.get("pid")
            run_id = run["run_id"]
            if isinstance(pid, int) and _pid_matches_run(pid, run_id):
                self.ledger.update(
                    run_id,
                    heartbeat_at=now,
                    lease_expires_at=now + self.config.lease_timeout_s,
                )
                continue
            issue = run.get("issue")
            claim_id = run.get("claim_comment_id")
            if isinstance(issue, int) and isinstance(claim_id, int):
                admission = Admission(
                    run_id=run_id,
                    issue=issue,
                    claim_comment_id=claim_id,
                )
                self._release_claim_if_unresolved(admission)
            admission = Admission(
                run_id=run_id,
                issue=issue if isinstance(issue, int) else 0,
                claim_comment_id=claim_id if isinstance(claim_id, int) else 0,
            )
            self._finish_outcome(admission, "interrupted", "stale lease expired")

    def reconcile_worktrees(
        self,
        *,
        apply: bool,
        only_paths: set[Path] | None = None,
    ):
        """Classify and optionally retire terminal runner worktrees."""
        from src.workspace.worktree_reconcile import (
            TRUSTED_GITHUB_REPOSITORY,
            GitHubRemoteVerifier,
            combine_worktree_reports,
            prune_redundant_workspace_archives,
            reconcile_managed_worktrees,
            reconcile_worktrees,
        )

        cfg = self.config
        max_terminal_bytes = int(cfg.max_terminal_worktree_gib * 1024**3)
        managed_worktrees_dir = cfg.managed_worktrees_dir
        assert managed_worktrees_dir is not None
        contexts = self._managed_worktree_contexts()
        remote_verifier = GitHubRemoteVerifier(
            repo_dir=cfg.repo_dir,  # type: ignore[arg-type]
            github=self.github,
            repository=TRUSTED_GITHUB_REPOSITORY,
        )
        managed_remote_verifier = GitHubRemoteVerifier(
            repo_dir=cfg.managed_repo_dir,  # type: ignore[arg-type]
            github=self.github,
            repository=TRUSTED_GITHUB_REPOSITORY,
        )

        managed_report = reconcile_managed_worktrees(
            repo_dir=cfg.managed_repo_dir,  # type: ignore[arg-type]
            worktrees_dir=managed_worktrees_dir,
            archive_dir=cfg.state_dir / "worktree-quarantine",  # type: ignore[operator]
            ledger=self.ledger,
            authoritative_main_verifier=managed_remote_verifier.verify_main,
            branch_verifier=lambda branch: managed_remote_verifier.verify_branch(
                branch,
                allow_absent=True,
            ),
            pid_checker=_pid_matches_run,
            live_path_checker=lambda path: (
                str(path.resolve()) in _live_worktree_paths(managed_worktrees_dir)
            ),
            max_terminal_directories=cfg.max_terminal_worktrees,
            max_terminal_bytes=max_terminal_bytes,
            apply=apply,
            context_by_path=contexts,
        )

        def _pre_remove(item) -> None:
            if item.issue is None:
                return
            worktree = Path(item.path)
            workspace_root = worktree / "apps" / "crawler" / ".workspace"
            self._cleanup_ws_artifacts_for_issue(
                item.issue,
                run_id=item.run_id,
                workspace_root=workspace_root,
                workspace_container=worktree,
            )

        runner_report = reconcile_worktrees(
            root=cfg.root,
            repo_dir=cfg.repo_dir,  # type: ignore[arg-type]
            worktrees_dir=cfg.worktrees_dir,  # type: ignore[arg-type]
            archive_dir=cfg.state_dir / "worktree-quarantine",  # type: ignore[operator]
            ledger=self.ledger,
            remote_verifier=remote_verifier,
            authoritative_main_verifier=remote_verifier.verify_main,
            pid_checker=_pid_matches_run,
            max_terminal_directories=cfg.max_terminal_worktrees,
            max_terminal_bytes=max_terminal_bytes,
            apply=apply,
            only_paths=only_paths,
            pre_remove=_pre_remove,
        )
        archive_retention = prune_redundant_workspace_archives(
            repo_dir=cfg.repo_dir,  # type: ignore[arg-type]
            archive_dir=cfg.state_dir / "worktree-quarantine",  # type: ignore[operator]
            ledger=self.ledger,
            remote_verifier=remote_verifier,
            authoritative_main_verifier=remote_verifier.verify_main,
            apply=apply,
        )
        print(
            json.dumps(
                {
                    "event": "codex_worktree_archive_retention",
                    **asdict(archive_retention),
                },
                sort_keys=True,
            )
        )
        report = combine_worktree_reports(
            [runner_report, managed_report],
            max_terminal_directories=cfg.max_terminal_worktrees,
            max_terminal_bytes=max_terminal_bytes,
            quarantine_dir=cfg.state_dir / "worktree-quarantine",  # type: ignore[operator]
        )
        print(
            json.dumps(
                {
                    "event": "codex_worktree_reconciliation",
                    **report.to_dict(include_items=False),
                },
                sort_keys=True,
            )
        )
        return report

    def _managed_worktree_contexts(self) -> dict[str, dict[str, Any]]:
        """Join workspace YAML paths back to the latest ledger run for an issue."""
        runs = self.ledger.worktree_runs()
        latest_by_issue: dict[int, dict[str, Any]] = {}
        roots: list[tuple[Path, Path]] = []
        for run in runs:
            issue = run.get("issue")
            if isinstance(issue, int):
                current = latest_by_issue.get(issue)
                if current is None or int(run.get("updated_at") or 0) >= int(
                    current.get("updated_at") or 0
                ):
                    latest_by_issue[issue] = run
            value = run.get("worktree_path")
            if isinstance(value, str) and value:
                worktree = Path(value)
                roots.append((worktree / "apps" / "crawler" / ".workspace", worktree))
        if self.config.repo_dir is not None:
            roots.append(
                (
                    self.config.repo_dir / "apps" / "crawler" / ".workspace",
                    self.config.repo_dir,
                )
            )
        if self.config.managed_repo_dir is not None:
            roots.append(
                (
                    self.config.managed_repo_dir / "apps" / "crawler" / ".workspace",
                    self.config.managed_repo_dir,
                )
            )

        contexts: dict[str, dict[str, Any]] = {}
        seen: set[Path] = set()
        for root, container in roots:
            resolved_root = _validated_workspace_root(root, container=container)
            if resolved_root is None or resolved_root in seen:
                continue
            seen.add(resolved_root)
            for workspace_dir in _safe_workspace_directories(resolved_root):
                data = _read_yaml_mapping_no_follow(workspace_dir / "workspace.yaml")
                managed_path = _workspace_worktree(data)
                issue = _workspace_issue(data)
                if managed_path is None:
                    continue
                context = latest_by_issue.get(issue) if issue is not None else None
                if context is None:
                    git_data = data.get("git")
                    git_mapping = git_data if isinstance(git_data, dict) else {}
                    context = {
                        "issue": issue,
                        "pr_number": _int_from_value(git_mapping.get("pr")),
                        "branch": git_mapping.get("branch"),
                        "state": "managed",
                    }
                contexts[str(managed_path.resolve())] = dict(context)
        return contexts

    def _execute_admission(self, admission: Admission) -> RunResult:
        cfg = self.config
        cfg.traces_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        cfg.logs_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        cfg.worktrees_dir.mkdir(parents=True, exist_ok=True)  # type: ignore[union-attr]
        trace_path = cfg.traces_dir / f"{admission.run_id}.jsonl"  # type: ignore[operator]
        stderr_path = cfg.logs_dir / f"{admission.run_id}.stderr.log"  # type: ignore[operator]
        worktree = self._prepare_worktree(admission)
        cwd = worktree / "apps" / "crawler"

        env = _safe_env()
        env["CODEX_EXEC_JSONL"] = str(trace_path)
        env["JOBSEEK_CODEX_RUN_ID"] = admission.run_id
        env["JOBSEEK_CODEX_ISSUE"] = str(admission.issue)
        env["WS_ACTIVE_SCOPE"] = admission.run_id

        cmd = build_codex_command(cfg, build_codex_prompt(admission.issue))
        timed_out = False
        with (
            self.ledger.worktree_execution_lease(admission.run_id),
            trace_path.open("w") as stdout,
            stderr_path.open("w") as stderr,
        ):
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                stdout=stdout,
                stderr=stderr,
                text=True,
                start_new_session=True,
            )
            self.ledger.update(
                admission.run_id,
                state="running",
                pid=proc.pid,
                trace_path=str(trace_path),
                stderr_path=str(stderr_path),
                worktree_path=str(worktree),
                started_at=int(time.time()),
                heartbeat_at=int(time.time()),
                lease_expires_at=int(time.time()) + cfg.lease_timeout_s,
            )
            try:
                exit_code = proc.wait(timeout=cfg.max_runtime_s)
            except subprocess.TimeoutExpired:
                _terminate_process_group(proc, cfg.kill_grace_s)
                timed_out = True

        if timed_out:
            summary = parse_codex_usage_jsonl(trace_path)
            self.ledger.ingest_trace_once(admission.run_id, trace_path, summary)
            self._record_pr_if_present(admission)
            self._release_claim_if_unresolved(admission, worktree=worktree)
            reason = "codex runtime exceeded"
            self._finish_outcome(admission, "interrupted", reason)
            return RunResult(
                run_id=admission.run_id,
                issue=admission.issue,
                state="interrupted",
                trace_path=trace_path,
                stderr_path=stderr_path,
                worktree_path=worktree,
                error="codex runtime exceeded",
            )

        summary = parse_codex_usage_jsonl(trace_path)
        self.ledger.ingest_trace_once(admission.run_id, trace_path, summary)
        resolution = self._record_resolution(admission, worktree=worktree)
        if resolution is not None:
            state, reason = resolution
            error = None
        elif exit_code == 0:
            state = "retryable"
            reason = "codex exited 0 without a terminal ws outcome"
            error = reason
        else:
            state = "retryable"
            reason = f"codex exited with status {exit_code} without a terminal ws outcome"
            error = f"exit {exit_code}"
        if state in RETRY_OUTCOMES:
            self._release_claim_if_unresolved(admission, worktree=worktree)
        self._finish_outcome(admission, state, reason, error=error)
        return RunResult(
            run_id=admission.run_id,
            issue=admission.issue,
            state=state,
            exit_code=exit_code,
            trace_path=trace_path,
            stderr_path=stderr_path,
            worktree_path=worktree,
        )

    def _prepare_worktree(self, admission: Admission) -> Path:
        cfg = self.config
        repo = cfg.repo_dir
        worktree = cfg.worktrees_dir / f"company-request-{admission.issue}-{admission.run_id}"  # type: ignore[operator]
        subprocess.run(["git", "fetch", "origin"], cwd=repo, check=True)
        subprocess.run(["git", "worktree", "prune"], cwd=repo, check=True)
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(worktree), "origin/main"],
            cwd=repo,
            check=True,
        )
        return worktree

    def _new_run_id(self, issue: int) -> str:
        return f"issue-{issue}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    def _record_pr_if_present(self, admission: Admission) -> None:
        try:
            prs = self.github.check_existing_prs(admission.issue)
        except GitHubStateError:
            return
        if prs:
            self._record_pr(admission, prs[0])

    def _record_resolution(
        self,
        admission: Admission,
        *,
        worktree: Path | None,
    ) -> tuple[str, str] | None:
        from src.workspace.git import classify_issue_prs

        prs = self.github.check_existing_prs(admission.issue)
        if prs:
            self._record_pr(admission, prs[0])
        issue = self.github.issue_resolution(admission.issue)
        if issue.outcome == "submitted" and not prs:
            return "submitted", issue.reason or "issue closed by linked PR"
        if issue.outcome in {"rejected", "escalated"} and not prs:
            return issue.outcome, issue.reason or f"issue was {issue.outcome}"
        if prs and classify_issue_prs(prs) == "submitted":
            number = prs[0].get("number")
            branch = prs[0].get("headRefName")
            if isinstance(branch, str) and branch.startswith("fix-crawler/"):
                return "submitted", f"coding-mode fix submitted as PR #{number}"
            if worktree and _ws_issue_completed(worktree, admission.issue):
                return "submitted", f"ws completed and PR #{number} remains draft for review"
        return None

    def _record_pr(self, admission: Admission, pr: dict[str, Any]) -> None:
        number = _int_from_value(pr.get("number"))
        self.ledger.update(
            admission.run_id,
            pr_number=number,
            pr_url=pr.get("url"),
            branch=pr.get("headRefName") or pr.get("branch"),
        )

    def _release_claim_if_unresolved(
        self,
        admission: Admission,
        *,
        worktree: Path | None = None,
    ) -> None:
        try:
            resolved = self._record_resolution(admission, worktree=worktree)
        except GitHubStateError:
            return
        if resolved:
            return
        self.github.delete_claim(admission.claim_comment_id)

    def _finish_outcome(
        self,
        admission: Admission,
        state: str,
        reason: str,
        *,
        error: str | None = None,
    ) -> None:
        retry_after_at = None
        if state in RETRY_OUTCOMES:
            retry_after_at = int(time.time()) + self._retry_delay(admission.run_id)
        issue_comment_error = None
        if admission.issue > 0:
            try:
                self.github.record_run_outcome(
                    admission.issue,
                    run_id=admission.run_id,
                    outcome=state,
                    reason=reason,
                    retry_after_at=retry_after_at,
                )
            except (AttributeError, GitHubStateError) as exc:
                issue_comment_error = str(exc)
        ledger_error = error
        if issue_comment_error:
            ledger_error = "; ".join(part for part in (error, issue_comment_error) if part)
        self.ledger.finish(
            admission.run_id,
            state,
            error=ledger_error,
            outcome_reason=reason,
            retry_after_at=retry_after_at,
        )

    def _retry_delay(self, run_id: str) -> int:
        run = self.ledger.get_run(run_id) or {}
        attempt = max(1, int(run.get("attempt") or 1))
        base = max(1, self.config.retry_backoff_s)
        maximum = max(base, self.config.max_retry_backoff_s)
        return min(maximum, base * (2 ** min(attempt - 1, 10)))

    def _cleanup_ws_artifacts_for_issue(
        self,
        issue: int,
        *,
        run_id: str | None = None,
        workspace_root: Path | None = None,
        workspace_container: Path | None = None,
    ) -> None:
        run_scope_marker = _run_scope_marker_name(issue=issue, run_id=run_id)
        if run_id is not None and run_scope_marker is None:
            raise RuntimeError("invalid run identity for scoped workspace cleanup")
        roots: list[tuple[Path, Path, bool]] = []
        if workspace_root is not None:
            roots.append((workspace_root, workspace_container or workspace_root.parent, True))
        if self.config.repo_dir is not None:
            roots.append(
                (
                    self.config.repo_dir / "apps" / "crawler" / ".workspace",
                    self.config.repo_dir,
                    False,
                )
            )
        if self.config.managed_repo_dir is not None:
            roots.append(
                (
                    self.config.managed_repo_dir / "apps" / "crawler" / ".workspace",
                    self.config.managed_repo_dir,
                    False,
                )
            )

        seen: set[Path] = set()
        for root, container, isolated_run_root in roots:
            safe_root = _validated_workspace_root(root, container=container)
            if safe_root is None or safe_root in seen:
                continue
            seen.add(safe_root)
            recover_pending_rmtree_claims(safe_root)
            for workspace_dir in _workspace_dirs_for_issue(safe_root, issue):
                self._cleanup_workspace_dir(
                    workspace_dir,
                    safe_root,
                    run_scope_marker=run_scope_marker,
                )
            if isolated_run_root:
                _cleanup_terminal_lifecycle_receipts(safe_root, issue=issue)

    def _cleanup_workspace_dir(
        self,
        workspace_dir: Path,
        workspace_root: Path,
        *,
        run_scope_marker: str | None = None,
    ) -> None:
        safe_workspace_dir = _validated_workspace_child(workspace_dir, workspace_root)
        if safe_workspace_dir is None:
            return
        workspace_root_fd = open_absolute_directory_no_follow(workspace_root)
        workspace_fd: int | None = None
        try:
            workspace_fd, workspace_stat = open_child_directory_no_follow(
                workspace_root_fd,
                safe_workspace_dir.name,
            )
            data = _read_yaml_mapping_at(workspace_fd, "workspace.yaml")
            if not data:
                return
            slug = safe_workspace_dir.name
            if data.get("slug") != slug:
                raise RuntimeError("workspace metadata slug does not match its directory")
            worktree = _workspace_worktree(data)
            if worktree and worktree.exists():
                raise RuntimeError(
                    f"managed ws worktree {worktree} was retained; refusing workspace cleanup"
                )
            # Clear the authenticated active pointer while workspace.yaml still
            # durably binds this slug to the terminal issue.  If directory
            # removal is interrupted, the issue-bearing workspace remains and
            # the next pass can repeat this proof.
            if run_scope_marker is None:
                _cleanup_active_markers_at(workspace_root_fd, slug)
            else:
                _cleanup_exact_active_marker_at(
                    workspace_root_fd,
                    name=run_scope_marker,
                    slug=slug,
                )
            rmtree_child_at(
                workspace_root_fd,
                slug,
                child_fd=workspace_fd,
                expected=workspace_stat,
            )
        finally:
            if workspace_fd is not None:
                os.close(workspace_fd)
            os.close(workspace_root_fd)


def _is_stale_runner_claim(claim: ClaimComment, *, older_than_s: int) -> bool:
    if not claim.body.startswith(DEFAULT_CLAIM_MARKER):
        return False
    if not any(line.startswith("run: issue-") for line in claim.body.splitlines()):
        return False
    created_at = _parse_github_timestamp(claim.created_at)
    if created_at is None:
        return False
    return int(time.time()) - created_at >= older_than_s


def _parse_github_timestamp(value: str | None) -> int | None:
    if not value:
        return None
    try:
        # Ubuntu 22.04 ships Python 3.10, before datetime.UTC exists.
        dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)  # noqa: UP017
    except ValueError:
        return None
    return int(dt.timestamp())


def _comment_field(body: str, name: str) -> str | None:
    prefix = f"{name}:"
    for line in body.splitlines():
        if line.startswith(prefix):
            value = line.removeprefix(prefix).strip()
            return value or None
    return None


def _ws_issue_completed(worktree: Path, issue: int) -> bool:
    workspace_root = worktree / "apps" / "crawler" / ".workspace"
    if not workspace_root.exists():
        return False
    for workspace_dir in _workspace_dirs_for_issue(workspace_root, issue):
        workflow = _read_yaml_mapping(workspace_dir / "workflow.state.yaml")
        if workflow.get("current_step") == "done":
            return True
        log = _read_yaml_list(workspace_dir / "log.yaml")
        for entry in log:
            if (
                isinstance(entry, dict)
                and entry.get("cmd") == "complete"
                and entry.get("ok") is True
            ):
                return True
    return False


def _workspace_dirs_for_issue(workspace_root: Path, issue: int) -> list[Path]:
    matches: list[Path] = []
    for workspace_dir in _safe_workspace_directories(workspace_root):
        data = _read_yaml_mapping_no_follow(workspace_dir / "workspace.yaml")
        if _workspace_issue(data) == issue:
            matches.append(workspace_dir)
    return matches


def _validated_workspace_root(path: Path, *, container: Path) -> Path | None:
    """Resolve a workspace root only when every child component is a real directory."""
    try:
        container_mode = container.lstat().st_mode
        if stat.S_ISLNK(container_mode) or not stat.S_ISDIR(container_mode):
            return None
        relative = path.relative_to(container)
        if not relative.parts or ".." in relative.parts:
            return None
        container_resolved = container.resolve(strict=True)
        current = container
        for part in relative.parts:
            current = current / part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                return None
        resolved = path.resolve(strict=True)
    except (OSError, ValueError):
        return None
    if not resolved.is_relative_to(container_resolved):
        return None
    return resolved


def _validated_workspace_child(path: Path, workspace_root: Path) -> Path | None:
    try:
        root_mode = workspace_root.lstat().st_mode
        child_mode = path.lstat().st_mode
        if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
            return None
        if stat.S_ISLNK(child_mode) or not stat.S_ISDIR(child_mode):
            return None
        root_resolved = workspace_root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    if resolved.parent != root_resolved:
        return None
    return resolved


def _safe_workspace_directories(workspace_root: Path) -> list[Path]:
    try:
        root_mode = workspace_root.lstat().st_mode
    except OSError:
        return []
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        return []

    directories: list[Path] = []
    try:
        entries = list(workspace_root.iterdir())
    except OSError:
        return []
    for entry in entries:
        safe_entry = _validated_workspace_child(entry, workspace_root)
        if safe_entry is None:
            continue
        workspace_yaml = safe_entry / "workspace.yaml"
        try:
            yaml_mode = workspace_yaml.lstat().st_mode
            yaml_resolved = workspace_yaml.resolve(strict=True)
        except OSError:
            continue
        if (
            stat.S_ISLNK(yaml_mode)
            or not stat.S_ISREG(yaml_mode)
            or yaml_resolved.parent != safe_entry
        ):
            continue
        directories.append(safe_entry)
    return directories


def _read_text_at_no_follow(parent_fd: int, name: str) -> tuple[str, os.stat_result]:
    if not name or name in {".", ".."} or "/" in name:
        raise OSError(f"invalid workspace metadata name: {name!r}")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(name, flags, dir_fd=parent_fd)
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise OSError(f"not a regular workspace metadata file: {name}")
        return handle.read(), opened


def _read_yaml_mapping_at(parent_fd: int, name: str) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(_read_text_at_no_follow(parent_fd, name)[0])
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _cleanup_terminal_lifecycle_receipts(workspace_root: Path, *, issue: int) -> None:
    """Remove completed terminal receipts from one ledger-bound run workspace."""
    from src.shared.constants import SLUG_RE
    from src.workspace.commands.lifecycle import (
        _validate_issue_terminal_journal,
        _validate_terminal_journal,
    )

    workspace_root_fd = open_absolute_directory_no_follow(workspace_root)
    terminal_fd: int | None = None
    issues_fd: int | None = None
    try:
        try:
            terminal_fd, _ = open_child_directory_no_follow(
                workspace_root_fd,
                ".terminal-lifecycle",
            )
        except RuntimeError as exc:
            if isinstance(exc.__cause__, FileNotFoundError):
                return
            raise RuntimeError("terminal lifecycle receipt root is unsafe") from exc

        completed: list[tuple[int, str, str, os.stat_result]] = []
        logical_names: set[tuple[int, str]] = set()
        for name in sorted(os.listdir(terminal_fd)):
            if name == "issues":
                issues_fd, _ = open_child_directory_no_follow(terminal_fd, name)
                for issue_name in sorted(os.listdir(issues_fd)):
                    data, opened = _read_terminal_receipt_at(issues_fd, issue_name)
                    _validate_issue_terminal_journal(data, issue)
                    logical_name = f"{issue}.completed.yaml"
                    if not _terminal_receipt_name_matches(issue_name, logical_name):
                        raise RuntimeError(
                            "terminal lifecycle issue receipt belongs to another run"
                        )
                    key = (issues_fd, logical_name)
                    if key in logical_names:
                        raise RuntimeError("terminal lifecycle receipt has duplicate evidence")
                    logical_names.add(key)
                    completed.append((issues_fd, issue_name, logical_name, opened))
                continue

            if not name.endswith(
                (".latest-receipt", ".completed.yaml")
            ) and not _is_terminal_receipt_claim_name(name):
                raise RuntimeError("terminal lifecycle contains incomplete or unknown evidence")

            data, opened = _read_terminal_receipt_at(terminal_fd, name)
            if data.get("issue") != issue:
                raise RuntimeError("terminal lifecycle receipt belongs to another run")
            if set(data) == {"version", "slug", "journal_id", "issue"}:
                slug = data.get("slug")
                journal_id = data.get("journal_id")
                if (
                    data.get("version") != 1
                    or not isinstance(slug, str)
                    or not SLUG_RE.fullmatch(slug)
                    or not isinstance(journal_id, str)
                    or len(journal_id) != 32
                    or any(character not in "0123456789abcdef" for character in journal_id)
                ):
                    raise RuntimeError("terminal lifecycle locator is invalid")
                logical_name = f"{slug}.latest-receipt"
            else:
                journal = _validate_terminal_journal(data)
                logical_name = f"{journal['slug']}.{journal['journal_id']}.completed.yaml"
            if not _terminal_receipt_name_matches(name, logical_name):
                raise RuntimeError("terminal lifecycle receipt filename is invalid")
            key = (terminal_fd, logical_name)
            if key in logical_names:
                raise RuntimeError("terminal lifecycle receipt has duplicate evidence")
            logical_names.add(key)
            completed.append((terminal_fd, name, logical_name, opened))

        for parent_fd, stored_name, logical_name, opened in completed:
            _remove_terminal_receipt_at(
                parent_fd,
                stored_name=stored_name,
                logical_name=logical_name,
                expected=opened,
            )
    finally:
        if issues_fd is not None:
            os.close(issues_fd)
        if terminal_fd is not None:
            os.close(terminal_fd)
        os.close(workspace_root_fd)


def _read_terminal_receipt_at(
    parent_fd: int,
    name: str,
) -> tuple[dict[str, Any], os.stat_result]:
    descriptor: int | None = None
    try:
        import yaml

        validate_child_name(name)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or opened.st_size > _TERMINAL_RECEIPT_MAX_BYTES
        ):
            raise RuntimeError(f"terminal lifecycle receipt is unauthenticated: {name}")
        chunks: list[bytes] = []
        remaining = _TERMINAL_RECEIPT_MAX_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw_bytes = b"".join(chunks)
        if len(raw_bytes) > _TERMINAL_RECEIPT_MAX_BYTES:
            raise RuntimeError(f"terminal lifecycle receipt is unauthenticated: {name}")
        data = yaml.safe_load(raw_bytes.decode())
    except Exception as exc:
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(f"terminal lifecycle receipt is unsafe: {name}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not isinstance(data, dict):
        raise RuntimeError(f"terminal lifecycle receipt is unauthenticated: {name}")
    return data, opened


def _terminal_receipt_claim_name(logical_name: str) -> str:
    validate_child_name(logical_name)
    digest = hashlib.sha256(os.fsencode(logical_name)).hexdigest()
    return f"{_TERMINAL_RECEIPT_CLAIM_PREFIX}{digest}"


def _is_terminal_receipt_claim_name(name: str) -> bool:
    digest = name.removeprefix(_TERMINAL_RECEIPT_CLAIM_PREFIX)
    return (
        name.startswith(_TERMINAL_RECEIPT_CLAIM_PREFIX)
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def _terminal_receipt_name_matches(stored_name: str, logical_name: str) -> bool:
    return stored_name in {logical_name, _terminal_receipt_claim_name(logical_name)}


def _remove_terminal_receipt_at(
    parent_fd: int,
    *,
    stored_name: str,
    logical_name: str,
    expected: os.stat_result,
) -> None:
    claimed_name = _terminal_receipt_claim_name(logical_name)
    if stored_name == logical_name:
        claim_child_at(
            parent_fd,
            logical_name,
            expected=expected,
            claimed_name=claimed_name,
        )
        os.fsync(parent_fd)
    elif stored_name != claimed_name:
        raise RuntimeError("terminal lifecycle receipt claim is invalid")
    unlink_claimed_child_at(parent_fd, claimed_name, expected=expected)
    os.fsync(parent_fd)


def _cleanup_active_markers_at(workspace_root_fd: int, slug: str) -> None:
    for name in os.listdir(workspace_root_fd):
        if not name.startswith("active"):
            continue
        try:
            value, opened = _read_text_at_no_follow(workspace_root_fd, name)
            if value.strip() != slug:
                continue
            unlink_child_at(workspace_root_fd, name, expected=opened)
        except (OSError, RuntimeError):
            continue


def _cleanup_exact_active_marker_at(workspace_root_fd: int, *, name: str, slug: str) -> None:
    try:
        value, opened = _read_text_at_no_follow(workspace_root_fd, name)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RuntimeError(f"run-scoped workspace marker is unsafe: {name}") from exc
    if (
        opened.st_uid != os.geteuid()
        or opened.st_nlink != 1
        or _workspace_active_pointer_slug(value, require_v1=True) != slug
    ):
        raise RuntimeError(f"run-scoped workspace marker is unauthenticated: {name}")
    unlink_child_at(workspace_root_fd, name, expected=opened)


def _workspace_active_pointer_slug(raw: str, *, require_v1: bool = False) -> str | None:
    """Read a workspace pointer without accepting malformed authenticated records."""
    value = raw.strip()
    if not value:
        return None
    if not value.startswith("{"):
        return None if require_v1 else value
    try:
        record = json.loads(value)
    except json.JSONDecodeError:
        return None
    generation = record.get("generation") if isinstance(record, dict) else None
    if (
        not isinstance(record, dict)
        or set(record) != {"version", "slug", "generation"}
        or record.get("version") != 1
        or not isinstance(record.get("slug"), str)
        or not record["slug"]
        or not isinstance(generation, str)
        or len(generation) != 32
        or any(character not in "0123456789abcdef" for character in generation)
    ):
        return None
    return str(record["slug"])


def _run_scope_marker_name(*, issue: int, run_id: str | None) -> str | None:
    """Derive the exact active-pointer child name for one ledger-bound run."""
    if run_id is None:
        return None
    parts = run_id.split("-")
    if (
        len(parts) != 4
        or parts[0] != "issue"
        or parts[1] != str(issue)
        or not parts[2].isdigit()
        or len(parts[3]) != 8
        or any(character not in "0123456789abcdef" for character in parts[3])
    ):
        return None
    return f"active.scope-{run_id}"


def _read_text_no_follow(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise OSError(f"not a regular file: {path}")
        return handle.read()


def _read_yaml_mapping_no_follow(path: Path) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(_read_text_no_follow(path))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(path.read_text())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_yaml_list(path: Path) -> list[Any]:
    try:
        import yaml

        data = yaml.safe_load(path.read_text())
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _workspace_issue(data: dict[str, Any]) -> int | None:
    git = data.get("git")
    value = git.get("issue") if isinstance(git, dict) else data.get("issue")
    return _int_from_value(value)


def _workspace_worktree(data: dict[str, Any]) -> Path | None:
    git = data.get("git")
    value = git.get("worktree") if isinstance(git, dict) else data.get("worktree")
    if not isinstance(value, str) or not value:
        return None
    return Path(value)


def _window(usage: UsageProbeResult, name: str) -> UsageWindow | None:
    for window in usage.windows:
        if window.name == name:
            return window
    return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def retry_failed_trace_exports(*, config: RunnerConfig, ledger: RunnerLedger) -> None:
    """Retry bounded terminal exports for every Jobseek automation kind."""
    if not config.trace_export_enabled:
        return
    for run in ledger.failed_trace_bundle_exports(
        limit=config.trace_retry_limit,
        include_pending_cleanup=config.trace_cleanup_enabled,
    ):
        export_terminal_trace(
            config=config,
            ledger=ledger,
            result=RunResult(
                run_id=str(run["run_id"]),
                issue=run.get("issue") if isinstance(run.get("issue"), int) else None,
                state=str(run["state"]),
                trace_path=(
                    Path(run["trace_path"]) if isinstance(run.get("trace_path"), str) else None
                ),
                stderr_path=(
                    Path(run["stderr_path"]) if isinstance(run.get("stderr_path"), str) else None
                ),
                worktree_path=(
                    Path(run["worktree_path"])
                    if isinstance(run.get("worktree_path"), str)
                    else None
                ),
                error=run.get("error") if isinstance(run.get("error"), str) else None,
            ),
        )


def export_terminal_trace(
    *,
    config: RunnerConfig,
    ledger: RunnerLedger,
    result: RunResult,
) -> RunResult:
    """Export, remotely verify, and checksum-clean one terminal run's evidence."""
    if not config.trace_export_enabled or not result.run_id:
        return result
    from src.workspace.trace_backfill import (
        build_bundle,
        cleanup_verified_sources,
        discover_sessions,
        pending_verified_cleanup,
        prune_hf_dataset_cache,
        quality_gate_reason,
        record_verified_export,
        upload_and_verify,
    )

    codex_home = config.codex_home or Path.home() / ".codex"
    ledger_path = config.ledger_path or config.root / "state" / "ledger.sqlite"
    tier: str | None = None
    retained_bytes = 0
    if config.trace_cleanup_enabled:
        try:
            pending_cleanup = pending_verified_cleanup(
                ledger_path=ledger_path,
                run_id=result.run_id,
            )
            if pending_cleanup is not None:
                manifest, tier, remote_dir, retained_bytes = pending_cleanup
                cleanup_verified_sources(
                    ledger_path=ledger_path,
                    run_id=result.run_id,
                    manifest=manifest,
                    runner_root=config.root,
                    codex_home=codex_home,
                    traces_dir=config.traces_dir,
                    logs_dir=config.logs_dir,
                )
                ledger.record_trace_bundle_attempt(
                    result.run_id,
                    status="cleaned",
                    quality_tier=tier,
                    remote_dir=remote_dir,
                    retained_bytes=0,
                )
                result.trace_export_status = "cleaned"
                result.trace_export_tier = tier
                result.trace_export_remote_dir = remote_dir
                return result
        except Exception as exc:  # noqa: BLE001 - retain verified evidence for retry
            error = _safe_trace_export_error(exc)
            ledger.record_trace_bundle_attempt(
                result.run_id,
                status="failed",
                quality_tier=tier,
                error=error,
                retained_bytes=retained_bytes,
            )
            result.trace_export_status = "failed"
            result.trace_export_error = error
            return result

    run = ledger.get_run(result.run_id)
    trace_value = run.get("trace_path") if run else None
    trace_path = Path(trace_value) if isinstance(trace_value, str) else None
    try:
        trace_mode = trace_path.lstat().st_mode if trace_path is not None else 0
    except OSError:
        trace_mode = 0
    if trace_path is None or not stat.S_ISREG(trace_mode):
        error = "terminal trace file unavailable"
        ledger.record_trace_bundle_attempt(result.run_id, status="unavailable", error=error)
        result.trace_export_status = "unavailable"
        result.trace_export_error = error
        return result

    try:
        sessions = discover_sessions(codex_home, result.run_id, ledger_path=ledger_path)
        ledger.record_codex_session_links(result.run_id, sessions)
        with tempfile.TemporaryDirectory(
            prefix=f"trace-export-{result.run_id}-",
            dir=config.state_dir,
        ) as temp_dir:
            bundle_dir = Path(temp_dir) / result.run_id
            manifest = build_bundle(
                run_id=result.run_id,
                runner_root=config.root,
                codex_home=codex_home,
                output_dir=bundle_dir,
                sessions=sessions,
                traces_dir=config.traces_dir,
                logs_dir=config.logs_dir,
            )
            tier = str(manifest["quality"]["tier"])
            retained_bytes = sum(
                int(entry.get("source_bytes", 0))
                for entry in manifest.get("files", [])
                if isinstance(entry, dict)
            )
            result.trace_export_tier = tier
            if tier == "quarantined":
                result.trace_export_status = "quarantined"
                ledger.record_trace_bundle_attempt(
                    result.run_id,
                    status="quarantined",
                    quality_tier=tier,
                    error=quality_gate_reason(manifest),
                    retained_bytes=retained_bytes,
                )
                return result

            remote_dir, verified = upload_and_verify(
                bundle_dir=bundle_dir,
                run_id=result.run_id,
                repo_id=config.trace_hf_repo,
                prefix=config.trace_hf_prefix,
                quality_tier=tier,
            )
            record_verified_export(
                ledger_path=ledger_path,
                run_id=result.run_id,
                remote_dir=remote_dir,
                manifest=manifest,
                verified=verified,
            )
            status = "verified"
            if config.trace_cleanup_enabled:
                cleanup_result = cleanup_verified_sources(
                    ledger_path=ledger_path,
                    run_id=result.run_id,
                    manifest=manifest,
                    runner_root=config.root,
                    codex_home=codex_home,
                    traces_dir=config.traces_dir,
                    logs_dir=config.logs_dir,
                )
                status = "cleaned"
                retained_bytes = 0
                disk = shutil.disk_usage(config.root)
                print(
                    json.dumps(
                        {
                            "event": "trace_retention_cleanup",
                            "run_id": result.run_id,
                            "reclaimed_bytes": int(cleanup_result["reclaimed_bytes"]),
                            "disk_free_bytes": disk.free,
                            "disk_total_bytes": disk.total,
                        },
                        sort_keys=True,
                    )
                )
                try:
                    cache_result = prune_hf_dataset_cache(repo_id=config.trace_hf_repo)
                    if cache_result["revisions"]:
                        print(
                            json.dumps(
                                {"event": "trace_hf_cache_cleanup", **cache_result},
                                sort_keys=True,
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - cache is non-canonical
                    print(
                        json.dumps(
                            {
                                "event": "trace_hf_cache_cleanup_failed",
                                "error": _safe_trace_export_error(exc),
                            },
                            sort_keys=True,
                        ),
                        file=sys.stderr,
                    )
            ledger.record_trace_bundle_attempt(
                result.run_id,
                status=status,
                quality_tier=tier,
                remote_dir=remote_dir,
                retained_bytes=retained_bytes,
            )
            result.trace_export_status = status
            result.trace_export_remote_dir = remote_dir
    except Exception as exc:  # noqa: BLE001 - export cannot alter the primary run outcome
        error = _safe_trace_export_error(exc)
        ledger.record_trace_bundle_attempt(
            result.run_id,
            status="failed",
            quality_tier=tier,
            error=error,
            retained_bytes=retained_bytes,
        )
        result.trace_export_status = "failed"
        result.trace_export_error = error
        print(
            json.dumps(
                {
                    "event": "trace_bundle_export_failed",
                    "run_id": result.run_id,
                    "quality_tier": tier,
                    "error": error,
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
    return result


def _safe_trace_export_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {str(exc)[:1000]}"
    try:
        from src.workspace.trace import detect_credentials

        if detect_credentials(message):
            return f"{type(exc).__name__}: details redacted by credential scanner"
    except Exception:  # noqa: BLE001 - error reporting must stay fail-safe
        return type(exc).__name__
    return message


def _pid_matches_run(pid: int, run_id: str) -> bool:
    proc_root = Path("/proc")
    environ_path = proc_root / str(pid) / "environ"
    if environ_path.exists():
        try:
            environ = environ_path.read_bytes().split(b"\0")
        except OSError:
            return False
        marker = f"JOBSEEK_CODEX_RUN_ID={run_id}".encode()
        return marker in environ
    if proc_root.exists():
        return False
    return _pid_alive(pid)


def _live_worktree_paths(worktrees_dir: Path) -> set[str]:
    """Return managed worktree roots containing a live process CWD."""
    proc_root = Path("/proc")
    if not proc_root.exists() or not worktrees_dir.exists():
        return set()
    try:
        root = worktrees_dir.resolve()
    except OSError:
        return set()
    live: set[str] = set()
    try:
        processes = list(proc_root.iterdir())
    except OSError:
        return live
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            cwd = Path(os.readlink(process / "cwd")).resolve()
            relative = cwd.relative_to(root)
        except (OSError, ValueError):
            continue
        if not relative.parts:
            continue
        candidate = root / relative.parts[0]
        if candidate.is_dir():
            live.add(str(candidate.resolve()))
    return live


def _terminate_process_group(proc: subprocess.Popen[Any], grace_s: int) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    proc.wait(timeout=grace_s)


def main() -> int:
    config = RunnerConfig.from_env()
    governor = CompanyResolverGovernor(config)
    result = governor.run_once()
    print(
        json.dumps(
            {
                "run_id": result.run_id,
                "issue": result.issue,
                "state": result.state,
                "exit_code": result.exit_code,
                "trace_path": str(result.trace_path) if result.trace_path else None,
                "trace_export_status": result.trace_export_status,
                "trace_export_tier": result.trace_export_tier,
                "trace_export_remote_dir": result.trace_export_remote_dir,
                "trace_export_error": result.trace_export_error,
                "error": result.error,
            },
            sort_keys=True,
        )
    )
    return 0 if result.state in {*RESOLVED_OUTCOMES, "completed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
