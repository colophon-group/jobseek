"""Hetzner-local Codex runner for company-request resolution.

The governor is intentionally small and stateful:

* it admits at most one active run through a SQLite ledger;
* it owns exactly one GitHub issue claim comment and deletes only that comment;
* it launches one noninteractive Codex process with one issue-specific prompt;
* it stores ``codex exec --json`` stdout as the canonical run trace.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACTIVE_STATES = ("claimed", "running")
TERMINAL_STATES = ("completed", "failed", "skipped", "timeout")
DEFAULT_ROOT = Path("/srv/jobseek-codex")
DEFAULT_RUNTIME_S = 90 * 60
DEFAULT_KILL_GRACE_S = 20
DEFAULT_CLAIM_MARKER = "<!-- ws-claim -->"
FIVE_HOURS_S = 5 * 60 * 60
ONE_WEEK_S = 7 * 24 * 60 * 60
UNKNOWN_USAGE_RETRY_S = 30 * 60


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


@dataclass(frozen=True)
class SchedulerDecision:
    should_run: bool
    reason: str
    recent_limit: int
    recent_runs: int
    usage: UsageProbeResult | None = None
    retry_after_s: int | None = None


@dataclass(frozen=True)
class ClaimComment:
    id: int
    body: str
    created_at: str | None = None


@dataclass(frozen=True)
class RunnerConfig:
    root: Path = DEFAULT_ROOT
    repo_dir: Path | None = None
    worktrees_dir: Path | None = None
    traces_dir: Path | None = None
    logs_dir: Path | None = None
    state_dir: Path | None = None
    ledger_path: Path | None = None
    codex_args: tuple[str, ...] = (
        "codex",
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
    )
    max_runtime_s: int = DEFAULT_RUNTIME_S
    kill_grace_s: int = DEFAULT_KILL_GRACE_S
    dry_run: bool = False
    cleanup_success_worktree: bool = True
    label: str = "company-request"
    active_slot: str = "company-resolver"
    max_runs_per_5h: int = 5
    conservative_runs_per_5h: int = 1
    min_five_hour_remaining_percent: float = 5.0
    min_weekly_remaining_percent: float = 3.0
    min_disk_free_gib: float = 5.0
    min_mem_available_gib: float = 2.0
    max_load_per_cpu: float = 2.0
    usage_probe_path: Path | None = None
    lease_timeout_s: int = 4 * 60 * 60

    def resolved(self) -> RunnerConfig:
        root = self.root
        repo_dir = self.repo_dir or root / "repo"
        return RunnerConfig(
            root=root,
            repo_dir=repo_dir,
            worktrees_dir=self.worktrees_dir or root / "worktrees",
            traces_dir=self.traces_dir or root / "traces",
            logs_dir=self.logs_dir or root / "logs",
            state_dir=self.state_dir or root / "state",
            ledger_path=self.ledger_path or root / "state" / "ledger.sqlite",
            codex_args=self.codex_args,
            max_runtime_s=self.max_runtime_s,
            kill_grace_s=self.kill_grace_s,
            dry_run=self.dry_run,
            cleanup_success_worktree=self.cleanup_success_worktree,
            label=self.label,
            active_slot=self.active_slot,
            max_runs_per_5h=self.max_runs_per_5h,
            conservative_runs_per_5h=self.conservative_runs_per_5h,
            min_five_hour_remaining_percent=self.min_five_hour_remaining_percent,
            min_weekly_remaining_percent=self.min_weekly_remaining_percent,
            min_disk_free_gib=self.min_disk_free_gib,
            min_mem_available_gib=self.min_mem_available_gib,
            max_load_per_cpu=self.max_load_per_cpu,
            usage_probe_path=self.usage_probe_path or repo_dir / "scripts" / "codex-usage-probe.py",
            lease_timeout_s=self.lease_timeout_s,
        )

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> RunnerConfig:
        env = environ if environ is not None else os.environ
        root = Path(env.get("JOBSEEK_CODEX_RUNNER_ROOT", str(DEFAULT_ROOT)))
        codex_args = tuple(
            part
            for part in env.get(
                "JOBSEEK_CODEX_ARGS",
                "codex exec --json --dangerously-bypass-approvals-and-sandbox",
            ).split()
            if part
        )
        return cls(
            root=root,
            repo_dir=(
                Path(env["JOBSEEK_CODEX_REPO_DIR"]) if env.get("JOBSEEK_CODEX_REPO_DIR") else None
            ),
            max_runtime_s=int(env.get("JOBSEEK_CODEX_MAX_RUNTIME_S", DEFAULT_RUNTIME_S)),
            kill_grace_s=int(env.get("JOBSEEK_CODEX_KILL_GRACE_S", DEFAULT_KILL_GRACE_S)),
            max_runs_per_5h=int(env.get("JOBSEEK_CODEX_MAX_RUNS_PER_5H", "5")),
            conservative_runs_per_5h=int(env.get("JOBSEEK_CODEX_CONSERVATIVE_RUNS_PER_5H", "1")),
            min_five_hour_remaining_percent=float(
                env.get("JOBSEEK_CODEX_MIN_5H_REMAINING_PERCENT", "5")
            ),
            min_weekly_remaining_percent=float(
                env.get("JOBSEEK_CODEX_MIN_WEEKLY_REMAINING_PERCENT", "3")
            ),
            min_disk_free_gib=float(env.get("JOBSEEK_CODEX_MIN_DISK_FREE_GIB", "5")),
            min_mem_available_gib=float(env.get("JOBSEEK_CODEX_MIN_MEM_AVAILABLE_GIB", "2")),
            max_load_per_cpu=float(env.get("JOBSEEK_CODEX_MAX_LOAD_PER_CPU", "2")),
            lease_timeout_s=int(env.get("JOBSEEK_CODEX_LEASE_TIMEOUT_S", str(4 * 60 * 60))),
            dry_run=env.get("JOBSEEK_CODEX_DRY_RUN", "").lower() in {"1", "true", "yes"},
            cleanup_success_worktree=env.get("JOBSEEK_CODEX_KEEP_SUCCESS_WORKTREE", "").lower()
            not in {"1", "true", "yes"},
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
                    completed_at INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS runs_one_active_slot
                    ON runs(active_slot)
                    WHERE state IN ('claimed', 'running') AND active_slot IS NOT NULL;
                CREATE UNIQUE INDEX IF NOT EXISTS runs_one_active_issue
                    ON runs(issue)
                    WHERE state IN ('claimed', 'running') AND issue IS NOT NULL;
                CREATE TABLE IF NOT EXISTS trace_ingestions (
                    trace_path TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL,
                    output_tokens INTEGER NOT NULL,
                    cached_input_tokens INTEGER NOT NULL,
                    events_with_usage INTEGER NOT NULL,
                    ingested_at INTEGER NOT NULL
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
        }.items():
            if name not in existing:
                conn.execute(ddl)

    def acquire(
        self,
        *,
        run_id: str,
        issue: int,
        active_slot: str,
        lease_expires_at: int | None = None,
    ) -> bool:
        now = int(time.time())
        try:
            with self._connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    """
                    INSERT INTO runs (
                        run_id, issue, active_slot, state, created_at,
                        updated_at, heartbeat_at, lease_expires_at
                    ) VALUES (?, ?, ?, 'claimed', ?, ?, ?, ?)
                    """,
                    (run_id, issue, active_slot, now, now, now, lease_expires_at),
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
            conn.execute(f"UPDATE runs SET {assignments} WHERE run_id = ?", values)

    def finish(self, run_id: str, state: str, *, error: str | None = None) -> None:
        if state not in TERMINAL_STATES:
            raise ValueError(f"invalid terminal state: {state}")
        self.update(run_id, state=state, completed_at=int(time.time()), error=error)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def count_recent_runs(self, *, active_slot: str, since: int) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM runs
                WHERE active_slot = ?
                  AND created_at >= ?
                  AND state IN ('claimed', 'running', 'completed', 'failed', 'timeout')
                """,
                (active_slot, since),
            ).fetchone()
        return int(row["count"]) if row else 0

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


