"""Git and GitHub CLI subprocess wrappers."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Literal

from src.workspace.errors import GitCommandError, GitHubApiError, WorkspaceError
from src.workspace.safe_cleanup import safe_rmtree_child

_GIT_RETRIES = 2
_GH_RETRIES = 2
_RETRY_DELAY = 2.0

_DEFAULT_REPO = "colophon-group/jobseek"
_OID_RE = re.compile(r"^[0-9a-f]{40}$")


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
    safe_rmtree_child(_MANAGED_REPO.parent, _MANAGED_REPO.name, missing_ok=True)


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
    """Reject the retired merge-main reconciliation path.

    Company PR branches may contain reviewed or human-repaired commits.  A
    resolver must rebuild from an exact ``origin/main`` worktree and replay
    only changes covered by its own lease; it must never merge main into an
    existing company branch.
    """
    raise WorkspaceError(
        f"Refusing to merge main into reviewed company branch {branch!r}; "
        "rebuild from exact origin/main and replay only explicitly owned changes"
    )


def managed_repo() -> Path:
    """Return the managed clone directory."""
    return _MANAGED_REPO


def worktrees_dir() -> Path:
    """Return the worktrees directory."""
    return _WORKTREES_DIR


def create_worktree(
    branch: str,
    path: Path,
    start_point: str = "origin/main",
) -> dict[str, str | int]:
    """Create a fresh authenticated managed worktree, failing closed on debris.

    Lifecycle cleanup owns deletion.  Bootstrap must never delete an existing
    pathname/ref merely because it has the desired deterministic name.
    """
    root = _absolute_lexical(worktrees_dir())
    target = _absolute_lexical(path)
    if target.parent != root or target.name in {"", ".", ".."}:
        raise WorkspaceError(f"Worktree {path} is not an exact child of {root}")
    root.mkdir(parents=True, exist_ok=True)
    try:
        mode = root.lstat().st_mode
    except OSError as exc:
        raise WorkspaceError(f"Managed worktree root could not be inspected: {root}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise WorkspaceError(f"Managed worktree root is unsafe: {root}")

    start = _run(
        ["git", "rev-parse", "--verify", f"{start_point}^{{commit}}"],
        cwd=_MANAGED_REPO,
    ).stdout.strip()
    if not _OID_RE.fullmatch(start):
        raise WorkspaceError(f"Start point {start_point!r} did not resolve to an exact commit")

    # Repeat every ownership probe immediately before Git mutates anything.
    if _managed_worktree_identity(target, branch) is not None:
        raise WorkspaceError(f"Managed worktree already exists: {target}")
    if local_branch_oid_strict(branch) is not None:
        raise WorkspaceError(f"Local branch already exists without workspace ownership: {branch}")
    if os.path.lexists(target):
        raise WorkspaceError(f"Worktree path already exists without workspace ownership: {target}")

    _run(
        ["git", "worktree", "add", str(target), "-b", branch, start],
        cwd=_MANAGED_REPO,
    )
    identity = _managed_worktree_identity(target, branch)
    if identity is None or identity["head"] != start:
        raise WorkspaceError("Fresh worktree did not authenticate at the requested commit")
    if local_branch_oid_strict(branch) != start:
        raise WorkspaceError("Fresh worktree branch ref contradicts its registered commit")
    return identity


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


def _absolute_lexical(path: Path) -> Path:
    """Return an absolute path without resolving symlinks."""
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def _registered_worktrees_strict() -> dict[Path, dict[str, str | bool | None]]:
    """Return exact managed-repository worktree registrations without resolving paths."""
    result = _run(["git", "worktree", "list", "--porcelain"], cwd=_MANAGED_REPO)
    registrations: dict[Path, dict[str, str | bool | None]] = {}
    current: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current = _absolute_lexical(Path(line.removeprefix("worktree ")))
            if current in registrations:
                raise WorkspaceError(f"Duplicate git worktree registration for {current}")
            registrations[current] = {"head": None, "branch": None, "locked": False}
        elif current is not None and line.startswith("HEAD "):
            registrations[current]["head"] = line.removeprefix("HEAD ")
        elif current is not None and line.startswith("branch "):
            registrations[current]["branch"] = line.removeprefix("branch ")
        elif current is not None and line.startswith("locked"):
            registrations[current]["locked"] = True
    return registrations


def authenticate_managed_worktree(
    path: Path,
    branch: str,
    expected_head: str,
    *,
    expected_dev: int | None = None,
    expected_ino: int | None = None,
) -> bool:
    """Authenticate an exact direct-child worktree using lstat and git registration data.

    Returns ``False`` only when both the path and its registration are absent.
    Symlinks, unexpected paths, branches, commits, or stale registrations fail closed.
    """
    if not _OID_RE.fullmatch(expected_head):
        raise WorkspaceError("Invalid expected worktree commit OID")
    identity = _managed_worktree_identity(path, branch)
    if identity is None:
        return False
    if identity["head"] != expected_head:
        raise WorkspaceError(f"Worktree {path} is registered at an unexpected commit")
    if expected_dev is not None and identity["dev"] != expected_dev:
        raise WorkspaceError(f"Worktree {path} is a replacement filesystem entry")
    if expected_ino is not None and identity["ino"] != expected_ino:
        raise WorkspaceError(f"Worktree {path} is a replacement filesystem entry")
    return True


def _managed_worktree_identity(path: Path, branch: str) -> dict[str, str | int] | None:
    """Inspect one direct child through descriptor-anchored, no-follow opens."""
    from src.workspace.safe_cleanup import (
        directory_open_flags,
        open_absolute_directory_no_follow,
    )

    root = _absolute_lexical(worktrees_dir())
    target = _absolute_lexical(path)
    if target.parent != root or target.name in {"", ".", ".."}:
        raise WorkspaceError(f"Worktree {path} is not an exact child of {root}")
    try:
        root_fd = open_absolute_directory_no_follow(root)
    except RuntimeError as exc:
        raise WorkspaceError(f"Managed worktree root is unsafe: {root}") from exc
    child_fd: int | None = None
    try:
        registrations = _registered_worktrees_strict()
        registration = registrations.get(target)
        try:
            expected = os.stat(target.name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            if registration is not None:
                raise WorkspaceError(
                    f"Worktree {target} is missing but remains registered"
                ) from None
            return None
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
            raise WorkspaceError(f"Worktree target is not a real directory: {target}")
        try:
            child_fd = os.open(target.name, directory_open_flags(), dir_fd=root_fd)
        except OSError as exc:
            raise WorkspaceError(f"Worktree target could not be opened safely: {target}") from exc
        opened = os.fstat(child_fd)
        current = os.stat(target.name, dir_fd=root_fd, follow_symlinks=False)
        expected_identity = (expected.st_dev, expected.st_ino)
        if (opened.st_dev, opened.st_ino) != expected_identity or (
            current.st_dev,
            current.st_ino,
        ) != expected_identity:
            raise WorkspaceError(f"Worktree {target} changed during authentication")
        if registration is None:
            raise WorkspaceError(f"Worktree {target} is not registered in the managed repository")
        if registration.get("locked"):
            raise WorkspaceError(f"Worktree {target} is locked")
        if registration.get("branch") != f"refs/heads/{branch}":
            raise WorkspaceError(f"Worktree {target} is registered to an unexpected branch")
        head = registration.get("head")
        if not isinstance(head, str) or not _OID_RE.fullmatch(head):
            raise WorkspaceError(f"Worktree {target} has no valid registered commit")
        return {"head": head, "dev": int(opened.st_dev), "ino": int(opened.st_ino)}
    finally:
        if child_fd is not None:
            os.close(child_fd)
        os.close(root_fd)


def managed_worktree_head_strict(path: Path, branch: str) -> str | None:
    """Return the authenticated registered head for one exact managed worktree."""
    identity = _managed_worktree_identity(path, branch)
    return str(identity["head"]) if identity is not None else None


def managed_worktree_identity_strict(path: Path, branch: str) -> dict[str, str | int] | None:
    """Return exact commit and filesystem identity for one managed worktree."""
    return _managed_worktree_identity(path, branch)


def terminal_worktree_quarantine_path(path: Path, branch: str, expected_head: str) -> Path:
    """Return the deterministic quarantine path for one journal-owned worktree."""
    if not _OID_RE.fullmatch(expected_head):
        raise WorkspaceError("Invalid expected worktree commit OID")
    root = _absolute_lexical(worktrees_dir())
    target = _absolute_lexical(path)
    if target.parent != root:
        raise WorkspaceError(f"Worktree {target} is outside the managed root")
    quarantine_name = (
        ".jobseek-terminal-"
        + hashlib.sha256(f"{target.name}\0{branch}\0{expected_head}".encode()).hexdigest()[:32]
    )
    return root / quarantine_name


def authenticate_terminal_worktree_removal_state(
    path: Path,
    branch: str,
    expected_head: str,
    *,
    expected_dev: int,
    expected_ino: int,
) -> Literal["canonical", "quarantine", "stale-registration", "absent"]:
    """Read-only authenticate every legal state of the terminal remover."""
    from src.workspace.safe_cleanup import (
        directory_open_flags,
        open_absolute_directory_no_follow,
    )

    root = _absolute_lexical(worktrees_dir())
    target = _absolute_lexical(path)
    quarantine = terminal_worktree_quarantine_path(target, branch, expected_head)
    try:
        root_fd = open_absolute_directory_no_follow(root)
    except RuntimeError as exc:
        raise WorkspaceError(f"Managed worktree root is unsafe: {root}") from exc
    try:
        registrations = _registered_worktrees_strict()
        original_registration = registrations.get(target)
        quarantine_registration = registrations.get(quarantine)
        expected_branch = f"refs/heads/{branch}"
        foreign_branch_owners = [
            registered_path
            for registered_path, registration in registrations.items()
            if registered_path not in {target, quarantine}
            and registration.get("branch") == expected_branch
        ]
        if foreign_branch_owners:
            raise WorkspaceError("Terminal worktree branch is registered at an unrelated path")
        registration_entries = [
            (name, registration)
            for name, registration in (
                (target, original_registration),
                (quarantine, quarantine_registration),
            )
            if registration is not None
        ]
        matching_registrations = [
            name
            for name, registration in registration_entries
            if _registration_matches(registration, branch=branch, head=expected_head)
        ]
        if len(registration_entries) != len(matching_registrations):
            raise WorkspaceError("Terminal worktree registration contradicts its journal")
        if len(matching_registrations) > 1:
            raise WorkspaceError("Multiple terminal worktree registrations match the journal")

        def entry(name: str) -> os.stat_result | None:
            try:
                return os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None

        original_stat = entry(target.name)
        quarantine_stat = entry(quarantine.name)
        if original_stat is not None and quarantine_stat is not None:
            raise WorkspaceError("Original and quarantined worktree paths both exist")

        def authenticate_entry(name: str, item: os.stat_result) -> None:
            if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
                raise WorkspaceError("Terminal worktree path is not a real directory")
            child_fd = os.open(name, directory_open_flags(), dir_fd=root_fd)
            try:
                opened = os.fstat(child_fd)
                current = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                exact = (expected_dev, expected_ino)
                if (opened.st_dev, opened.st_ino) != exact or (
                    current.st_dev,
                    current.st_ino,
                ) != exact:
                    raise WorkspaceError("Terminal worktree is a replacement filesystem entry")
            finally:
                os.close(child_fd)

        if original_stat is not None:
            authenticate_entry(target.name, original_stat)
            if matching_registrations != [target]:
                raise WorkspaceError("Live terminal worktree registration is not exact")
            state: Literal["canonical", "quarantine", "stale-registration", "absent"] = "canonical"
        elif quarantine_stat is not None:
            authenticate_entry(quarantine.name, quarantine_stat)
            if len(matching_registrations) != 1:
                raise WorkspaceError("Quarantined terminal worktree registration is not exact")
            state = "quarantine"
        elif matching_registrations:
            state = "stale-registration"
        else:
            state = "absent"
    finally:
        os.close(root_fd)

    admin_identity = _worktree_admin_identity_strict(
        target,
        quarantine,
        branch=branch,
    )
    if state == "absent":
        if admin_identity is not None:
            raise WorkspaceError("Unregistered terminal worktree admin state survived")
    elif admin_identity is None:
        raise WorkspaceError("Terminal worktree registration has no exact admin identity")
    return state


def remove_authenticated_worktree(
    path: Path,
    branch: str,
    expected_head: str,
    *,
    expected_dev: int | None = None,
    expected_ino: int | None = None,
    absent_is_success: bool = False,
) -> None:
    """Atomically quarantine then descriptor-delete one authenticated worktree.

    The original pathname is never recursively removed. It is first renamed
    inside its no-follow-opened parent; descriptor-anchored cleanup then claims
    that exact quarantined inode, so a replacement at either public pathname is
    preserved and rejected rather than followed.
    """
    from src.workspace.safe_cleanup import (
        directory_open_flags,
        open_absolute_directory_no_follow,
        safe_rmtree_child,
        validate_child_name,
    )

    root = _absolute_lexical(worktrees_dir())
    target = _absolute_lexical(path)
    quarantine = terminal_worktree_quarantine_path(target, branch, expected_head)
    quarantine_name = quarantine.name
    validate_child_name(quarantine_name)

    try:
        root_fd = open_absolute_directory_no_follow(root)
    except RuntimeError as exc:
        raise WorkspaceError(f"Managed worktree root is unsafe: {root}") from exc
    try:
        registrations = _registered_worktrees_strict()
        original_registration = registrations.get(target)
        quarantine_registration = registrations.get(quarantine)

        def entry(name: str) -> os.stat_result | None:
            try:
                return os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None

        original_stat = entry(target.name)
        quarantine_stat = entry(quarantine_name)
        if original_stat is not None and quarantine_stat is not None:
            raise WorkspaceError("Original and quarantined worktree paths both exist")

        if original_stat is not None:
            if not stat.S_ISDIR(original_stat.st_mode) or stat.S_ISLNK(original_stat.st_mode):
                raise WorkspaceError("Original worktree path is not a real directory")
            child_fd = os.open(target.name, directory_open_flags(), dir_fd=root_fd)
            try:
                opened = os.fstat(child_fd)
                if (opened.st_dev, opened.st_ino) != (
                    original_stat.st_dev,
                    original_stat.st_ino,
                ):
                    raise WorkspaceError("Original worktree changed while opening")
                if expected_dev is not None and opened.st_dev != expected_dev:
                    raise WorkspaceError("Original worktree device contradicts journal")
                if expected_ino is not None and opened.st_ino != expected_ino:
                    raise WorkspaceError("Original worktree inode contradicts journal")
                if not _registration_matches(
                    original_registration, branch=branch, head=expected_head
                ):
                    raise WorkspaceError("Original worktree registration contradicts journal")
                os.rename(
                    target.name,
                    quarantine_name,
                    src_dir_fd=root_fd,
                    dst_dir_fd=root_fd,
                )
                claimed = os.stat(quarantine_name, dir_fd=root_fd, follow_symlinks=False)
                if (claimed.st_dev, claimed.st_ino) != (opened.st_dev, opened.st_ino):
                    raise WorkspaceError("Worktree changed during atomic quarantine")
            finally:
                os.close(child_fd)
            quarantine_stat = entry(quarantine_name)
        elif quarantine_stat is not None:
            if not stat.S_ISDIR(quarantine_stat.st_mode) or stat.S_ISLNK(quarantine_stat.st_mode):
                raise WorkspaceError("Quarantined worktree is not a real directory")
            if expected_dev is not None and quarantine_stat.st_dev != expected_dev:
                raise WorkspaceError("Quarantined worktree device contradicts journal")
            if expected_ino is not None and quarantine_stat.st_ino != expected_ino:
                raise WorkspaceError("Quarantined worktree inode contradicts journal")
            if not (
                _registration_matches(original_registration, branch=branch, head=expected_head)
                or _registration_matches(quarantine_registration, branch=branch, head=expected_head)
            ):
                raise WorkspaceError("Quarantined worktree registration contradicts journal")
        else:
            matching_registration = _registration_matches(
                original_registration, branch=branch, head=expected_head
            ) or _registration_matches(quarantine_registration, branch=branch, head=expected_head)
            if matching_registration:
                safe_rmtree_child(
                    root,
                    quarantine_name,
                    missing_ok=True,
                    expected_dev=expected_dev,
                    expected_ino=expected_ino,
                )
                _remove_worktree_admin_strict(
                    target,
                    quarantine,
                    branch=branch,
                    missing_ok=False,
                )
                registrations = _registered_worktrees_strict()
                if target in registrations or quarantine in registrations:
                    raise WorkspaceError("Stale worktree registration survived pruning")
            elif not absent_is_success:
                raise WorkspaceError("Worktree disappeared before removal was attempted")
            return
    finally:
        os.close(root_fd)

    safe_rmtree_child(
        root,
        quarantine_name,
        missing_ok=True,
        expected_dev=expected_dev,
        expected_ino=expected_ino,
    )
    _remove_worktree_admin_strict(
        target,
        quarantine,
        branch=branch,
        missing_ok=False,
    )
    registrations = _registered_worktrees_strict()
    if target in registrations or quarantine in registrations:
        raise WorkspaceError("Worktree registration survived authenticated cleanup")
    try:
        target_mode = os.lstat(target).st_mode
    except FileNotFoundError:
        target_mode = None
    if target_mode is not None:
        raise WorkspaceError("A replacement appeared at the original worktree path; preserved it")
    if os.path.lexists(quarantine):
        raise WorkspaceError("Quarantined worktree path survived cleanup")


def _registration_matches(
    registration: dict[str, str | bool | None] | None,
    *,
    branch: str,
    head: str,
) -> bool:
    return bool(
        registration
        and registration.get("locked") is False
        and registration.get("branch") == f"refs/heads/{branch}"
        and registration.get("head") == head
    )


def _read_admin_file_at(parent_fd: int, name: str) -> str:
    try:
        expected = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
            raise WorkspaceError(f"Git worktree admin file {name!r} is unsafe")
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd)
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
                raise WorkspaceError(f"Git worktree admin file {name!r} changed while opening")
            data = os.read(fd, 16384)
            if os.read(fd, 1):
                raise WorkspaceError(f"Git worktree admin file {name!r} is unexpectedly large")
        finally:
            os.close(fd)
    except OSError as exc:
        raise WorkspaceError(f"Could not safely read Git worktree admin file {name!r}") from exc
    try:
        return data.decode().strip()
    except UnicodeDecodeError as exc:
        raise WorkspaceError(f"Git worktree admin file {name!r} is not UTF-8") from exc


def _worktree_admin_identity_strict(
    target: Path,
    quarantine: Path,
    *,
    branch: str,
) -> tuple[Path, str, int, int] | None:
    """Locate the unique exact Git admin directory for one managed worktree."""
    from src.workspace.safe_cleanup import (
        directory_open_flags,
        open_absolute_directory_no_follow,
        validate_child_name,
    )

    raw_common = _run(
        ["git", "rev-parse", "--git-common-dir"],
        cwd=_MANAGED_REPO,
    ).stdout.strip()
    if not raw_common:
        raise WorkspaceError("Managed repository returned no Git common directory")
    common = Path(raw_common)
    if not common.is_absolute():
        common = _MANAGED_REPO / common
    admin_root = _absolute_lexical(common) / "worktrees"
    try:
        root_fd = open_absolute_directory_no_follow(admin_root)
    except RuntimeError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return None
        raise WorkspaceError(f"Git worktree admin root is unsafe: {admin_root}") from exc
    candidates = {_absolute_lexical(target), _absolute_lexical(quarantine)}
    matches: list[tuple[Path, str, int, int]] = []
    try:
        for name in sorted(os.listdir(root_fd)):
            try:
                validate_child_name(name)
                child_fd = os.open(name, directory_open_flags(), dir_fd=root_fd)
            except (OSError, RuntimeError):
                continue
            try:
                opened = os.fstat(child_fd)
                gitdir = _read_admin_file_at(child_fd, "gitdir")
                head = _read_admin_file_at(child_fd, "HEAD")
            finally:
                os.close(child_fd)
            gitdir_path = Path(gitdir)
            if not gitdir_path.is_absolute():
                raise WorkspaceError("Git worktree admin gitdir path is not absolute")
            if _absolute_lexical(gitdir_path).name != ".git":
                raise WorkspaceError("Git worktree admin gitdir path is malformed")
            if _absolute_lexical(gitdir_path).parent not in candidates:
                continue
            if head != f"ref: refs/heads/{branch}":
                raise WorkspaceError("Git worktree admin HEAD contradicts the journal branch")
            matches.append((admin_root, name, int(opened.st_dev), int(opened.st_ino)))
    finally:
        os.close(root_fd)
    if len(matches) > 1:
        raise WorkspaceError("Multiple Git admin directories match the managed worktree")
    return matches[0] if matches else None


def _remove_worktree_admin_strict(
    target: Path,
    quarantine: Path,
    *,
    branch: str,
    missing_ok: bool,
) -> None:
    from src.workspace.safe_cleanup import safe_rmtree_child

    identity = _worktree_admin_identity_strict(
        target,
        quarantine,
        branch=branch,
    )
    if identity is None:
        if missing_ok:
            return
        raise WorkspaceError("Managed worktree registration has no exact Git admin directory")
    root, name, dev, ino = identity
    try:
        safe_rmtree_child(
            root,
            name,
            expected_dev=dev,
            expected_ino=ino,
        )
    except RuntimeError as exc:
        raise WorkspaceError("Could not remove exact Git worktree admin directory") from exc


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
    When *check* is True, ``CalledProcessError`` is translated. A missing
    executable is translated regardless of *check*. Both become
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
        except FileNotFoundError as exc:
            is_gh = args[0] == "gh"
            err_cls = GitHubApiError if is_gh else GitCommandError
            raise err_cls(
                cmd=args,
                returncode=127,
                stderr=f"executable not found: {args[0]}",
            ) from exc

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


def changed_paths_strict() -> set[str]:
    """Return every staged, unstaged, and untracked path in the current checkout."""
    result = _run(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"])
    fields = result.stdout.split("\0")
    paths: set[str] = set()
    index = 0
    while index < len(fields):
        field = fields[index]
        index += 1
        if not field:
            continue
        if len(field) < 4 or field[2] != " ":
            raise WorkspaceError("Could not parse changed-path status")
        status_code = field[:2]
        paths.add(field[3:])
        if "R" in status_code or "C" in status_code:
            if index >= len(fields) or not fields[index]:
                raise WorkspaceError("Could not parse renamed changed path")
            paths.add(fields[index])
            index += 1
    return paths


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


def current_head_oid_strict(*, cwd: Path | None = None) -> str:
    """Return the exact local HEAD commit OID."""
    oid = _run(
        ["git", "--no-replace-objects", "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=cwd,
    ).stdout.strip()
    if not _OID_RE.fullmatch(oid):
        raise WorkspaceError("Local HEAD lookup returned an invalid commit OID")
    return oid


def verify_single_commit_strict(
    oid: str,
    *,
    parent_oid: str,
    allowed_prefix: str,
    message: str,
) -> None:
    """Authenticate one exact locally-created commit before publication."""
    if not _OID_RE.fullmatch(oid) or not _OID_RE.fullmatch(parent_oid):
        raise WorkspaceError("Invalid commit OID in ready journal")
    actual_parent = _run(
        ["git", "--no-replace-objects", "rev-parse", "--verify", f"{oid}^{{commit}}^"],
    ).stdout.strip()
    if actual_parent != parent_oid:
        raise WorkspaceError("KB publication commit has an unexpected parent")
    actual_message = _run(
        ["git", "--no-replace-objects", "show", "-s", "--format=%B", oid],
    ).stdout.rstrip("\n")
    if actual_message != message:
        raise WorkspaceError("KB publication commit has an unexpected message")
    changed = {
        path
        for path in _run(
            [
                "git",
                "--no-replace-objects",
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                oid,
            ]
        ).stdout.splitlines()
        if path
    }
    if not changed or any(
        path != allowed_prefix.rstrip("/") and not path.startswith(allowed_prefix)
        for path in changed
    ):
        raise WorkspaceError("KB publication commit changed an unauthorized path")


def push_branch_at_expected_oid(
    name: str,
    local_oid: str,
    expected_remote_oid: str | None,
) -> None:
    """Publish exactly *local_oid* while leasing the prior remote value."""
    if not _OID_RE.fullmatch(local_oid):
        raise WorkspaceError(f"Invalid local head OID for branch {name!r}")
    if expected_remote_oid is not None and not _OID_RE.fullmatch(expected_remote_oid):
        raise WorkspaceError(f"Invalid expected remote OID for branch {name!r}")
    current = remote_branch_oid_strict(name)
    if current != expected_remote_oid:
        raise WorkspaceError(
            f"Remote branch {name!r} changed from {expected_remote_oid!r} to {current!r}; "
            "refusing publication"
        )
    lease = f"--force-with-lease=refs/heads/{name}:{expected_remote_oid or ''}"
    _run(
        [
            "git",
            "push",
            "-u",
            lease,
            "origin",
            f"{local_oid}:refs/heads/{name}",
        ],
        retries=_GIT_RETRIES,
    )
    published = remote_branch_oid_strict(name)
    if published != local_oid:
        raise WorkspaceError(
            f"Remote branch {name!r} is {published!r} after push, expected {local_oid}"
        )


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


def remote_branch_oid_strict(name: str) -> str | None:
    """Return the exact remote branch OID, failing on malformed output."""
    result = _run(
        ["git", "ls-remote", "--heads", "origin", f"refs/heads/{name}"],
        cwd=_MANAGED_REPO,
        retries=_GIT_RETRIES,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) != 1:
        raise WorkspaceError(f"Remote branch lookup for {name!r} was ambiguous")
    fields = lines[0].split()
    expected_ref = f"refs/heads/{name}"
    if len(fields) != 2 or fields[1] != expected_ref or not _OID_RE.fullmatch(fields[0]):
        raise WorkspaceError(f"Remote branch lookup for {name!r} returned malformed data")
    return fields[0]


def delete_remote_branch_at_expected_oid(
    name: str,
    expected_oid: str,
    *,
    absent_is_success: bool = False,
) -> None:
    """Delete one remote branch with an exact lease and retry reconciliation."""
    if not _OID_RE.fullmatch(expected_oid):
        raise WorkspaceError(f"Invalid recorded head OID for branch {name!r}")
    current_oid = remote_branch_oid_strict(name)
    if current_oid is None:
        if absent_is_success:
            return
        raise WorkspaceError(f"Remote branch {name!r} disappeared; refusing destructive cleanup")
    if current_oid != expected_oid:
        raise WorkspaceError(
            f"Remote branch {name!r} changed from {expected_oid} to {current_oid}; "
            "refusing destructive cleanup"
        )

    _run(
        [
            "git",
            "push",
            f"--force-with-lease=refs/heads/{name}:{expected_oid}",
            "origin",
            f":refs/heads/{name}",
        ],
        cwd=_MANAGED_REPO,
        retries=_GIT_RETRIES,
    )
    if remote_branch_oid_strict(name) is not None:
        raise WorkspaceError(f"Remote branch still exists after deletion: {name}")


def delete_branch_at_expected_oid(name: str, expected_oid: str) -> None:
    """Delete one local/remote branch only while the remote is unchanged."""
    delete_remote_branch_at_expected_oid(name, expected_oid)
    delete_local_branch_strict(name)


def delete_local_branch_strict(name: str) -> None:
    """Delete only a local branch, without ever mutating a remote ref."""
    _run(["git", "branch", "-D", name], cwd=_MANAGED_REPO, check=False)
    local = _run(["git", "branch", "--list", name], cwd=_MANAGED_REPO)
    if local.stdout.strip():
        raise WorkspaceError(f"Local branch still exists after deletion: {name}")


def local_branch_oid_strict(name: str) -> str | None:
    """Return the exact local branch OID from the managed clone."""
    result = _run(
        ["git", "show-ref", "--verify", "--hash", f"refs/heads/{name}"],
        cwd=_MANAGED_REPO,
        check=False,
    )
    # Git versions differ here: an absent exact ref may return either 1 or
    # 128 (with "not a valid ref"). Treat only those known absence shapes as
    # missing, while continuing to fail closed for every other lookup error.
    if result.returncode in {1, 128} and not result.stdout.strip() and (
        result.returncode == 1 or "not a valid ref" in result.stderr
    ):
        return None
    if result.returncode != 0:
        raise WorkspaceError(f"Could not inspect local branch {name!r}")
    oid = result.stdout.strip()
    if not _OID_RE.fullmatch(oid):
        raise WorkspaceError(f"Local branch {name!r} returned an invalid OID")
    return oid


def delete_local_branch_at_expected_oid(
    name: str,
    expected_oid: str,
    *,
    absent_is_success: bool = False,
) -> None:
    """Atomically delete a local ref only if it still names the captured commit."""
    if not _OID_RE.fullmatch(expected_oid):
        raise WorkspaceError(f"Invalid expected local OID for branch {name!r}")
    current = local_branch_oid_strict(name)
    if current is None:
        if absent_is_success:
            return
        raise WorkspaceError(f"Local branch {name!r} disappeared before deletion was attempted")
    if current != expected_oid:
        raise WorkspaceError(
            f"Local branch {name!r} changed from {expected_oid} to {current}; refusing deletion"
        )
    _run(
        ["git", "update-ref", "-d", f"refs/heads/{name}", expected_oid],
        cwd=_MANAGED_REPO,
    )
    if local_branch_oid_strict(name) is not None:
        raise WorkspaceError(f"Local branch still exists after exact deletion: {name}")


# ── GitHub CLI operations ───────────────────────────────────────────────


def check_gh_auth() -> bool:
    """Check if GitHub CLI is authenticated. Returns True if OK."""
    result = _run(["gh", "auth", "status"], check=False)
    return result.returncode == 0


def check_existing_prs_strict(issue_number: int) -> list[dict]:
    """Check for open PRs that close a given issue.

    Returns enough metadata to distinguish a submitted company PR from an
    unrelated/manual PR. Drafts are submitted/external across resolver runs.
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


