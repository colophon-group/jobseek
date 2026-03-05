"""Git and GitHub CLI subprocess wrappers."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from src.workspace.errors import GitCommandError, GitHubApiError

_GIT_RETRIES = 2
_GH_RETRIES = 2
_RETRY_DELAY = 2.0


def _is_retryable(e: GitCommandError | GitHubApiError) -> bool:
    """Return True if the error looks like a transient network issue."""
    stderr = e.stderr.lower()
    for hint in ("timeout", "timed out", "could not resolve", "connection refused", "502", "503"):
        if hint in stderr:
            return True
    return False


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
    retries: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command and return the result.

    When *check* is True, ``CalledProcessError`` is translated into
    ``GitCommandError`` (for ``git``) or ``GitHubApiError`` (for ``gh``).
    Transient failures are retried up to *retries* times.
    """
    last_err: GitCommandError | GitHubApiError | None = None

    for attempt in range(1 + retries):
        try:
            return subprocess.run(
                args,
                cwd=cwd,
                check=check,
                capture_output=capture,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            is_gh = args[0] == "gh"
            err_cls = GitHubApiError if is_gh else GitCommandError
            last_err = err_cls(cmd=args, returncode=exc.returncode, stderr=exc.stderr or "")

            if attempt < retries and _is_retryable(last_err):
                time.sleep(_RETRY_DELAY)
                continue
            raise last_err from exc

    # Should not reach here, but satisfy type checker
    raise last_err  # type: ignore[misc]


def _repo_root() -> Path:
    """Find the git repository root."""
    result = _run(["git", "rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip())


# ── Git operations ──────────────────────────────────────────────────────


def current_branch() -> str:
    """Return the current git branch name."""
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip()


def has_uncommitted_changes(paths: list[str] | None = None) -> bool:
    """Check if there are staged or unstaged changes (optionally scoped to paths)."""
    args = ["git", "status", "--porcelain"]
    if paths:
        args += ["--", *paths]
    result = _run(args)
    return bool(result.stdout.strip())


def is_ahead_of_remote(branch: str | None = None) -> bool:
    """Check if local branch has unpushed commits.

    Returns True if the branch is ahead of its remote counterpart,
    or if the remote tracking branch doesn't exist yet.
    """
    if branch is None:
        branch = current_branch()
    result = _run(
        ["git", "rev-list", f"origin/{branch}..{branch}", "--count"],
        check=False,
    )
    if result.returncode != 0:
        return True  # Assume ahead if remote doesn't exist
    return int(result.stdout.strip()) > 0


def create_branch(name: str) -> None:
    """Create and checkout a new branch."""
    _run(["git", "checkout", "-b", name])


def checkout(name: str) -> None:
    """Checkout an existing branch."""
    _run(["git", "checkout", name])


def add_files(paths: list[str]) -> None:
    """Stage files for commit."""
    _run(["git", "add", *paths])


def commit(message: str) -> None:
    """Create a git commit."""
    _run(["git", "commit", "-m", message])


def push(branch: str | None = None, set_upstream: bool = False) -> None:
    """Push to remote."""
    args = ["git", "push"]
    if set_upstream and branch:
        args += ["-u", "origin", branch]
    _run(args, retries=_GIT_RETRIES)


def delete_branch(name: str, remote: bool = True) -> None:
    """Delete a local branch and optionally the remote."""
    # Delete local (force)
    _run(["git", "branch", "-D", name], check=False)
    if remote:
        _run(["git", "push", "origin", "--delete", name], check=False)


# ── GitHub CLI operations ───────────────────────────────────────────────


def check_gh_auth() -> bool:
    """Check if GitHub CLI is authenticated. Returns True if OK."""
    result = _run(["gh", "auth", "status"], check=False)
    return result.returncode == 0


def check_existing_prs(issue_number: int) -> list[dict[str, str]]:
    """Check for open PRs that close a given issue.

    Returns list of dicts with 'number', 'title', 'url' keys.
    """
    import json

    result = _run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--search",
            f"Closes #{issue_number}",
            "--json",
            "number,title,url",
        ],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return []


def create_draft_pr(title: str, body: str) -> int:
    """Create a draft PR and return its number.

    ``gh pr create`` prints the PR URL to stdout (e.g.
    ``https://github.com/owner/repo/pull/42``).  We extract the number
    from the trailing path segment.
    """
    result = _run(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--title",
            title,
            "--body",
            body,
        ],
        retries=_GH_RETRIES,
    )
    url = result.stdout.strip()
    # URL format: https://github.com/<owner>/<repo>/pull/<number>
    return int(url.rstrip("/").split("/")[-1])


def mark_pr_ready(pr_number: int) -> None:
    """Mark a draft PR as ready for review."""
    _run(["gh", "pr", "ready", str(pr_number)], retries=_GH_RETRIES)


def comment_on_pr(pr_number: int, body: str) -> None:
    """Add a comment to a PR."""
    _run(["gh", "pr", "comment", str(pr_number), "--body", body], retries=_GH_RETRIES)


def comment_on_issue(issue_number: int, body: str) -> None:
    """Add a comment to an issue."""
    _run(
        ["gh", "issue", "comment", str(issue_number), "--body", body],
        retries=_GH_RETRIES,
    )


def close_issue(issue_number: int) -> None:
    """Close a GitHub issue."""
    _run(["gh", "issue", "close", str(issue_number)], retries=_GH_RETRIES)


def edit_pr_body(pr_number: int, body: str) -> None:
    """Update a PR's body text."""
    _run(
        ["gh", "pr", "edit", str(pr_number), "--body", body],
        retries=_GH_RETRIES,
    )


def close_pr(pr_number: int) -> None:
    """Close a GitHub PR."""
    _run(["gh", "pr", "close", str(pr_number)], retries=_GH_RETRIES)


def get_main_branch() -> str:
    """Detect the default branch (main or master)."""
    result = _run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        check=False,
    )
    if result.returncode == 0:
        # refs/remotes/origin/main -> main
        return result.stdout.strip().split("/")[-1]
    return "main"