class GitHubCoordinator:
    """Thin wrapper around existing GitHub helpers plus owned claim comments."""

    def check_auth(self) -> bool:
        from src.workspace.git import check_gh_auth

        return check_gh_auth()

    def fetch_oldest_open_issue(self, label: str) -> int | None:
        from src.workspace.git import fetch_oldest_open_issue

        return fetch_oldest_open_issue(label=label)

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
                "number,title,url,headRefName",
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

    def issue_is_closed(self, issue: int) -> bool:
        import json

        from src.workspace import git

        result = git._run(  # noqa: SLF001
            [
                "gh",
                "issue",
                "view",
                str(issue),
                *git._gh_repo_flag(),  # noqa: SLF001
                "--json",
                "state",
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
        return state.upper() == "CLOSED"

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
- Stop after `ws task complete`, `ws reject`, or an unrecoverable `ws task fail`.
"""


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
        return HostHealth(False, f"disk free {free_gib:.1f}GiB below threshold")

    mem_available = _mem_available_gib()
    if mem_available is not None and mem_available < config.min_mem_available_gib:
        return HostHealth(False, f"memory available {mem_available:.1f}GiB below threshold")

    try:
        load1, _, _ = os.getloadavg()
    except OSError:
        return HostHealth(True)
    cpus = max(1, os.cpu_count() or 1)
    max_load = cpus * config.max_load_per_cpu
    if load1 > max_load:
        return HostHealth(False, f"load {load1:.2f} above threshold {max_load:.2f}")
    return HostHealth(True)


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
        issue = self.github.fetch_oldest_open_issue(self.config.label)
        if issue is None:
            return None

        run_id = self._new_run_id(issue)
        lease_expires_at = int(time.time()) + self.config.lease_timeout_s
        if not self.ledger.acquire(
            run_id=run_id,
            issue=issue,
            active_slot=self.config.active_slot,
            lease_expires_at=lease_expires_at,
        ):
            return None

        claim_id = self.github.claim_issue(issue, run_id)
        if claim_id is None:
            self.ledger.finish(run_id, "skipped", error="could not post claim")
            return None
        self.ledger.update(run_id, claim_comment_id=claim_id)

        try:
            if self.github.check_existing_prs(issue):
                self.github.delete_claim(claim_id)
                self.ledger.finish(run_id, "skipped", error="open PR appeared before launch")
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

    def run_once(self) -> RunResult:
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
            return self._execute_admission(admission)
        except Exception as exc:  # noqa: BLE001 - final guard for claimed issues
            self._release_claim_if_unresolved(admission)
            self.ledger.finish(admission.run_id, "failed", error=str(exc))
            return RunResult(
                run_id=admission.run_id,
                issue=admission.issue,
                state="failed",
                error=str(exc),
            )

    def should_start(self) -> SchedulerDecision:
        self.reconcile_stale_runs()
        health = check_host_health(self.config)
        if not health.ok:
            return SchedulerDecision(
                should_run=False,
                reason=health.reason or "host health gate failed",
                recent_limit=0,
                recent_runs=0,
            )

        usage = self._probe_usage()
        recent_limit = self._recent_run_limit(usage)
        recent_runs = self.ledger.count_recent_runs(
            active_slot=self.config.active_slot,
            since=int(time.time()) - FIVE_HOURS_S,
        )
        if recent_runs >= recent_limit:
            return SchedulerDecision(
                should_run=False,
                reason="five-hour run budget exhausted",
                recent_limit=recent_limit,
                recent_runs=recent_runs,
                usage=usage,
            )

        retry_after = self._usage_retry_after(usage)
        if retry_after is not None:
            return SchedulerDecision(
                should_run=False,
                reason="Codex usage window below threshold",
                recent_limit=recent_limit,
                recent_runs=recent_runs,
                usage=usage,
                retry_after_s=retry_after,
            )

        return SchedulerDecision(
            should_run=True,
            reason="admitted",
            recent_limit=recent_limit,
            recent_runs=recent_runs,
            usage=usage,
        )

    def _probe_usage(self) -> UsageProbeResult | None:
        probe = self.config.usage_probe_path
        if not probe or not probe.exists():
            return UsageProbeResult(ok=False, error="usage probe not found")
        return run_usage_probe(probe)

    def _recent_run_limit(self, usage: UsageProbeResult | None) -> int:
        cfg = self.config
        conservative = max(1, min(cfg.conservative_runs_per_5h, cfg.max_runs_per_5h))
        if not usage or not usage.ok:
            return conservative

        weekly = _window(usage, "weekly")
        if weekly is None or weekly.remaining_percent is None or weekly.reset_in_seconds is None:
            return conservative

        remaining = weekly.remaining_percent
        seconds_left = weekly.reset_in_seconds
        if remaining >= 50 and seconds_left <= 2 * 24 * 60 * 60:
            return cfg.max_runs_per_5h
        if remaining >= 25 and seconds_left <= 24 * 60 * 60:
            return cfg.max_runs_per_5h
        if remaining >= 25:
            return max(conservative, min(3, cfg.max_runs_per_5h))
        return conservative

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
            self.ledger.finish(run_id, "failed", error="stale lease expired")

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

        cmd = [*cfg.codex_args, build_codex_prompt(admission.issue)]
        with trace_path.open("w") as stdout, stderr_path.open("w") as stderr:
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
                summary = parse_codex_usage_jsonl(trace_path)
                self.ledger.ingest_trace_once(admission.run_id, trace_path, summary)
                self._record_pr_if_present(admission)
                self._release_claim_if_unresolved(admission, worktree=worktree)
                self.ledger.finish(admission.run_id, "timeout", error="codex runtime exceeded")
                return RunResult(
                    run_id=admission.run_id,
                    issue=admission.issue,
                    state="timeout",
                    trace_path=trace_path,
                    stderr_path=stderr_path,
                    worktree_path=worktree,
                    error="codex runtime exceeded",
                )

        summary = parse_codex_usage_jsonl(trace_path)
        self.ledger.ingest_trace_once(admission.run_id, trace_path, summary)
        resolution_confirmed = self._record_resolution(admission, worktree=worktree)
        state = "completed" if exit_code == 0 and resolution_confirmed else "failed"
        if exit_code == 0 and not resolution_confirmed:
            error = (
                "codex exited 0 but no ws completion, PR completion, or closed issue was confirmed"
            )
        else:
            error = None if exit_code == 0 else f"exit {exit_code}"
        if state != "completed":
            self._release_claim_if_unresolved(admission, worktree=worktree)
        self.ledger.finish(admission.run_id, state, error=error)
        if state == "completed" and cfg.cleanup_success_worktree:
            self._cleanup_ws_artifacts_for_issue(
                admission.issue,
                workspace_root=worktree / "apps" / "crawler" / ".workspace",
            )
            self._cleanup_worktree(worktree)
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

    def _cleanup_worktree(self, worktree: Path) -> None:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(worktree)],
            cwd=self.config.repo_dir,
            check=False,
        )

    def _new_run_id(self, issue: int) -> str:
        return f"issue-{issue}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    def _record_pr_if_present(self, admission: Admission) -> None:
        try:
            prs = self.github.check_existing_prs(admission.issue)
        except GitHubStateError:
            return
        if prs:
            self._record_pr(admission, prs[0])

    def _record_resolution(self, admission: Admission, *, worktree: Path | None) -> bool:
        prs = self.github.check_existing_prs(admission.issue)
        if prs:
            self._record_pr(admission, prs[0])
        if self.github.issue_is_closed(admission.issue):
            return True
        return bool(prs and worktree and _ws_issue_completed(worktree, admission.issue))

    def _record_pr(self, admission: Admission, pr: dict[str, Any]) -> None:
        number = _int_or_none(pr.get("number"))
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

    def _cleanup_ws_artifacts_for_issue(
        self,
        issue: int,
        *,
        workspace_root: Path | None = None,
    ) -> None:
        roots = []
        if workspace_root is not None:
            roots.append(workspace_root)
        if self.config.repo_dir is not None:
            roots.append(self.config.repo_dir / "apps" / "crawler" / ".workspace")
        roots.append(Path.home() / ".jobseek" / "repo" / "apps" / "crawler" / ".workspace")

        seen: set[Path] = set()
        for root in roots:
            root = root.resolve() if root.exists() else root
            if root in seen or not root.exists():
                continue
            seen.add(root)
            for workspace_dir in _workspace_dirs_for_issue(root, issue):
                self._cleanup_workspace_dir(workspace_dir, root)

    def _cleanup_workspace_dir(self, workspace_dir: Path, workspace_root: Path) -> None:
        data = _read_yaml_mapping(workspace_dir / "workspace.yaml")
        if not data:
            return
        slug = workspace_dir.name
        worktree = _workspace_worktree(data)
        if worktree:
            managed_repo = Path.home() / ".jobseek" / "repo"
            cwd = managed_repo if managed_repo.exists() else self.config.repo_dir
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree],
                cwd=cwd,
                check=False,
            )
            shutil.rmtree(worktree, ignore_errors=True)
        shutil.rmtree(workspace_dir, ignore_errors=True)
        for active in workspace_root.glob("active*"):
            try:
                if active.is_file() and active.read_text().strip() == slug:
                    active.unlink()
            except OSError:
                continue


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
    for workspace_yaml in workspace_root.glob("*/workspace.yaml"):
        data = _read_yaml_mapping(workspace_yaml)
        if _workspace_issue(data) == issue:
            matches.append(workspace_yaml.parent)
    return matches


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
                "error": result.error,
            },
            sort_keys=True,
        )
    )
    return 0 if result.state in {"completed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
