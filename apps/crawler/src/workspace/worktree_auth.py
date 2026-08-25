"""Exact persisted-identity authentication for managed workspace pivots."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from src.workspace.errors import WorkspaceError

if TYPE_CHECKING:
    from src.workspace.state import Workspace


WORKTREE_IDENTITY_KEYS = {
    "version",
    "path",
    "slug",
    "branch",
    "head",
    "dev",
    "ino",
    "issue",
    "pr",
    "pr_provenance",
}


def authenticate_workspace_worktree(ws: Workspace) -> Path:
    """Authenticate and return the exact canonical managed worktree path."""
    from src.workspace import git

    canonical = Path(os.path.abspath(str(git.worktrees_dir() / ws.slug)))
    if not ws.worktree:
        raise WorkspaceError("Workspace is missing its authenticated worktree path")
    recorded = Path(os.path.abspath(os.path.expanduser(ws.worktree)))
    if recorded != canonical:
        raise WorkspaceError("Workspace records a non-canonical managed worktree path")

    identity = ws.worktree_identity
    if not isinstance(identity, dict) or set(identity) != WORKTREE_IDENTITY_KEYS:
        raise WorkspaceError("Workspace is missing its authenticated worktree identity")
    if (
        identity.get("version") != 1
        or identity.get("path") != str(canonical)
        or identity.get("slug") != ws.slug
        or identity.get("branch") != ws.branch
        or identity.get("issue") != ws.issue
        or identity.get("pr") != ws.pr
        or identity.get("pr_provenance") != ws.pr_provenance
        or not isinstance(identity.get("head"), str)
        or not isinstance(identity.get("dev"), int)
        or not isinstance(identity.get("ino"), int)
    ):
        raise WorkspaceError("Workspace worktree identity contradicts ownership provenance")
    if not git.authenticate_managed_worktree(
        canonical,
        ws.branch,
        identity["head"],
        expected_dev=identity["dev"],
        expected_ino=identity["ino"],
    ):
        raise WorkspaceError("Authenticated workspace worktree disappeared")
    if git.local_branch_oid_strict(ws.branch) != identity["head"]:
        raise WorkspaceError("Workspace local ref contradicts authenticated worktree")
    return canonical


def pivot_to_authenticated_worktree(ws: Workspace) -> Path:
    """Authenticate before and after changing the process repository root."""
    from src.shared.constants import get_repo_root, set_repo_root

    canonical = authenticate_workspace_worktree(ws)
    if get_repo_root() != canonical:
        set_repo_root(canonical)
    if authenticate_workspace_worktree(ws) != canonical:
        raise WorkspaceError("Workspace worktree identity changed during repository pivot")
    return canonical
