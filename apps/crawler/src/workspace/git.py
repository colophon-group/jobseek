"""Git and GitHub CLI subprocess wrappers."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from src.workspace.errors import GitCommandError, GitHubApiError

_GIT_RETRIES = 2
_GH_RETRIES = 2
_RETRY_DELAY = 2.0

_DEFAULT_REPO = "colophon-group/jobseek"


def _repo_cwd() -> Path | None:
    """Return the repo root for use as subprocess cwd."""
    from src.shared.constants import get_repo_root

    return get_repo_root()


def _gh_repo_flag() -> list[str]:
    """Return ['--repo', 'owner/repo'] when no repo root is available."""
    from src.shared.constants import get_repo_root

    if get_repo_root() is None:
        repo = os.environ.get("WS_REPO", _DEFAULT_REPO)
        return ["--repo", repo]
    return []


_MANAGED_REPO = Path.home() / ".jobseek" / "repo"


def _managed_repo_url() -> str:
    return os.environ.get(
        "WS_REPO_URL",
        "https://github.com/colophon-group/jobseek.git",
    )


def purge_clone() -> None:
    """Remove the managed clone entirely."""
    import shutil

    if _MANAGED_REPO.exists():
        shutil.rmtree(_MANAGED_REPO)


def _resolve_csv_conflicts(cwd: Path) -> bool:
    """Resolve CSV merge conflicts by accepting both sides and re-sorting.

    Returns True if conflicts were resolved, False if non-CSV conflicts remain.
    """
    # List conflicted files
    result = _run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        cwd=cwd,
        check=False,
    )
    conflicted = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    if not conflicted:
        return True

    csv_files = {"apps/crawler/data/companies.csv", "apps/crawler/data/boards.csv"}
    non_csv = [f for f in conflicted if f not in csv_files]
    if non_csv:
        return False  # Code conflicts — cannot auto-resolve

    # For each CSV conflict: accept both sides (union merge) then re-sort
    from src.shared.csv_io import read_csv as _read_csv
    from src.shared.csv_io import write_csv as _write_csv

    for csv_rel in conflicted:
        csv_path = cwd / csv_rel

        # Read ours and theirs, merge rows by deduplicating on key
        # Use git to get clean versions
        ours = _run(["git", "show", f":2:{csv_rel}"], cwd=cwd, check=False)
        theirs = _run(["git", "show", f":3:{csv_rel}"], cwd=cwd, check=False)

        if ours.returncode != 0 or theirs.returncode != 0:
            return False

        # Write ours to a temp file, read it, then merge theirs
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(ours.stdout)
            ours_path = Path(f.name)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(theirs.stdout)
            theirs_path = Path(f.name)

        try:
            headers, ours_rows = _read_csv(ours_path)
            _, theirs_rows = _read_csv(theirs_path)

            # Determine key field
            if "slug" in headers:
                key_field = "slug"
            elif "company_slug" in headers:
                key_field = "board_slug"
            else:
                return False

            # Merge: theirs wins on conflict, both kept otherwise
            merged: dict[str, dict] = {}
            for row in ours_rows:
                merged[row.get(key_field, "")] = row
            for row in theirs_rows:
                merged[row.get(key_field, "")] = row

            rows = list(merged.values())

            # Sort like sort_csvs does
            if "slug" in headers:
                rows.sort(key=lambda r: r.get("slug", ""))
            else:
                rows.sort(key=lambda r: (r.get("company_slug", ""), r.get("board_slug", "")))

            _write_csv(csv_path, headers, rows)
        finally:
            ours_path.unlink(missing_ok=True)
            theirs_path.unlink(missing_ok=True)

        _run(["git", "add", csv_rel], cwd=cwd)

    return True


def ensure_clone(*, reset: bool = False) -> Path:
    """Ensure repo is cloned at ~/.jobseek/repo/ with latest main.

    When *reset* is True, the managed clone is purged and re-cloned from
    scratch.  Otherwise an existing clone is updated to the latest
    ``origin/main``, with CSV-only merge conflicts auto-resolved by
    union-merging and re-sorting (matching ``sort_csvs()``).  Non-CSV
    conflicts cause an error directing the user to ``--reset``.

    Returns the repo root path.
    """
    managed = _MANAGED_REPO
    repo_url = _managed_repo_url()

    if reset:
        purge_clone()

    if (managed / "apps" / "crawler" / "data").exists():
        _run(["git", "fetch", "origin"], cwd=managed)
        main = get_main_branch_remote(cwd=managed)

        # Ensure on main, discarding any leftover index state
        _run(["git", "checkout", main], cwd=managed, check=False)
        _run(["git", "reset", "--hard", f"origin/{main}"], cwd=managed)
        return managed

    # Fresh clone
    managed.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", repo_url, str(managed)],
        check=True,
        capture_output=True,
        text=True,
    )
    return managed


def sync_branch_with_main(branch: str) -> None:
    """Rebase *branch* onto latest main, auto-resolving CSV conflicts.

    Called after ``ensure_clone()`` when the workspace already has a
    feature branch.  Fetches, rebases, and if CSV conflicts appear they
    are resolved the same way the submit workflow does it (union-merge +
    sort).  Non-CSV conflicts abort with a message pointing to
    ``--reset``.
    """
    from src.workspace.errors import WorkspaceError

    cwd = _repo_cwd()
    main = get_main_branch_remote(cwd=cwd)

    _run(["git", "fetch", "origin"], cwd=cwd)
    _run(["git", "checkout", branch], cwd=cwd)

    result = _run(
        ["git", "rebase", f"origin/{main}"],
        cwd=cwd,
        check=False,
    )
    if result.returncode == 0:
        return  # Clean rebase

    # Rebase paused on conflicts — try to resolve
    if _resolve_csv_conflicts(cwd):
        cont = _run(["git", "rebase", "--continue"], cwd=cwd, check=False)
        if cont.returncode == 0:
            return
        # May be more conflict commits; loop
        for _ in range(20):  # safety bound
            if not _resolve_csv_conflicts(cwd):
                break
            cont = _run(["git", "rebase", "--continue"], cwd=cwd, check=False)
            if cont.returncode == 0:
                return

    # Could not resolve — abort and error out
    _run(["git", "rebase", "--abort"], cwd=cwd, check=False)
    raise WorkspaceError(
        "Non-CSV merge conflicts detected in the managed clone. "
        "Run with --reset to purge and re-clone."
    )


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

    When *cwd* is not given, defaults to the detected repo root (if any).
    When *check* is True, ``CalledProcessError`` is translated into
    ``GitCommandError`` (for ``git``) or ``GitHubApiError`` (for ``gh``).
    Transient failures are retried up to *retries* times.
    """
    if cwd is None:
        cwd = _repo_cwd()

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


