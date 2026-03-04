"""Pre-flight validation — lightweight checks before board-scoped commands.

Runs inside ``_resolve_board()`` to catch obvious environment issues
(e.g. wrong branch) without slowing down commands.  Heavy validation
(PR state, board readiness) is reserved for ``ws resume``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.config import settings

if TYPE_CHECKING:
    from src.workspace.state import Workspace


@dataclass(slots=True)
class PreflightIssue:
    code: str
    message: str
    severity: str  # "critical" | "warning" | "info"


def run_preflight(
    ws: Workspace,
    *,
    check_branch: bool | None = None,
) -> list[PreflightIssue]:
    """Quick sanity checks before executing a command.

    Returns a list of issues found.  Callers decide how to handle them
    (warnings are printed, criticals may abort).
    """
    if not settings.ws_preflight_enabled:
        return []

    if check_branch is None:
        check_branch = settings.ws_preflight_check_branch

    issues: list[PreflightIssue] = []

    if check_branch and ws.branch:
        from src.workspace import git

        try:
            # Check if expected branch exists locally
            result = git._run(["git", "branch", "--list", ws.branch], check=False)
            if ws.branch not in result.stdout:
                issues.append(PreflightIssue(
                    "branch_missing",
                    f"Branch {ws.branch!r} not found locally",
                    "critical",
                ))
            else:
                current = git.current_branch()
                if current != ws.branch:
                    issues.append(PreflightIssue(
                        "wrong_branch",
                        f"On branch {current!r}, expected {ws.branch!r}",
                        "warning",
                    ))
        except Exception:
            pass  # Don't break commands if git fails

    return issues
