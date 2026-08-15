"""Pre-flight validation — lightweight checks before board-scoped commands.

Runs inside ``_resolve_board()`` to catch obvious environment issues
(e.g. wrong branch) without slowing down commands.  Heavy validation
(PR state, board readiness) is reserved for ``ws resume``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.workspace.state import Workspace


@dataclass(slots=True)
class PreflightIssue:
    code: str
    message: str
    severity: str  # "critical" | "warning" | "info"


def pivot_to_workspace_worktree(ws: Workspace) -> None:
    """Point repo-scoped operations at the workspace's managed worktree.

    CLI startup normally performs this pivot from the active-workspace
    pointer.  That pointer is intentionally TTY-scoped, though, so a command
    that supplies the workspace slug explicitly may run in a different PTY
    without an active pointer.  Use the workspace record itself as the
    authoritative fallback before running git checks or board commands.
    """
    if not ws.worktree:
        return

    from src.shared.constants import get_repo_root, set_repo_root

    worktree = Path(ws.worktree)
    if get_repo_root() != worktree and (worktree / "apps" / "crawler" / "data").exists():
        set_repo_root(worktree)


def run_preflight(
    ws: Workspace,
    *,
    check_branch: bool | None = None,
) -> list[PreflightIssue]:
    """Quick sanity checks before executing a command.

    Returns a list of issues found.  Callers decide how to handle them
    (warnings are printed, criticals may abort).

    Skips all checks in local mode (``WS_LOCAL=1``).
    """
    import os

    if os.environ.get("WS_LOCAL", "").strip() in ("1", "true", "yes"):
        return []

    if check_branch is None:
        check_branch = True

    issues: list[PreflightIssue] = []

    if check_branch and ws.branch:
        from src.workspace import git

        pivot_to_workspace_worktree(ws)

        try:
            # Check if expected branch exists locally
            result = git._run(["git", "branch", "--list", ws.branch], check=False)
            if ws.branch not in result.stdout:
                issues.append(
                    PreflightIssue(
                        "branch_missing",
                        f"Branch {ws.branch!r} not found locally",
                        "critical",
                    )
                )
            else:
                current = git.current_branch()
                if current != ws.branch:
                    issues.append(
                        PreflightIssue(
                            "wrong_branch",
                            f"On branch {current!r}, expected {ws.branch!r}",
                            "warning",
                        )
                    )
        except Exception:
            pass  # Don't break commands if git fails

    return issues