def get_authenticated_login_strict() -> str:
    """Return the login owning the current GitHub credentials."""
    args = ["gh", "api", "user", "--jq", ".login"]
    login = _run(args, retries=_GH_RETRIES).stdout.strip()
    if not login:
        raise GitHubApiError(args, 1, "Authenticated GitHub login was empty")
    return login


_PR_PROVENANCE_FIELDS = (
    "number,state,isDraft,headRefName,headRefOid,headRepository,"
    "headRepositoryOwner,baseRefName,author,closingIssuesReferences,"
    "isCrossRepository,url,reviewDecision,reviews,comments,labels"
)

_REVIEW_OR_HOLD_RE = re.compile(
    r"\b(?:approved|request(?:ed)?\s+changes?|do\s+not\s+merge|must\s+not\s+merge|"
    r"merge\s+hold|capacity\s+gate|keep(?:ing)?\s+(?:this\s+)?pr\s+draft|"
    r"remain(?:s|ing)?\s+draft)\b",
    re.IGNORECASE,
)
_HOLD_LABEL_RE = re.compile(r"(?:do[- ]?not[- ]?merge|blocked|merge[- ]?hold|hold)", re.I)


def get_pr_details_strict(pr_number: int) -> dict:
    """Fetch security-sensitive PR metadata from the configured base repo."""
    import json

    args = [
        "gh",
        "pr",
        "view",
        str(pr_number),
        "--repo",
        _resolve_repo(),
        "--json",
        _PR_PROVENANCE_FIELDS,
    ]
    result = _run(args, retries=_GH_RETRIES)
    try:
        details = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GitHubApiError(args, 1, "Could not parse PR provenance response") from exc
    if not isinstance(details, dict):
        raise GitHubApiError(args, 1, "Unexpected PR provenance response")
    return details


