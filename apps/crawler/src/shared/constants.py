"""Shared constants used across csvtool, inspect, and workspace."""

from __future__ import annotations

import re
from pathlib import Path

# Module-level constants — correct when running from a checked-out repo
# (i.e. dev mode or CI).  Workspace commands use the getter functions below
# so they pick up the repo root discovered at startup.
DATA_DIR = Path(__file__).parent.parent.parent / "data"
WORKSPACE_DIR = Path(__file__).parent.parent.parent / ".workspace"

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
URL_RE = re.compile(r"^https?://[^\s/]+")

# ── Dynamic repo root (set at CLI startup) ────────────────────────────

_repo_root: Path | None = None


def set_repo_root(path: Path) -> None:
    """Set the repo root for path resolution."""
    global _repo_root
    _repo_root = path


def get_repo_root() -> Path | None:
    """Return the repo root, or None if not detected."""
    return _repo_root


def get_data_dir() -> Path:
    """Return the data directory, relative to repo root if set."""
    if _repo_root:
        return _repo_root / "apps" / "crawler" / "data"
    return DATA_DIR


def get_workspace_dir() -> Path:
    """Return the workspace directory, relative to repo root if set."""
    if _repo_root:
        return _repo_root / "apps" / "crawler" / ".workspace"
    return WORKSPACE_DIR
