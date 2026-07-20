"""Git and GitHub CLI subprocess wrappers."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from src.workspace.errors import GitCommandError, GitHubApiError, WorkspaceError

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
_WORKTREES_DIR = Path.home() / ".jobseek" / "worktrees"


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

    Uses a file lock to prevent races when multiple agents call this
    concurrently.

    Returns the repo root path.
    """
    import fcntl

    managed = _MANAGED_REPO
    repo_url = _managed_repo_url()

    lock_path = managed.parent / "repo.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")  # noqa: SIM115
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX)

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
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def sync_branch_with_main(branch: str) -> None:
    """Merge latest main into *branch*, auto-resolving CSV conflicts.

    Called after ``ensure_clone()`` when the workspace already has a
    feature branch.  Fetches, merges, and if CSV conflicts appear they
    are resolved the same way the submit workflow does it (union-merge +
    sort).  A merge keeps the resumed remote branch pushable without a
    history rewrite; non-CSV conflicts abort without changing the branch.
    """
    from src.workspace.errors import WorkspaceError

    cwd = _repo_cwd()
    if cwd is None:
        raise WorkspaceError("sync_branch_with_main must run inside a git repository")

    main = get_main_branch_remote(cwd=cwd)

    _run(["git", "fetch", "origin"], cwd=cwd)
    _run(["git", "checkout", branch], cwd=cwd)

    result = _run(
        ["git", "merge", "--no-edit", f"origin/{main}"],
        cwd=cwd,
        check=False,
    )
    if result.returncode == 0:
        return

    # Merge paused on conflicts — only the append-only registry CSVs can be
    # resolved automatically.  Code conflicts require operator judgment.
    if _resolve_csv_conflicts(cwd):
        commit = _run(["git", "commit", "--no-edit"], cwd=cwd, check=False)
        if commit.returncode == 0:
            return

    # Could not resolve — abort and error out
    _run(["git", "merge", "--abort"], cwd=cwd, check=False)
    raise WorkspaceError(
        "Could not synchronize the resumed branch with latest main; "
        "non-CSV conflicts require manual resolution."
    )


def managed_repo() -> Path:
    """Return the managed clone directory."""
    return _MANAGED_REPO


def worktrees_dir() -> Path:
    """Return the worktrees directory."""
    return _WORKTREES_DIR


def create_worktree(branch: str, path: Path, start_point: str = "origin/main") -> None:
    """Create a git worktree at *path* on *branch* from *start_point*.

    If *branch* already exists (leftover from a previous run), the
    existing branch is reused and reset to *start_point*.
    If *path* already exists, the old worktree is removed first.
    """
    # Clean up stale worktree at this path
    if path.exists():
        remove_worktree(path)

    path.parent.mkdir(parents=True, exist_ok=True)

    # Check if branch already exists
    result = _run(
        ["git", "branch", "--list", branch],
        cwd=_MANAGED_REPO,
        check=False,
    )
    if branch in result.stdout:
        # Delete the stale branch, then create fresh
        _run(["git", "branch", "-D", branch], cwd=_MANAGED_REPO, check=False)

    _run(
        ["git", "worktree", "add", str(path), "-b", branch, start_point],
        cwd=_MANAGED_REPO,
    )


def remove_worktree(path: Path) -> None:
    """Remove a git worktree."""
    if not path.exists():
        return
    _run(
        ["git", "worktree", "remove", str(path), "--force"],
        cwd=_MANAGED_REPO,
        check=False,
    )


def remove_worktree_strict(path: Path) -> None:
    """Idempotently remove a worktree, raising if cleanup is incomplete."""
    if not path.exists():
        return
    _run(
        ["git", "worktree", "remove", str(path), "--force"],
        cwd=_MANAGED_REPO,
    )
    if path.exists():
        raise WorkspaceError(f"Worktree still exists after removal: {path}")


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
    if retries < 0:
        raise ValueError("retries must be non-negative")

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

    # Should not reach here, but satisfy type checker if the loop invariant changes.
    raise RuntimeError("subprocess retry loop exited without a result")