def create_branch(name: str, start_point: str | None = None) -> None:
    """Create and checkout a new branch.

    When *start_point* is given (e.g. ``origin/main``), the branch is
    created from that ref instead of the current HEAD.
    """
    args = ["git", "checkout", "-b", name]
    if start_point:
        args.append(start_point)
    _run(args)


def fetch() -> None:
    """Fetch latest from origin."""
    _run(["git", "fetch", "origin"], retries=_GIT_RETRIES)


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
            *_gh_repo_flag(),
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
        ["gh", "issue", "comment", str(issue_number), *_gh_repo_flag(), "--body", body],
        retries=_GH_RETRIES,
    )


def fetch_issue(issue_number: int) -> dict:
    """Fetch a GitHub issue's title, body, and labels.

    Returns dict with 'title', 'body', 'labels' keys.
    """
    import json

    result = _run(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            *_gh_repo_flag(),
            "--json",
            "title,body,labels,url",
        ],
        retries=_GH_RETRIES,
    )
    return json.loads(result.stdout)


def close_issue(issue_number: int) -> None:
    """Close a GitHub issue."""
    _run(["gh", "issue", "close", str(issue_number), *_gh_repo_flag()], retries=_GH_RETRIES)


def edit_pr_body(pr_number: int, body: str) -> None:
    """Update a PR's body text."""
    _run(
        ["gh", "pr", "edit", str(pr_number), "--body", body],
        retries=_GH_RETRIES,
    )


def close_pr(pr_number: int) -> None:
    """Close a GitHub PR."""
    _run(["gh", "pr", "close", str(pr_number)], retries=_GH_RETRIES)


def repo_name_with_owner() -> str:
    """Return 'owner/repo' for the current GitHub repository."""
    result = _run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    return result.stdout.strip()


def get_main_branch() -> str:
    """Detect the default branch (main or master)."""
    return get_main_branch_remote()


def get_main_branch_remote(*, cwd: Path | None = None) -> str:
    """Detect the default branch (main or master), with explicit *cwd*."""
    result = _run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=cwd,
        check=False,
    )
    if result.returncode == 0:
        # refs/remotes/origin/main -> main
        return result.stdout.strip().split("/")[-1]
    return "main"
