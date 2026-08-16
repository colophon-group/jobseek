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


def pivot_to_workspace_worktree(ws: Workspace) -> PreflightIssue | None:
    """Point repo-scoped operations at the workspace's managed worktree.

    CLI startup normally performs this pivot from the active-workspace
    pointer.  That pointer is intentionally TTY-scoped, though, so a command
    that supplies the workspace slug explicitly may run in a different PTY
    without an active pointer.  Use the workspace record itself as the
    authoritative fallback before running git checks or board commands.
    """
    if not ws.worktree:
        return None

    from src.shared.constants import get_repo_root, set_repo_root
    from src.workspace import git

    configured = Path(ws.worktree).expanduser()
    try:
        worktree = configured.resolve(strict=True)
        expected = (git.worktrees_dir() / ws.slug).resolve(strict=True)
    except OSError:
        return PreflightIssue(
            "worktree_missing",
            f"Managed worktree for {ws.slug!r} is missing: {configured}",
            "critical",
        )

    if worktree != expected:
        return PreflightIssue(
            "worktree_mismatch",
            f"Workspace {ws.slug!r} records an unexpected worktree path: {configured}",
            "critical",
        )

    if not (worktree / ".git").exists() or not (worktree / "apps" / "crawler" / "data").is_dir():
        return PreflightIssue(
            "worktree_invalid",
            f"Managed worktree for {ws.slug!r} is not a valid Jobseek checkout: {worktree}",
            "critical",
        )

    if get_repo_root() != worktree:
        set_repo_root(worktree)
    return None


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

    if ws.worktree:
        worktree_issue = pivot_to_workspace_worktree(ws)
        if worktree_issue:
            return [worktree_issue]

    if check_branch and ws.branch:
        from src.workspace import git

        try:
            # Workspace state is anchored to the resolver's outer worktree,
            # while the feature branch lives in the managed company worktree.
            # Check the branch where it actually lives even when the CLI could
            # not pivot automatically (for example, when active-workspace
            # session markers are unavailable to a non-interactive command).
            cwd = Path(ws.worktree) if ws.worktree else None

            # Check if expected branch exists locally
            result = git._run(
                ["git", "branch", "--list", ws.branch],
                cwd=cwd,
                check=False,
            )
            if ws.branch not in result.stdout:
                issues.append(
                    PreflightIssue(
                        "branch_missing",
                        f"Branch {ws.branch!r} not found locally",
                        "critical",
                    )
                )
            else:
                current = git._run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=cwd,
                ).stdout.strip()
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