def _repo_root() -> Path:
    """Find the git repository root."""
    result = _run(["git", "rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip())


# ── Git operations ──────────────────────────────────────────────────────


def current_branch() -> str:
    """Return the current git branch name."""
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip()


def current_commit() -> str:
    """Return the current git commit SHA."""
    result = _run(["git", "rev-parse", "HEAD"])
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
        delete_remote_branch(name)


def delete_remote_branch(name: str) -> None:
    """Delete a remote branch (no-op if it doesn't exist)."""
    _run(["git", "push", "origin", "--delete", name], check=False)


def delete_branch_strict(name: str) -> None:
    """Idempotently delete a local and remote branch with verification."""
    _run(["git", "branch", "-D", name], cwd=_MANAGED_REPO, check=False)
    local = _run(["git", "branch", "--list", name], cwd=_MANAGED_REPO)
    if local.stdout.strip():
        raise WorkspaceError(f"Local branch still exists after deletion: {name}")
    remote = _run(
        ["git", "ls-remote", "--heads", "origin", name],
        cwd=_MANAGED_REPO,
        retries=_GIT_RETRIES,
    )
    if remote.stdout.strip():
        _run(
            ["git", "push", "origin", "--delete", name],
            cwd=_MANAGED_REPO,
            retries=_GIT_RETRIES,
        )
        verify = _run(
            ["git", "ls-remote", "--heads", "origin", name],
            cwd=_MANAGED_REPO,
            retries=_GIT_RETRIES,
        )
        if verify.stdout.strip():
            raise WorkspaceError(f"Remote branch still exists after deletion: {name}")


# ── GitHub CLI operations ───────────────────────────────────────────────


def check_gh_auth() -> bool:
    """Check if GitHub CLI is authenticated. Returns True if OK."""
    result = _run(["gh", "auth", "status"], check=False)
    return result.returncode == 0


def check_existing_prs_strict(issue_number: int) -> list[dict]:
    """Check for open PRs that close a given issue.

    Returns enough metadata to distinguish a resumable resolver draft from a
    ready resolver submission or an unrelated/manual PR.
    """
    import json

    args = [
        "gh",
        "pr",
        "list",
        *_gh_repo_flag(),
        "--state",
        "open",
        "--search",
        f"Closes #{issue_number}",
        "--json",
        "number,title,url,headRefName,isDraft",
    ]
    result = _run(args, retries=_GH_RETRIES)
    try:
        prs = json.loads(result.stdout or "[]")
    except (json.JSONDecodeError, TypeError) as exc:
        raise GitHubApiError(args, 1, "Could not parse linked-PR lookup response") from exc
    if not isinstance(prs, list) or not all(isinstance(pr, dict) for pr in prs):
        raise GitHubApiError(args, 1, "Unexpected linked-PR lookup response")
    return prs


def check_existing_prs(issue_number: int) -> list[dict]:
    """Best-effort compatibility wrapper for non-coordination call sites."""
    try:
        return check_existing_prs_strict(issue_number)
    except (GitHubApiError, GitCommandError):
        return []


def classify_issue_prs(prs: list[dict]) -> str:
    """Classify PRs linked to one company-request issue.

    A single draft on an ``add-company/`` branch is owned by the resolver and
    can be resumed.  Once that PR is ready it represents a submitted outcome.
    A ``fix-crawler/`` PR is also submitted: it is the expected result after
    ``ws task fail`` enters coding mode, but must never be resumed as a company
    workspace.  Any other shape is treated as a manual conflict.
    """
    if not prs:
        return "none"
    if len(prs) != 1:
        return "conflicting"
    pr = prs[0]
    branch = pr.get("headRefName")
    is_draft = pr.get("isDraft")
    if not isinstance(branch, str):
        return "conflicting"
    if branch.startswith("fix-crawler/"):
        return "submitted"
    if not branch.startswith("add-company/"):
        return "conflicting"
    if is_draft is True:
        return "resumable"
    if is_draft is False:
        return "submitted"
    return "conflicting"


def get_pr_branch(pr_number: int) -> str | None:
    """Return the head branch name for a given PR number, or None on failure."""
    import json

    result = _run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            *_gh_repo_flag(),
            "--json",
            "headRefName",
        ],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        data = json.loads(result.stdout)
        return data.get("headRefName")
    except (json.JSONDecodeError, TypeError):
        return None


def find_open_pr_for_branch(branch: str) -> int | None:
    """Return the open PR number for *branch*, if one exists."""
    import json

    args = [
        "gh",
        "pr",
        "list",
        *_gh_repo_flag(),
        "--state",
        "open",
        "--head",
        branch,
        "--limit",
        "1",
        "--json",
        "number",
    ]
    # This lookup is a duplicate-publication gate. Fail closed on GitHub
    # errors instead of interpreting them as "no PR exists".
    result = _run(args, retries=_GH_RETRIES)
    if not result.stdout.strip():
        return None
    try:
        prs = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GitHubApiError(
            cmd=args,
            returncode=1,
            stderr="Could not parse open-PR lookup response",
        ) from exc
    if not prs:
        return None
    number = prs[0].get("number")
    return int(number) if number is not None else None


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


def comment_on_issue_once(issue_number: int, marker: str, body: str) -> None:
    """Post a marker-owned issue comment only when it is not already present."""
    import json

    result = _run(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            *_gh_repo_flag(),
            "--json",
            "comments",
        ],
        retries=_GH_RETRIES,
    )
    data = json.loads(result.stdout or "{}")
    comments = data.get("comments", []) if isinstance(data, dict) else []
    if any(
        isinstance(comment, dict)
        and isinstance(comment.get("body"), str)
        and comment["body"].startswith(marker)
        for comment in comments
    ):
        return
    comment_on_issue(issue_number, body)


