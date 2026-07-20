"""Safe reconciliation for Codex runner worktrees.

The runner creates large, disposable Git worktrees, but their disposition is
not disposable metadata.  This module joins each directory to the SQLite run
ledger, verifies terminal remote state, archives unique dirty/debug material,
and only then removes the registered worktree.  Every applied decision is
recorded in the ledger before and after removal.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

ACTIVE_STATES = {"claimed", "running"}
TERMINAL_STATES = {
    "completed",
    "failed",
    "timeout",
    "submitted",
    "rejected",
    "escalated",
    "retryable",
    "interrupted",
    "skipped",
}
RESOLVED_OUTCOMES = {"submitted", "rejected", "escalated"}
DEBUG_OUTCOMES = {"retryable", "interrupted"}


class Ledger(Protocol):
    def worktree_runs(self) -> list[dict[str, Any]]: ...

    def record_worktree_reconciliation(self, **fields: Any) -> None: ...


@dataclass(frozen=True)
class RemoteProof:
    ok: bool
    kind: str
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass
class WorktreeItem:
    path: str
    name: str
    bytes: int
    run_id: str | None
    issue: int | None
    state: str
    export_status: str | None
    pr_number: int | None
    branch: str | None
    registered: bool
    locked: bool
    pid_live: bool
    dirty_entries: int
    classification: str = ""
    reason: str = ""
    planned_action: str = "retain"
    remote_proof: dict[str, Any] | None = None
    archive_path: str | None = None
    archive_sha256: str | None = None
    reclaimed_bytes: int = 0
    error: str | None = None


@dataclass
class WorktreeReport:
    apply: bool
    directories: int
    bytes_before: int
    removed: int
    reclaimed_bytes: int
    archived: int
    active: int
    retained: int
    removal_failures: int
    remaining_terminal_directories: int
    remaining_terminal_bytes: int
    max_terminal_directories: int
    max_terminal_bytes: int
    within_bounds: bool
    items: list[WorktreeItem]

    def to_dict(self, *, include_items: bool = True) -> dict[str, Any]:
        result = asdict(self)
        if not include_items:
            result.pop("items", None)
        return result


class GitHubRemoteVerifier:
    """Fail-closed verifier for linked PRs and explicit issue outcomes."""

    def __init__(self, *, repo_dir: Path, github: Any):
        self.repo_dir = repo_dir
        self.github = github
        self._pr_cache: dict[int, RemoteProof] = {}
        self._issue_cache: dict[int, Any] = {}

    def __call__(self, run: dict[str, Any]) -> RemoteProof:
        state = str(run.get("state") or "")
        issue = run.get("issue")
        pr_number = run.get("pr_number")
        branch = run.get("branch")

        if isinstance(pr_number, int):
            proof = self._verify_pr(pr_number, branch if isinstance(branch, str) else None)
            if not proof.ok:
                return proof
        elif isinstance(branch, str) and branch:
            proof = self._verify_branch(branch)
            if not proof.ok:
                return proof
        else:
            proof = RemoteProof(ok=True, kind="no_remote_artifact")

        # A submitted resolver outcome normally leaves the issue open while
        # the verified PR awaits review. The linked PR itself is the durable
        # remote proof; issue closure is only the fallback when no PR was
        # recorded (for example, after a merged-PR reconciliation).
        if state == "submitted" and isinstance(pr_number, int):
            return proof

        if state in RESOLVED_OUTCOMES:
            if not isinstance(issue, int):
                return RemoteProof(
                    ok=False,
                    kind="missing_issue",
                    error=f"{state} run has no issue number",
                )
            resolution = self._issue_cache.get(issue)
            if resolution is None:
                try:
                    resolution = self.github.issue_resolution(issue)
                except Exception as exc:  # noqa: BLE001 - remote proof must fail closed
                    return RemoteProof(
                        ok=False,
                        kind="issue_lookup_failed",
                        error=str(exc),
                    )
                self._issue_cache[issue] = resolution
            if getattr(resolution, "outcome", None) != state:
                return RemoteProof(
                    ok=False,
                    kind="outcome_mismatch",
                    detail={
                        "issue": issue,
                        "expected": state,
                        "observed": getattr(resolution, "outcome", None),
                        "issue_state": getattr(resolution, "state", None),
                    },
                    error="ledger outcome is not confirmed by GitHub",
                )
            return RemoteProof(
                ok=True,
                kind="issue_outcome",
                detail={
                    "issue": issue,
                    "outcome": state,
                    "remote": proof.detail,
                },
            )

        return proof

    def _verify_pr(self, number: int, expected_branch: str | None) -> RemoteProof:
        cached = self._pr_cache.get(number)
        if cached is not None:
            return cached
        result = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(number),
                "--json",
                "number,state,isDraft,headRefName,headRefOid,mergedAt,url",
            ],
            cwd=self.repo_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            proof = RemoteProof(
                ok=False,
                kind="pr_lookup_failed",
                detail={"pr_number": number},
                error=(result.stderr or "GitHub PR lookup failed").strip(),
            )
            self._pr_cache[number] = proof
            return proof
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            proof = RemoteProof(
                ok=False,
                kind="pr_lookup_invalid",
                detail={"pr_number": number},
                error=str(exc),
            )
            self._pr_cache[number] = proof
            return proof
        head = data.get("headRefName") if isinstance(data, dict) else None
        if expected_branch and head != expected_branch:
            proof = RemoteProof(
                ok=False,
                kind="pr_branch_mismatch",
                detail={
                    "pr_number": number,
                    "expected_branch": expected_branch,
                    "observed_branch": head,
                },
                error="linked PR branch does not match the ledger",
            )
            self._pr_cache[number] = proof
            return proof
        detail = {
            key: data.get(key)
            for key in (
                "number",
                "state",
                "isDraft",
                "headRefName",
                "headRefOid",
                "mergedAt",
                "url",
            )
        }
        proof = RemoteProof(ok=True, kind="pull_request", detail=detail)
        self._pr_cache[number] = proof
        return proof

    def _verify_branch(self, branch: str) -> RemoteProof:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"],
            cwd=self.repo_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            return RemoteProof(
                ok=False,
                kind="branch_lookup_failed",
                detail={"branch": branch},
                error="ledger branch has no verifiable remote ref or PR",
            )
        oid = result.stdout.split()[0] if result.stdout.split() else None
        return RemoteProof(
            ok=True,
            kind="remote_branch",
            detail={"branch": branch, "headRefOid": oid},
        )


def reconcile_worktrees(
    *,
    root: Path,
    repo_dir: Path,
    worktrees_dir: Path,
    archive_dir: Path,
    ledger: Ledger,
    remote_verifier: Callable[[dict[str, Any]], RemoteProof],
    pid_checker: Callable[[int, str], bool],
    max_terminal_directories: int,
    max_terminal_bytes: int,
    apply: bool,
    only_paths: set[Path] | None = None,
    pre_remove: Callable[[WorktreeItem], None] | None = None,
    remove_worktree: Callable[[Path], None] | None = None,
) -> WorktreeReport:
    """Classify and optionally retire terminal runner worktrees."""
    del root  # Kept explicit in the API because the caller's policy is root-scoped.
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    paths = [path for path in sorted(worktrees_dir.iterdir()) if path.is_dir()]
    selected = {path.resolve() for path in only_paths} if only_paths else None
    if selected is not None:
        paths = [path for path in paths if path.resolve() in selected]
    if not paths:
        return WorktreeReport(
            apply=apply,
            directories=0,
            bytes_before=0,
            removed=0,
            reclaimed_bytes=0,
            archived=0,
            active=0,
            retained=0,
            removal_failures=0,
            remaining_terminal_directories=0,
            remaining_terminal_bytes=0,
            max_terminal_directories=max_terminal_directories,
            max_terminal_bytes=max_terminal_bytes,
            within_bounds=True,
            items=[],
        )

    registered = _registered_worktrees(repo_dir)
    runs = ledger.worktree_runs()
    run_by_path = {
        str(Path(run["worktree_path"]).resolve()): run
        for run in runs
        if isinstance(run.get("worktree_path"), str) and run["worktree_path"]
    }
    items: list[WorktreeItem] = []
    bytes_before = 0
    removed = 0
    reclaimed = 0
    archived = 0
    failures = 0
    remover = remove_worktree or (lambda path: _remove_registered_worktree(repo_dir, path))
    now = int(time.time())

    for path in paths:
        resolved = path.resolve()
        size = _directory_bytes(path)
        bytes_before += size
        run = run_by_path.get(str(resolved))
        registration = registered.get(str(resolved), {})
        dirty_entries, status_error = _dirty_entry_count(path)
        state = str(run.get("state")) if run else "missing-ledger"
        pid = run.get("pid") if run else None
        run_id = str(run.get("run_id")) if run and run.get("run_id") else None
        pid_live = bool(run_id and isinstance(pid, int) and pid_checker(pid, run_id))
        item = WorktreeItem(
            path=str(path),
            name=path.name,
            bytes=size,
            run_id=run_id,
            issue=run.get("issue") if run and isinstance(run.get("issue"), int) else None,
            state=state,
            export_status=(
                str(run.get("export_status"))
                if run and isinstance(run.get("export_status"), str)
                else None
            ),
            pr_number=(
                run.get("pr_number") if run and isinstance(run.get("pr_number"), int) else None
            ),
            branch=(str(run.get("branch")) if run and isinstance(run.get("branch"), str) else None),
            registered=str(resolved) in registered,
            locked=bool(registration.get("locked")),
            pid_live=pid_live,
            dirty_entries=dirty_entries,
        )

        _classify(item, run=run, status_error=status_error)
        if item.classification == "terminal_candidate" and run is not None:
            proof = remote_verifier(run)
            item.remote_proof = asdict(proof)
            if not proof.ok:
                item.classification = "remote_unverified"
                item.reason = proof.error or "remote state could not be verified"
                item.planned_action = "retain"

        if item.classification == "terminal_candidate":
            workspace_artifacts = Path(item.path) / "apps" / "crawler" / ".workspace"
            must_archive = (
                item.dirty_entries > 0
                or item.state in DEBUG_OUTCOMES
                or workspace_artifacts.is_dir()
            )
            item.planned_action = "archive_remove" if must_archive else "remove"
            if apply:
                _record_event(
                    ledger,
                    item,
                    action="removal_started",
                    observed_at=now,
                )
                try:
                    if must_archive:
                        archive_path, archive_sha = _archive_worktree(
                            path,
                            archive_dir=archive_dir,
                            run_id=item.run_id or item.name,
                            item=item,
                        )
                        item.archive_path = str(archive_path)
                        item.archive_sha256 = archive_sha
                        archived += 1
                    if pre_remove is not None:
                        pre_remove(item)
                    remover(path)
                    if path.exists():
                        raise RuntimeError(
                            "worktree removal returned but the directory still exists"
                        )
                    item.classification = "removed"
                    item.reason = "terminal state and remote evidence verified"
                    item.reclaimed_bytes = size
                    removed += 1
                    reclaimed += size
                    _record_event(
                        ledger,
                        item,
                        action="removed",
                        observed_at=int(time.time()),
                    )
                except Exception as exc:  # noqa: BLE001 - removal must fail closed
                    item.classification = "removal_failed"
                    item.reason = "terminal worktree cleanup failed"
                    item.error = str(exc)
                    item.planned_action = "retain"
                    failures += 1
                    _record_event(
                        ledger,
                        item,
                        action="removal_failed",
                        observed_at=int(time.time()),
                    )
        elif apply:
            _record_event(
                ledger,
                item,
                action="retained",
                observed_at=now,
            )
        items.append(item)

    remaining_terminal = [
        item for item in items if item.classification not in {"removed", "active"}
    ]
    remaining_terminal_bytes = sum(item.bytes for item in remaining_terminal)
    within_bounds = (
        len(remaining_terminal) <= max_terminal_directories
        and remaining_terminal_bytes <= max_terminal_bytes
    )
    return WorktreeReport(
        apply=apply,
        directories=len(items),
        bytes_before=bytes_before,
        removed=removed,
        reclaimed_bytes=reclaimed,
        archived=archived,
        active=sum(item.classification == "active" for item in items),
        retained=sum(item.classification != "removed" for item in items),
        removal_failures=failures,
        remaining_terminal_directories=len(remaining_terminal),
        remaining_terminal_bytes=remaining_terminal_bytes,
        max_terminal_directories=max_terminal_directories,
        max_terminal_bytes=max_terminal_bytes,
        within_bounds=within_bounds,
        items=items,
    )


def _classify(
    item: WorktreeItem,
    *,
    run: dict[str, Any] | None,
    status_error: str | None,
) -> None:
    if item.pid_live or item.state in ACTIVE_STATES:
        item.classification = "active"
        item.reason = "ledger or live process marks the worktree active"
        return
    if run is None:
        item.classification = "missing_ledger"
        item.reason = "directory has no matching ledger run"
        return
    if item.locked:
        item.classification = "locked"
        item.reason = "git worktree is locked"
        return
    if not item.registered:
        item.classification = "unregistered"
        item.reason = "directory is not registered in the runner repository"
        return
    if status_error:
        item.classification = "status_failed"
        item.reason = status_error
        return
    if item.state not in TERMINAL_STATES:
        item.classification = "unknown_state"
        item.reason = f"ledger state {item.state!r} is not terminal"
        return
    item.classification = "terminal_candidate"
    item.reason = "terminal ledger state; awaiting remote verification"


def _registered_worktrees(repo_dir: Path) -> dict[str, dict[str, Any]]:
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_dir,
        text=True,
        capture_output=True,
        check=True,
    )
    registered: dict[str, dict[str, Any]] = {}
    current: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current = str(Path(line.removeprefix("worktree ")).resolve())
            registered[current] = {"locked": False}
        elif line.startswith("locked") and current:
            registered[current]["locked"] = True
    return registered


def _dirty_entry_count(path: Path) -> tuple[int, str | None]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=path,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return -1, (result.stderr or b"git status failed").decode(errors="replace").strip()
    return len([entry for entry in result.stdout.split(b"\0") if entry]), None


def _directory_bytes(root: Path) -> int:
    total = 0
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(directory)
        kept_dirs = []
        for name in dirnames:
            path = base / name
            try:
                stat = path.lstat()
            except OSError:
                continue
            total += stat.st_size
            if not path.is_symlink():
                kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in filenames:
            try:
                total += (base / name).lstat().st_size
            except OSError:
                continue
    return total


def _remove_registered_worktree(repo_dir: Path, path: Path) -> None:
    result = subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=repo_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git worktree remove failed").strip())


def _archive_worktree(
    worktree: Path,
    *,
    archive_dir: Path,
    run_id: str,
    item: WorktreeItem,
) -> tuple[Path, str]:
    archive_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(archive_dir, 0o700)
    safe_run_id = "".join(char if char.isalnum() or char in "-_." else "_" for char in run_id)
    destination = archive_dir / f"{safe_run_id}.tar.gz"
    temporary = archive_dir / f".{safe_run_id}.{os.getpid()}.tmp"

    patch = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=worktree,
        capture_output=True,
        check=True,
    ).stdout
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=worktree,
        capture_output=True,
        check=True,
    ).stdout
    candidates: dict[Path, str] = {}
    for raw in untracked.split(b"\0"):
        if raw:
            relative = Path(os.fsdecode(raw))
            candidates[worktree / relative] = f"untracked/{relative.as_posix()}"
    workspace_root = worktree / "apps" / "crawler" / ".workspace"
    if workspace_root.is_dir():
        for source in workspace_root.rglob("*"):
            if source.is_file() or source.is_symlink():
                relative = source.relative_to(workspace_root)
                candidates[source] = f"workspace/{relative.as_posix()}"

    inventory = []
    try:
        with tarfile.open(temporary, "w:gz") as archive:
            if patch:
                _tar_add_bytes(archive, "tracked.patch", patch)
            for source, archive_name in sorted(candidates.items(), key=lambda pair: pair[1]):
                if source.is_symlink():
                    target = os.readlink(source)
                    inventory.append(
                        {
                            "archive_name": archive_name,
                            "source": str(source.relative_to(worktree)),
                            "type": "symlink",
                            "target_sha256": hashlib.sha256(target.encode()).hexdigest(),
                        }
                    )
                    archive.add(source, arcname=archive_name, recursive=False)
                    continue
                if not source.is_file():
                    continue
                digest, size = _hash_file(source)
                inventory.append(
                    {
                        "archive_name": archive_name,
                        "source": str(source.relative_to(worktree)),
                        "type": "file",
                        "bytes": size,
                        "sha256": digest,
                    }
                )
                archive.add(source, arcname=archive_name, recursive=False)
            manifest = {
                "schema_version": 1,
                "created_at": int(time.time()),
                "run_id": item.run_id,
                "issue": item.issue,
                "state": item.state,
                "worktree_path": item.path,
                "worktree_bytes": item.bytes,
                "dirty_entries": item.dirty_entries,
                "remote_proof": item.remote_proof,
                "tracked_patch_bytes": len(patch),
                "files": inventory,
            }
            _tar_add_bytes(
                archive,
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n",
            )
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    digest, _ = _hash_file(destination)
    return destination, digest


def _tar_add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    info.mtime = int(time.time())
    archive.addfile(info, io.BytesIO(data))


def _hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _record_event(
    ledger: Ledger,
    item: WorktreeItem,
    *,
    action: str,
    observed_at: int,
) -> None:
    ledger.record_worktree_reconciliation(
        worktree_path=item.path,
        run_id=item.run_id,
        issue=item.issue,
        state=item.state,
        classification=item.classification,
        reason=item.reason,
        action=action,
        bytes_before=item.bytes,
        dirty_entries=item.dirty_entries,
        remote_proof_json=(
            json.dumps(item.remote_proof, sort_keys=True) if item.remote_proof is not None else None
        ),
        archive_path=item.archive_path,
        archive_sha256=item.archive_sha256,
        reclaimed_bytes=item.reclaimed_bytes,
        error=item.error,
        observed_at=observed_at,
    )