def _head_repo(details: dict) -> str:
    owner = details.get("headRepositoryOwner")
    repository = details.get("headRepository")
    owner_login = owner.get("login") if isinstance(owner, dict) else None
    repo_name = repository.get("name") if isinstance(repository, dict) else None
    if not isinstance(owner_login, str) or not isinstance(repo_name, str):
        return ""
    return f"{owner_login}/{repo_name}".lower()


def _closing_issue_keys(details: dict) -> set[tuple[str, int]]:
    keys: set[tuple[str, int]] = set()
    references = details.get("closingIssuesReferences")
    if not isinstance(references, list):
        return keys
    for reference in references:
        if not isinstance(reference, dict) or not isinstance(reference.get("number"), int):
            continue
        repository = reference.get("repository")
        if not isinstance(repository, dict):
            continue
        owner = repository.get("owner")
        owner_login = owner.get("login") if isinstance(owner, dict) else None
        repo_name = repository.get("name")
        if isinstance(owner_login, str) and isinstance(repo_name, str):
            keys.add((f"{owner_login}/{repo_name}".lower(), reference["number"]))
    return keys


def _review_and_hold_evidence(details: dict) -> tuple[list[dict[str, str]], list[str]]:
    """Return stable safety evidence that invalidates a resolver head lease."""
    evidence: list[dict[str, str]] = []
    decision = details.get("reviewDecision")
    if isinstance(decision, str) and decision:
        evidence.append({"kind": "decision", "value": decision})

    reviews = details.get("reviews")
    if isinstance(reviews, list):
        for review in reviews:
            if not isinstance(review, dict):
                continue
            state = review.get("state")
            if state not in {"APPROVED", "CHANGES_REQUESTED"}:
                continue
            author = review.get("author")
            author_login = author.get("login") if isinstance(author, dict) else ""
            commit = review.get("commit")
            commit_oid = commit.get("oid") if isinstance(commit, dict) else ""
            submitted_at = review.get("submittedAt")
            evidence.append(
                {
                    "kind": "review",
                    "state": str(state),
                    "author": author_login if isinstance(author_login, str) else "",
                    "commit_oid": commit_oid if isinstance(commit_oid, str) else "",
                    "submitted_at": submitted_at if isinstance(submitted_at, str) else "",
                }
            )

    comments = details.get("comments")
    if isinstance(comments, list):
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            body = comment.get("body")
            if not isinstance(body, str) or not _REVIEW_OR_HOLD_RE.search(body):
                continue
            author = comment.get("author")
            author_login = author.get("login") if isinstance(author, dict) else ""
            created_at = comment.get("createdAt")
            evidence.append(
                {
                    "kind": "comment",
                    "author": author_login if isinstance(author_login, str) else "",
                    "created_at": created_at if isinstance(created_at, str) else "",
                    "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                }
            )

    holds: list[str] = []
    labels = details.get("labels")
    if isinstance(labels, list):
        for label in labels:
            name = label.get("name") if isinstance(label, dict) else None
            if isinstance(name, str) and _HOLD_LABEL_RE.search(name):
                holds.append(name)
    return evidence, sorted(set(holds))


def validate_pr_attachment(
    details: dict,
    *,
    pr_number: int,
    branch: str,
    base_ref: str,
    issue: int | None,
    slug: str,
    authorized_actor: str | None,
) -> None:
    """Validate that a PR is the exact resolver object we intend to mutate."""
    from src.shared.constants import SLUG_RE

    expected_repo = _resolve_repo().lower()
    if not SLUG_RE.fullmatch(slug):
        raise WorkspaceError(f"Invalid company slug in PR provenance: {slug!r}")
    if details.get("number") != pr_number:
        raise WorkspaceError(f"PR lookup did not return exact PR #{pr_number}")
    if details.get("state") != "OPEN" or details.get("isDraft") is not True:
        raise WorkspaceError(f"PR #{pr_number} is not an open draft")
    if details.get("headRefName") != branch:
        raise WorkspaceError(
            f"PR #{pr_number} uses branch {details.get('headRefName')!r}, expected {branch!r}"
        )
    head_oid = details.get("headRefOid")
    if not isinstance(head_oid, str) or not _OID_RE.fullmatch(head_oid):
        raise WorkspaceError(f"PR #{pr_number} has no valid exact head OID")
    if details.get("isCrossRepository") is not False or _head_repo(details) != expected_repo:
        raise WorkspaceError(f"PR #{pr_number} is not owned by {expected_repo}")
    if details.get("baseRefName") != base_ref:
        raise WorkspaceError(
            f"PR #{pr_number} targets {details.get('baseRefName')!r}, expected {base_ref!r}"
        )
    author = details.get("author")
    author_login = author.get("login") if isinstance(author, dict) else None
    if authorized_actor is not None and author_login != authorized_actor:
        raise WorkspaceError(
            f"PR #{pr_number} author {author_login!r} is not the authenticated resolver actor"
        )
    if issue is not None:
        expected_issue = {(expected_repo, issue)}
        if _closing_issue_keys(details) != expected_issue:
            raise WorkspaceError(
                f"PR #{pr_number} is not structured as the sole resolver for issue #{issue}"
            )
    if branch not in {f"add-company/{slug}", f"fix-crawler/{slug}"}:
        raise WorkspaceError(f"PR #{pr_number} branch is not bound to company slug {slug!r}")

    review_evidence, hold_labels = _review_and_hold_evidence(details)
    if review_evidence or hold_labels:
        raise WorkspaceError(
            f"PR #{pr_number} has review or merge-hold evidence; refusing resolver attachment"
        )


def pr_provenance(details: dict, *, issue: int | None, slug: str) -> dict:
    """Return the immutable PR fields recorded in workspace state."""
    author = details.get("author")
    review_evidence, hold_labels = _review_and_hold_evidence(details)
    return {
        "number": details.get("number"),
        "head_ref_name": details.get("headRefName"),
        "head_ref_oid": details.get("headRefOid"),
        "head_repository": _head_repo(details),
        "base_repository": _resolve_repo().lower(),
        "base_ref_name": details.get("baseRefName"),
        "author_login": author.get("login") if isinstance(author, dict) else None,
        "is_draft": details.get("isDraft"),
        "closing_issues": [
            {"repository": repository, "number": number}
            for repository, number in sorted(_closing_issue_keys(details))
        ],
        "review_evidence": review_evidence,
        "hold_labels": hold_labels,
        "issue": issue,
        "slug": slug,
    }


def verify_recorded_pr(
    provenance: dict,
    *,
    pr_number: int,
    branch: str,
    issue: int | None,
    slug: str,
    allow_closed: bool = False,
) -> dict:
    """Fail closed unless GitHub still exposes the exact recorded PR/ref."""
    required = {
        "number",
        "head_ref_name",
        "head_ref_oid",
        "head_repository",
        "base_repository",
        "base_ref_name",
        "author_login",
        "is_draft",
        "closing_issues",
        "review_evidence",
        "hold_labels",
        "issue",
        "slug",
    }
    if not isinstance(provenance, dict) or not required.issubset(provenance):
        raise WorkspaceError(
            "Workspace has no complete PR provenance; refusing destructive mutation"
        )
    if provenance["number"] != pr_number or provenance["head_ref_name"] != branch:
        raise WorkspaceError("Recorded PR number/branch does not match workspace state")
    if provenance["issue"] != issue or provenance["slug"] != slug:
        raise WorkspaceError("Recorded PR issue/slug does not match workspace state")

    details = get_pr_details_strict(pr_number)
    current = pr_provenance(details, issue=issue, slug=slug)
    state = details.get("state")
    if state != "OPEN" and not (allow_closed and state == "CLOSED"):
        raise WorkspaceError(f"PR #{pr_number} is {state!r}; refusing destructive mutation")
    if current != provenance:
        raise WorkspaceError(f"PR #{pr_number} provenance or head changed; refusing mutation")
    if remote_branch_oid_strict(branch) != provenance["head_ref_oid"]:
        raise WorkspaceError(
            f"PR #{pr_number} remote ref changed or disappeared; refusing mutation"
        )
    return details


def verify_pr_ready(
    provenance: dict,
    *,
    pr_number: int,
    branch: str,
    issue: int | None,
    slug: str,
) -> dict:
    """Verify that the exact recorded draft became ready without any other change."""
    details = get_pr_details_strict(pr_number)
    if details.get("state") != "OPEN" or details.get("isDraft") is not False:
        raise WorkspaceError(f"PR #{pr_number} is not an open ready PR")
    current = pr_provenance(details, issue=issue, slug=slug)
    expected = dict(provenance)
    expected["is_draft"] = False
    if current != expected:
        raise WorkspaceError(f"PR #{pr_number} changed while transitioning to ready")
    if remote_branch_oid_strict(branch) != provenance.get("head_ref_oid"):
        raise WorkspaceError(f"PR #{pr_number} remote ref changed while transitioning to ready")
    return details


def verify_recorded_pr_object(
    provenance: dict,
    *,
    pr_number: int,
    branch: str,
    issue: int | None,
    slug: str,
) -> dict:
    """Verify immutable PR identity without requiring its branch ref to exist."""
    details = get_pr_details_strict(pr_number)
    if details.get("state") not in {"OPEN", "CLOSED"}:
        raise WorkspaceError(f"PR #{pr_number} has unsafe state {details.get('state')!r}")
    current = pr_provenance(details, issue=issue, slug=slug)
    if current != provenance:
        raise WorkspaceError(f"PR #{pr_number} immutable provenance contradicts journal")
    if provenance.get("head_ref_name") != branch:
        raise WorkspaceError("Recorded PR branch contradicts terminal journal")
    return details


def check_existing_prs(issue_number: int) -> list[dict]:
    """Best-effort compatibility wrapper for non-coordination call sites."""
    try:
        return check_existing_prs_strict(issue_number)
    except (GitHubApiError, GitCommandError):
        return []


def classify_issue_prs(prs: list[dict]) -> str:
    """Classify PRs linked to one company-request issue.

    Any existing company PR is a submitted outcome for scheduling purposes.
    Cross-run draft takeover is unsafe: draft state, a deterministic branch,
    and a linked issue do not prove that the new resolver run owns the head.
    A still-running resolver uses its persisted workspace/head lease instead
    of entering through this issue-level classifier.
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
    if is_draft in {True, False}:
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
    """Retired resolver mutation: company automation must leave PRs draft."""
    raise WorkspaceError(f"Refusing to mark company PR #{pr_number} ready from resolver automation")


def mark_pr_draft(pr_number: int) -> None:
    """Return a PR to draft after a readiness-only race."""
    _run(
        ["gh", "pr", "ready", str(pr_number), "--undo", "--repo", _resolve_repo()],
        retries=_GH_RETRIES,
    )


def comment_on_pr(pr_number: int, body: str) -> None:
    """Add a comment to a PR."""
    _run(
        [
            "gh",
            "pr",
            "comment",
            str(pr_number),
            "--repo",
            _resolve_repo(),
            "--body",
            body,
        ],
        retries=_GH_RETRIES,
    )


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


def issue_has_comment_marker_strict(issue_number: int, marker: str) -> bool:
    """Return whether an issue has a marker-owned comment, failing closed."""
    import json

    args = [
        "gh",
        "issue",
        "view",
        str(issue_number),
        *_gh_repo_flag(),
        "--json",
        "comments",
    ]
    result = _run(args, retries=_GH_RETRIES)
    try:
        data = json.loads(result.stdout or "{}")
    except (json.JSONDecodeError, TypeError) as exc:
        raise GitHubApiError(args, 1, "Could not parse issue comments") from exc
    comments = data.get("comments") if isinstance(data, dict) else None
    if not isinstance(comments, list):
        raise GitHubApiError(args, 1, "Unexpected issue comments response")
    return any(
        isinstance(comment, dict)
        and isinstance(comment.get("body"), str)
        and comment["body"].startswith(marker)
        for comment in comments
    )


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


def is_issue_claimed_strict(issue_number: int) -> bool:
    """Return exact claim state, propagating lookup failures."""
    return bool(_get_claim_comment_ids_strict(issue_number))


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


def issue_state_and_labels_strict(issue_number: int) -> tuple[str, set[str]]:
    """Return exact issue state and labels."""
    import json

    args = [
        "gh",
        "issue",
        "view",
        str(issue_number),
        *_gh_repo_flag(),
        "--json",
        "state,labels",
    ]
    result = _run(args, retries=_GH_RETRIES)
    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise GitHubApiError(args, 1, "Could not parse issue state") from exc
    state = data.get("state") if isinstance(data, dict) else None
    labels = data.get("labels") if isinstance(data, dict) else None
    if state not in {"OPEN", "CLOSED"} or not isinstance(labels, list):
        raise GitHubApiError(args, 1, "Unexpected issue state response")
    names = {
        label["name"]
        for label in labels
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    }
    return state, names


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
        ["gh", "pr", "edit", str(pr_number), "--repo", _resolve_repo(), "--body", body],
        retries=_GH_RETRIES,
    )


def close_pr(pr_number: int) -> None:
    """Close a GitHub PR."""
    _run(
        ["gh", "pr", "close", str(pr_number), "--repo", _resolve_repo()],
        retries=_GH_RETRIES,
    )


def close_pr_if_open(pr_number: int) -> None:
    """Idempotently close a PR and verify that it is no longer open."""
    state = _run(
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            _resolve_repo(),
            "--json",
            "state",
            "--jq",
            ".state",
        ],
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
                "--repo",
                _resolve_repo(),
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