_CLAIM_MARKER = "<!-- ws-claim -->"
_CLAIM_BODY = f"{_CLAIM_MARKER}\nWorking on it"


def _get_claim_comment_ids(issue_number: int) -> list[int]:
    """Return IDs of claim comments on an issue."""
    import json

    result = _run(
        [
            "gh",
            "api",
            f"repos/{_resolve_repo()}/issues/{issue_number}/comments",
        ],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []
    try:
        comments = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return []
    return [c["id"] for c in comments if c.get("body", "").startswith(_CLAIM_MARKER)]


def _get_claim_comment_ids_strict(issue_number: int) -> list[int]:
    """Return claim IDs, raising when GitHub state cannot be established."""
    import json

    args = [
        "gh",
        "api",
        f"repos/{_resolve_repo()}/issues/{issue_number}/comments",
    ]
    result = _run(args, retries=_GH_RETRIES)
    try:
        comments = json.loads(result.stdout or "[]")
    except (json.JSONDecodeError, TypeError) as exc:
        raise GitHubApiError(args, 1, "Could not parse issue comments response") from exc
    if not isinstance(comments, list):
        raise GitHubApiError(args, 1, "Unexpected issue comments response")
    return [
        comment["id"]
        for comment in comments
        if isinstance(comment, dict)
        and isinstance(comment.get("id"), int)
        and isinstance(comment.get("body"), str)
        and comment["body"].startswith(_CLAIM_MARKER)
    ]


def is_issue_claimed(issue_number: int) -> bool:
    """Check if an issue has an active claim comment."""
    return len(_get_claim_comment_ids(issue_number)) > 0


def claim_issue(issue_number: int) -> None:
    """Add a claim comment to an issue."""
    comment_on_issue(issue_number, _CLAIM_BODY)


def unclaim_issue(issue_number: int) -> None:
    """Remove all claim comments from an issue."""
    for comment_id in _get_claim_comment_ids(issue_number):
        _run(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                f"repos/{_resolve_repo()}/issues/comments/{comment_id}",
            ],
            check=False,
        )


def unclaim_issue_strict(issue_number: int) -> None:
    """Remove all claim comments and verify none remain."""
    for comment_id in _get_claim_comment_ids_strict(issue_number):
        _run(
            [
                "gh",
                "api",
                "--method",
                "DELETE",
                f"repos/{_resolve_repo()}/issues/comments/{comment_id}",
            ],
            retries=_GH_RETRIES,
        )
    if _get_claim_comment_ids_strict(issue_number):
        raise WorkspaceError(f"Issue #{issue_number} still has resolver claim comments")


def _resolve_repo() -> str:
    """Return 'owner/repo' from env or default."""
    return os.environ.get("WS_REPO", _DEFAULT_REPO)


