"""Workspace state management — YAML-backed dataclasses.

A workspace lives at ``.workspace/<slug>/`` and contains:
- ``workspace.yaml`` — company details, git state, progress
- ``log.yaml`` — workspace-level action log
- ``boards/<alias>.yaml`` — per-board config, run results, board-level log
- ``artifacts/<alias>/`` — debug artifacts from monitor/scraper runs
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.shared.constants import WORKSPACE_DIR


@dataclass
class Board:
    """Per-board configuration and run results."""

    alias: str
    slug: str  # {company_slug}-{alias}
    url: str

    monitor_type: str | None = None
    monitor_config: dict[str, Any] = field(default_factory=dict)

    scraper_type: str | None = None
    scraper_config: dict[str, Any] = field(default_factory=dict)

    # Run results
    monitor_run: dict[str, Any] = field(default_factory=dict)
    scraper_run: dict[str, Any] = field(default_factory=dict)

    # Board-level action log (embedded in the YAML)
    log: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "alias": self.alias,
            "slug": self.slug,
            "url": self.url,
        }
        d["monitor"] = {
            "type": self.monitor_type,
            "config": self.monitor_config,
        }
        d["scraper"] = {
            "type": self.scraper_type,
            "config": self.scraper_config,
        }
        if self.monitor_run:
            d["monitor_run"] = self.monitor_run
        if self.scraper_run:
            d["scraper_run"] = self.scraper_run
        if self.log:
            d["log"] = self.log
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Board:
        monitor = data.get("monitor") or {}
        scraper = data.get("scraper") or {}
        return cls(
            alias=data["alias"],
            slug=data["slug"],
            url=data["url"],
            monitor_type=monitor.get("type"),
            monitor_config=monitor.get("config") or {},
            scraper_type=scraper.get("type"),
            scraper_config=scraper.get("config") or {},
            monitor_run=data.get("monitor_run") or {},
            scraper_run=data.get("scraper_run") or {},
            log=data.get("log") or [],
        )


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

    # Active board alias
    active_board: str = ""

    # Advisory progress tracking
    progress: dict[str, bool] = field(
        default_factory=lambda: {
            "board_added": False,
            "monitor_selected": False,
            "monitor_tested": False,
            "scraper_selected": False,
            "scraper_tested": False,
            "submitted": False,
        }
    )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "version": 1,
            "slug": self.slug,
            "created_at": self.created_at,
            "git": {
                "branch": self.branch,
                "issue": self.issue,
                "pr": self.pr,
            },
            "company": {
                "name": self.name,
                "website": self.website,
                "logo_url": self.logo_url,
                "icon_url": self.icon_url,
            },
            "active_board": self.active_board,
            "progress": self.progress.copy(),
        }
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workspace:
        git = data.get("git") or {}
        company = data.get("company") or {}
        default_progress = {
            "board_added": False,
            "monitor_selected": False,
            "monitor_tested": False,
            "scraper_selected": False,
            "scraper_tested": False,
            "submitted": False,
        }
        progress = data.get("progress") or {}
        merged_progress = {**default_progress, **progress}
        return cls(
            slug=data["slug"],
            created_at=data.get("created_at", ""),
            branch=git.get("branch", ""),
            issue=git.get("issue"),
            pr=git.get("pr"),
            name=company.get("name", ""),
            website=company.get("website", ""),
            logo_url=company.get("logo_url", ""),
            icon_url=company.get("icon_url", ""),
            active_board=data.get("active_board", ""),
            progress=merged_progress,
        )


# ── File I/O helpers ────────────────────────────────────────────────────


def ws_dir(slug: str) -> Path:
    """Return the workspace directory for a given slug."""
    return WORKSPACE_DIR / slug


def ws_yaml_path(slug: str) -> Path:
    return ws_dir(slug) / "workspace.yaml"


def ws_log_path(slug: str) -> Path:
    return ws_dir(slug) / "log.yaml"


def board_yaml_path(slug: str, alias: str) -> Path:
    return ws_dir(slug) / "boards" / f"{alias}.yaml"


def artifacts_dir(slug: str, alias: str) -> Path:
    return ws_dir(slug) / "artifacts" / alias


def save_workspace(ws: Workspace) -> None:
    """Write workspace.yaml."""
    path = ws_yaml_path(ws.slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(ws.to_dict(), default_flow_style=False, sort_keys=False))


def load_workspace(slug: str) -> Workspace:
    """Load workspace.yaml. Raises FileNotFoundError if missing."""
    path = ws_yaml_path(slug)
    if not path.exists():
        raise FileNotFoundError(f"Workspace {slug!r} not found at {path}")
    data = yaml.safe_load(path.read_text())
    return Workspace.from_dict(data)


def save_board(slug: str, board: Board) -> None:
    """Write a board YAML file."""
    path = board_yaml_path(slug, board.alias)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(board.to_dict(), default_flow_style=False, sort_keys=False))


def load_board(slug: str, alias: str) -> Board:
    """Load a board YAML file. Raises FileNotFoundError if missing."""
    path = board_yaml_path(slug, alias)
    if not path.exists():
        raise FileNotFoundError(f"Board {alias!r} not found for workspace {slug!r}")
    data = yaml.safe_load(path.read_text())
    return Board.from_dict(data)


def list_boards(slug: str) -> list[Board]:
    """List all boards for a workspace."""
    boards_path = ws_dir(slug) / "boards"
    if not boards_path.exists():
        return []
    boards = []
    for p in sorted(boards_path.glob("*.yaml")):
        data = yaml.safe_load(p.read_text())
        boards.append(Board.from_dict(data))
    return boards


def list_workspaces() -> list[Workspace]:
    """List all workspaces."""
    if not WORKSPACE_DIR.exists():
        return []
    workspaces = []
    for p in sorted(WORKSPACE_DIR.iterdir()):
        yaml_path = p / "workspace.yaml"
        if yaml_path.exists():
            data = yaml.safe_load(yaml_path.read_text())
            workspaces.append(Workspace.from_dict(data))
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


def _active_path() -> Path:
    return WORKSPACE_DIR / "active"


def get_active_slug() -> str | None:
    """Return the active workspace slug, or None if not set."""
    path = _active_path()
    if path.exists():
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
