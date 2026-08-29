"""Shared constants used across csvtool, inspect, and workspace."""

from __future__ import annotations

import re
from pathlib import Path

_MODULE_FILE = Path(__file__).resolve()
_CHECKOUT_CRAWLER_ROOT = _MODULE_FILE.parent.parent.parent
_INSTALLED_DATA_DIR = Path("/app/data")

# Module-level constants are retained for checked-out developer/test callers.
# Installed runtime consumers must use ``get_data_dir()`` so a wheel can never
# accidentally treat a sibling ``site-packages/data`` directory as authority.
DATA_DIR = _CHECKOUT_CRAWLER_ROOT / "data"
WORKSPACE_DIR = _CHECKOUT_CRAWLER_ROOT / ".workspace"

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
URL_RE = re.compile(r"^https?://[^\s/]+")
LOGO_TYPES = ("wordmark", "wordmark+icon", "icon")
DISPLAY_LOCALES: tuple[str, ...] = ("en", "de", "fr", "it")

# ── Dynamic repo root (set at CLI startup) ────────────────────────────

_repo_root: Path | None = None
_workspace_root: Path | None = None


def set_repo_root(path: Path) -> None:
    """Set the repo root for path resolution.

    The first call also anchors the workspace directory.  Subsequent
    calls (e.g. pivoting to a worktree) update data/git paths but
    leave the workspace dir unchanged so that state files are always
    found in the same place.
    """
    global _repo_root, _workspace_root
    _repo_root = path
    if _workspace_root is None:
        _workspace_root = path


def get_repo_root() -> Path | None:
    """Return the repo root, or None if not detected."""
    return _repo_root


def is_source_checkout() -> bool:
    """Return whether this imported module is the checkout's source file."""

    source_file = _CHECKOUT_CRAWLER_ROOT / "src" / "shared" / "constants.py"
    return (
        (_CHECKOUT_CRAWLER_ROOT / "pyproject.toml").is_file()
        and source_file.is_file()
        and source_file.resolve() == _MODULE_FILE
    )


def get_data_dir() -> Path:
    """Return the sole authoritative CSV root for this execution mode.

    Workspace commands may explicitly pivot to another checkout. Normal source
    execution uses the structurally verified checkout containing this module.
    An installed wheel uses only the read-only ``/app/data`` image contract and
    fails closed when that directory is absent; it never probes wheel-relative
    or copied source-tree fallbacks.
    """

    if _repo_root:
        return _repo_root / "apps" / "crawler" / "data"
    if is_source_checkout():
        return DATA_DIR
    if _INSTALLED_DATA_DIR.is_dir():
        return _INSTALLED_DATA_DIR
    raise RuntimeError("installed crawler runtime requires the /app/data directory")


def get_workspace_dir() -> Path:
    """Return the workspace directory, anchored to the initial repo root.

    Unlike ``get_data_dir()``, this does NOT follow worktree pivots so
    that workspace state is always stored in one stable location.
    """
    if _workspace_root:
        return _workspace_root / "apps" / "crawler" / ".workspace"
    return WORKSPACE_DIR