def _fetch_issues_with_open_prs() -> set[int]:
    """Batch-fetch all issue numbers that have an open add-company/ or fix-crawler/ PR."""
    import json
    import re

    result = _run(
        [
            "gh",
            "pr",
            "list",
            *_gh_repo_flag(),
            "--state",
            "open",
            "--limit",
            "200",
            "--json",
            "headRefName,body",
        ],
        retries=_GH_RETRIES,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return set()
    try:
        prs = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return set()

    linked: set[int] = set()
    closes_re = re.compile(r"(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)
    for pr in prs:
        branch = pr.get("headRefName", "")
        if not (branch.startswith("add-company/") or branch.startswith("fix-crawler/")):
            continue
        for m in closes_re.finditer(pr.get("body", "") or ""):
            linked.add(int(m.group(1)))
    return linked


def fetch_oldest_open_issue(
    label: str = "company-request",
    *,
    skip_open_prs: bool = True,
) -> int | None:
    """Return the issue number of the oldest open issue with the given label.

    Skips issues that already have an open ``add-company/`` or
    ``fix-crawler/`` PR linked via "Closes #N", and issues with an
    active claim comment.  Returns ``None`` when no eligible issue exists.
    """
    import concurrent.futures

    result = _run(
        [
            "gh",
            "issue",
            "list",
            *_gh_repo_flag(),
            "--label",
            label,
            "--state",
            "open",
            "--search",
            "sort:created-asc",
            "--limit",
            "100",
            "--json",
            "number",
            "--jq",
            ".[].number",
        ],
        retries=_GH_RETRIES,
    )
    numbers = [int(n) for n in result.stdout.strip().splitlines() if n.strip()]
    if not numbers:
        return None

    candidates = numbers
    if skip_open_prs:
        # Batch-fetch all issues with open PRs (1 API call instead of N).
        issues_with_prs = _fetch_issues_with_open_prs()
        candidates = [n for n in numbers if n not in issues_with_prs]
    if not candidates:
        return None

    # Check claims in parallel (up to 8 concurrent checks)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        claim_map = dict(zip(candidates, pool.map(is_issue_claimed, candidates), strict=True))

    for num in candidates:
        if not claim_map[num]:
            return num

    return None


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


def add_label_to_issue(issue_number: int, label: str) -> None:
    """Add a label to a GitHub issue."""
    _run(
        ["gh", "issue", "edit", str(issue_number), "--add-label", label, *_gh_repo_flag()],
        retries=_GH_RETRIES,
    )


def close_issue(issue_number: int) -> None:
    """Close a GitHub issue."""
    _run(["gh", "issue", "close", str(issue_number), *_gh_repo_flag()], retries=_GH_RETRIES)


def close_issue_if_open(issue_number: int) -> None:
    """Idempotently close an issue and verify its terminal state."""
    state = _run(
        [
            "gh",
            "issue",
            "view",
            str(issue_number),
            *_gh_repo_flag(),
            "--json",
            "state",
            "--jq",
            ".state",
        ],
        retries=_GH_RETRIES,
    ).stdout.strip()
    if state.upper() == "OPEN":
        close_issue(issue_number)
        state = _run(
            [
                "gh",
                "issue",
                "view",
                str(issue_number),
                *_gh_repo_flag(),
                "--json",
                "state",
                "--jq",
                ".state",
            ],
            retries=_GH_RETRIES,
        ).stdout.strip()
    if state.upper() != "CLOSED":
        raise WorkspaceError(f"Issue #{issue_number} is not closed after terminal outcome")


def edit_pr_body(pr_number: int, body: str) -> None:
    """Update a PR's body text."""
    _run(
        ["gh", "pr", "edit", str(pr_number), "--body", body],
        retries=_GH_RETRIES,
    )


def close_pr(pr_number: int) -> None:
    """Close a GitHub PR."""
    _run(["gh", "pr", "close", str(pr_number)], retries=_GH_RETRIES)


def close_pr_if_open(pr_number: int) -> None:
    """Idempotently close a PR and verify that it is no longer open."""
    state = _run(
        ["gh", "pr", "view", str(pr_number), *_gh_repo_flag(), "--json", "state", "--jq", ".state"],
        retries=_GH_RETRIES,
    ).stdout.strip()
    if state.upper() == "OPEN":
        close_pr(pr_number)
        state = _run(
            [
                "gh",
                "pr",
                "view",
                str(pr_number),
                *_gh_repo_flag(),
                "--json",
                "state",
                "--jq",
                ".state",
            ],
            retries=_GH_RETRIES,
        ).stdout.strip()
    if state.upper() not in {"CLOSED", "MERGED"}:
        raise WorkspaceError(f"PR #{pr_number} is not closed after cleanup (state={state!r})")


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
