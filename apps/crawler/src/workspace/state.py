"""Workspace state management — YAML-backed dataclasses.

A workspace lives at ``.workspace/<slug>/`` and contains:
- ``workspace.yaml`` — company details, git state, submit checkpoint
- ``log.yaml`` — workspace-level action log
- ``boards/<alias>.yaml`` — per-board config, detections, configs, log
- ``artifacts/<alias>/`` — debug artifacts from monitor/scraper runs
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.shared.constants import get_workspace_dir
from src.workspace.filelock import file_lock

# ── Atomic file write ──────────────────────────────────────────────────


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically via write-to-temp + rename."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# ── Board dataclass ────────────────────────────────────────────────────


@dataclass
class Board:
    """Per-board configuration and run results.

    v2 stores all config/run data inside ``configs`` dict, keyed by
    agent-chosen config name.  Properties provide backward-compatible
    read/write access using the ``active_config`` pointer.
    """

    alias: str
    slug: str  # {company_slug}-{alias}
    url: str
    job_link_pattern: str = ""

    active_config: str | None = None
    detections: dict[str, Any] = field(default_factory=dict)
    configs: dict[str, dict[str, Any]] = field(default_factory=dict)
    log: list[dict[str, Any]] = field(default_factory=list)

    # ── Internal helpers ───────────────────────────────────────────

    def _active_cfg(self) -> dict[str, Any]:
        """Return the active config dict, or empty dict if none."""
        if self.active_config and self.active_config in self.configs:
            return self.configs[self.active_config]
        return {}

    def _ensure_cfg(self) -> dict[str, Any]:
        """Return the active config dict, creating one if needed."""
        if not self.active_config:
            self.active_config = "default"
        if self.active_config not in self.configs:
            self.configs[self.active_config] = {}
        return self.configs[self.active_config]

    # ── Backward-compat properties ─────────────────────────────────

    @property
    def monitor_type(self) -> str | None:
        return self._active_cfg().get("monitor_type")

    @monitor_type.setter
    def monitor_type(self, value: str | None) -> None:
        if not self.active_config:
            name = value or "default"
            self.active_config = name
        if self.active_config not in self.configs:
            self.configs[self.active_config] = {}
        self.configs[self.active_config]["monitor_type"] = value

    @property
    def monitor_config(self) -> dict:
        return self._active_cfg().get("monitor_config") or {}

    @monitor_config.setter
    def monitor_config(self, value: dict) -> None:
        self._ensure_cfg()["monitor_config"] = value

    @property
    def scraper_type(self) -> str | None:
        return self._active_cfg().get("scraper_type")

    @scraper_type.setter
    def scraper_type(self, value: str | None) -> None:
        self._ensure_cfg()["scraper_type"] = value

    @property
    def scraper_config(self) -> dict:
        return self._active_cfg().get("scraper_config") or {}

    @scraper_config.setter
    def scraper_config(self, value: dict) -> None:
        self._ensure_cfg()["scraper_config"] = value

    @property
    def monitor_run(self) -> dict:
        return self._active_cfg().get("run") or {}

    @monitor_run.setter
    def monitor_run(self, value: dict) -> None:
        self._ensure_cfg()["run"] = value

    @property
    def scraper_run(self) -> dict:
        return self._active_cfg().get("scraper_run") or {}

    @scraper_run.setter
    def scraper_run(self, value: dict) -> None:
        self._ensure_cfg()["scraper_run"] = value

    # ── Derived state ──────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        """Board is fully configured, tested, and reviewed."""
        if not self.active_config:
            return False
        cfg = self.configs.get(self.active_config)
        if not cfg or cfg.get("status") != "tested":
            return False
        fb = cfg.get("feedback")
        return bool(fb and fb.get("verdict") in ("good", "acceptable"))

    # ── Serialization ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "alias": self.alias,
            "slug": self.slug,
            "url": self.url,
            "active_config": self.active_config,
            "detections": self.detections,
            "configs": self.configs,
        }
        if self.job_link_pattern:
            d["job_link_pattern"] = self.job_link_pattern
        if self.log:
            d["log"] = self.log
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Board:
        if "configs" in data:
            return cls._from_v2(data)
        return cls._from_v1(data)

    @classmethod
    def _from_v2(cls, data: dict[str, Any]) -> Board:
        return cls(
            alias=data["alias"],
            slug=data["slug"],
            url=data["url"],
            job_link_pattern=data.get("job_link_pattern", ""),
            active_config=data.get("active_config"),
            detections=data.get("detections") or {},
            configs=data.get("configs") or {},
            log=data.get("log") or [],
        )

    @classmethod
    def _from_v1(cls, data: dict[str, Any]) -> Board:
        """Migrate a v1 board YAML (monitor/scraper dicts) to v2."""
        monitor = data.get("monitor") or {}
        scraper = data.get("scraper") or {}
        monitor_run = data.get("monitor_run") or {}
        scraper_run = data.get("scraper_run") or {}

        config_entry: dict[str, Any] = {
            "monitor_type": monitor.get("type"),
            "monitor_config": monitor.get("config") or {},
            "scraper_type": scraper.get("type"),
            "scraper_config": scraper.get("config") or {},
            "status": (
                "tested" if monitor_run else ("selected" if monitor.get("type") else "detected")
            ),
            "rich": monitor_run.get("has_rich_data", False),
            "run": monitor_run if monitor_run else {},
            "scraper_run": scraper_run if scraper_run else {},
            "cost": {},
            "feedback": None,
        }

        config_name = monitor.get("type") or "unnamed"
        configs = {config_name: config_entry} if monitor.get("type") else {}
        active_config = config_name if monitor.get("type") else None

        return cls(
            alias=data["alias"],
            slug=data["slug"],
            url=data["url"],
            job_link_pattern="",
            active_config=active_config,
            configs=configs,
            detections={},
            log=data.get("log") or [],
        )


# ── Workspace dataclass ───────────────────────────────────────────────


@dataclass
class Workspace:
    """Top-level workspace state."""

    slug: str
    created_at: str = ""

    # Git/GitHub state
    branch: str = ""
    issue: int | None = None
    pr: int | None = None

    # Company details (staged — written to CSV on submit)
    name: str = ""
    website: str = ""
    logo_url: str = ""
    icon_url: str = ""
    logo_type: str = ""

    # Active board alias
    active_board: str = ""

    # Submit checkpoint for idempotent retry
    submit_state: dict[str, Any] = field(default_factory=dict)

    # Worktree path (empty = legacy single-checkout mode)
    worktree: str = ""

    # Last error from workspace-level commands
    last_error: dict[str, Any] = field(default_factory=dict)

    @property
    def submitted(self) -> bool:
        """All critical submit steps completed."""
        return all(
            self.submit_state.get(k) for k in ("csv_written", "validated", "committed", "pushed")
        )

    def to_dict(self) -> dict[str, Any]:
        git: dict[str, Any] = {
            "branch": self.branch,
            "issue": self.issue,
            "pr": self.pr,
        }
        if self.worktree:
            git["worktree"] = self.worktree
        return {
            "version": 2,
            "slug": self.slug,
            "created_at": self.created_at,
            "git": git,
            "company": {
                "name": self.name,
                "website": self.website,
                "logo_url": self.logo_url,
                "icon_url": self.icon_url,
                "logo_type": self.logo_type,
            },
            "active_board": self.active_board,
            "submit_state": self.submit_state,
            "last_error": self.last_error or None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workspace:
        git = data.get("git") or {}
        company = data.get("company") or {}
        return cls(
            slug=data["slug"],
            created_at=data.get("created_at", ""),
            branch=git.get("branch", ""),
            issue=git.get("issue"),
            pr=git.get("pr"),
            worktree=git.get("worktree", ""),
            name=company.get("name", ""),
            website=company.get("website", ""),
            logo_url=company.get("logo_url", ""),
            icon_url=company.get("icon_url", ""),
            logo_type=company.get("logo_type", ""),
            active_board=data.get("active_board", ""),
            submit_state=data.get("submit_state") or {},
            last_error=data.get("last_error") or {},
        )


# ── File I/O helpers ────────────────────────────────────────────────────


def ws_dir(slug: str) -> Path:
    """Return the workspace directory for a given slug."""
    return get_workspace_dir() / slug


def ws_yaml_path(slug: str) -> Path:
    return ws_dir(slug) / "workspace.yaml"


def ws_log_path(slug: str) -> Path:
    return ws_dir(slug) / "log.yaml"


def board_yaml_path(slug: str, alias: str) -> Path:
    return ws_dir(slug) / "boards" / f"{alias}.yaml"


def artifacts_dir(slug: str, alias: str) -> Path:
    return ws_dir(slug) / "artifacts" / alias


def save_workspace(ws: Workspace) -> None:
    """Write workspace.yaml atomically under advisory lock."""
    path = ws_yaml_path(ws.slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        _atomic_write(path, yaml.dump(ws.to_dict(), default_flow_style=False, sort_keys=False))


@contextmanager
def update_workspace(slug: str) -> Generator[Workspace, None, None]:
    """Atomic read-modify-write for workspace.yaml.

    Usage::

        with update_workspace(slug) as ws:
            ws.active_board = "careers"
        # Automatically saved on exit
    """
    path = ws_yaml_path(slug)
    with file_lock(path):
        ws = load_workspace(slug)
        yield ws
        _atomic_write(path, yaml.dump(ws.to_dict(), default_flow_style=False, sort_keys=False))


def load_workspace(slug: str) -> Workspace:
    """Load workspace.yaml. Raises FileNotFoundError if missing."""
    from src.workspace.errors import WorkspaceStateError

    path = ws_yaml_path(slug)
    if not path.exists():
        raise FileNotFoundError(f"Workspace {slug!r} not found at {path}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise WorkspaceStateError(f"Corrupt workspace YAML for {slug!r}: {e}") from e
    if not isinstance(data, dict):
        raise WorkspaceStateError(
            f"Invalid workspace YAML for {slug!r}: expected mapping, got {type(data).__name__}"
        )
    return Workspace.from_dict(data)


def save_board(slug: str, board: Board) -> None:
    """Write a board YAML file atomically under advisory lock."""
    path = board_yaml_path(slug, board.alias)
    path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(path):
        content = yaml.dump(
            board.to_dict(),
            default_flow_style=False,
            sort_keys=False,
        )
        _atomic_write(path, content)


def load_board(slug: str, alias: str) -> Board:
    """Load a board YAML file. Raises FileNotFoundError if missing."""
    from src.workspace.errors import WorkspaceStateError

    path = board_yaml_path(slug, alias)
    if not path.exists():
        raise FileNotFoundError(f"Board {alias!r} not found for workspace {slug!r}")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise WorkspaceStateError(f"Corrupt board YAML for {alias!r}: {e}") from e
    if not isinstance(data, dict):
        raise WorkspaceStateError(
            f"Invalid board YAML for {alias!r}: expected mapping, got {type(data).__name__}"
        )
    return Board.from_dict(data)


def list_boards(slug: str) -> list[Board]:
    """List all boards for a workspace.

    Boards with corrupt YAML are silently skipped to avoid breaking
    listing commands when a single board file is damaged.
    """
    from src.workspace.errors import WorkspaceStateError

    boards_path = ws_dir(slug) / "boards"
    if not boards_path.exists():
        return []
    boards = []
    for p in sorted(boards_path.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text())
            if not isinstance(data, dict):
                raise WorkspaceStateError(f"Invalid board YAML: {p.name}")
            boards.append(Board.from_dict(data))
        except (yaml.YAMLError, WorkspaceStateError):
            pass  # Skip corrupt board files
    return boards


def list_workspaces() -> list[Workspace]:
    """List all workspaces.

    Workspaces with corrupt YAML are silently skipped.
    """
    from src.workspace.errors import WorkspaceStateError

    ws_root = get_workspace_dir()
    if not ws_root.exists():
        return []
    workspaces = []
    for p in sorted(ws_root.iterdir()):
        yaml_path = p / "workspace.yaml"
        if yaml_path.exists():
            try:
                data = yaml.safe_load(yaml_path.read_text())
                if not isinstance(data, dict):
                    raise WorkspaceStateError(f"Invalid workspace YAML: {p.name}")
                workspaces.append(Workspace.from_dict(data))
            except (yaml.YAMLError, WorkspaceStateError):
                pass  # Skip corrupt workspace files
    return workspaces


def workspace_exists(slug: str) -> bool:
    """Check if a workspace exists."""
    return ws_yaml_path(slug).exists()


def delete_workspace(slug: str) -> None:
    """Remove an entire workspace directory."""
    import shutil

    path = ws_dir(slug)
    if path.exists():
        shutil.rmtree(path)


# ── Active workspace ──────────────────────────────────────────────────


def _tty_suffix() -> str | None:
    """Return a TTY-based suffix to scope the active pointer per terminal.

    This lets multiple agents in separate terminals each have their own
    active workspace without clobbering each other.
    """
    import os

    try:
        tty = os.ttyname(0)  # e.g. /dev/ttys003
        return tty.rsplit("/", 1)[-1]
    except (OSError, AttributeError):
        return None


def _sanitize_suffix(value: str) -> str | None:
    """Return a filesystem-safe suffix token."""
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-._")
    if not cleaned:
        return None
    return cleaned[:64]


def _non_tty_suffix() -> str | None:
    """Return a stable suffix for non-interactive sessions.

    Priority:
    1. ``WS_ACTIVE_SCOPE`` explicit override
    2. Known session identifiers from agent/CI environments
    3. Hash of (parent PID + current working directory)
    """
    import hashlib
    import os

    explicit = _sanitize_suffix(os.environ.get("WS_ACTIVE_SCOPE", "").strip())
    if explicit:
        return f"scope-{explicit}"

    for key in (
        "TERM_SESSION_ID",
        "CODEX_SESSION_ID",
        "CODEX_TASK_ID",
        "CLAUDE_SESSION_ID",
        "CLAUDECODE_SESSION_ID",
        "GITHUB_RUN_ID",
        "GITHUB_JOB",
        "CI_JOB_ID",
        "BUILDKITE_BUILD_ID",
    ):
        value = _sanitize_suffix(os.environ.get(key, "").strip())
        if value:
            return f"{key.lower()}-{value}"

    ppid = str(os.getppid())
    cwd = os.environ.get("PWD", "").strip()
    if not cwd:
        with contextlib.suppress(Exception):
            cwd = os.getcwd()
    digest = hashlib.sha1(f"{ppid}:{cwd}".encode()).hexdigest()[:12]
    return f"proc-{digest}"


def _legacy_active_path() -> Path:
    return get_workspace_dir() / "active"


def _active_path() -> Path:
    suffix = _tty_suffix()
    if suffix:
        return get_workspace_dir() / f"active.{suffix}"
    suffix = _non_tty_suffix()
    if suffix:
        return get_workspace_dir() / f"active.{suffix}"
    return _legacy_active_path()


def get_active_slug() -> str | None:
    """Return the active workspace slug, or None if not set.

    Checks ``WS_ACTIVE`` env var first (allows concurrent agents in
    separate terminals), then checks the session-scoped active file.
    As a compatibility fallback, also checks the legacy ``active`` file.
    """
    import os

    env = os.environ.get("WS_ACTIVE", "").strip()
    if env and workspace_exists(env):
        return env

    paths = [_active_path()]
    legacy = _legacy_active_path()
    if legacy not in paths:
        paths.append(legacy)

    for path in paths:
        if not path.exists():
            continue
        slug = path.read_text().strip()
        if slug and workspace_exists(slug):
            return slug
    return None


def set_active_slug(slug: str) -> None:
    """Set the active workspace slug."""
    path = _active_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(slug)


def clear_active_slug() -> None:
    """Clear the active workspace slug."""
    path = _active_path()
    if path.exists():
        path.unlink()


def resolve_slug(slug: str | None) -> str:
    """Resolve a slug from an explicit argument or the active workspace.

    Raises SystemExit if neither is available.
    """
    if slug:
        return slug
    active = get_active_slug()
    if active:
        return active
    from src.workspace import output as out

    out.die("No active workspace. Provide a slug or run: ws new <slug> --issue N")
    return ""  # unreachable, but keeps type checker happy


def resolve_two_args(first: str, second: str | None) -> tuple[str, str]:
    """Resolve (slug, value) when slug is optional.

    With two args: first is slug, second is value.
    With one arg: it's the value; slug comes from the active workspace.
    """
    if second is not None:
        return first, second
    return resolve_slug(None), first


def resolve_board_alias(slug: str, alias_or_slug: str) -> str:
    """Resolve board identifier to an alias.

    Accepts either board alias (``careers``) or board slug
    (``<company>-careers``). Returns the alias candidate even when unresolved,
    so callers can keep existing "not found" error behavior.
    """
    candidate = (alias_or_slug or "").strip()
    if not candidate:
        return candidate

    # Exact alias match (normal case).
    if board_yaml_path(slug, candidate).exists():
        return candidate

    # Common confusion: passing full board slug where alias is expected.
    prefix = f"{slug}-"
    if candidate.startswith(prefix):
        alias = candidate.removeprefix(prefix)
        if alias and board_yaml_path(slug, alias).exists():
            return alias

    # Fallback: match persisted board.slug values.
    for board in list_boards(slug):
        if board.slug == candidate:
            return board.alias

    return candidate
