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
import re
import secrets
import stat
import subprocess
import tarfile
import tempfile
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from src.workspace.safe_cleanup import (
    directory_open_flags,
    open_absolute_directory_no_follow,
    recover_pending_rmtree_claims,
    unlink_child_at,
    validate_child_name,
)

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
TRUSTED_GITHUB_REPOSITORY = "colophon-group/jobseek"
ARCHIVE_METADATA_RESERVE_BYTES = 1024 * 1024


def _git_proof_env() -> dict[str, str]:
    """Disable child-controlled history substitution for destructive Git proof."""
    return {
        **os.environ,
        "GIT_GRAFT_FILE": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


class Ledger(Protocol):
    def worktree_runs(self) -> list[dict[str, Any]]: ...

    def record_worktree_reconciliation(self, **fields: Any) -> None: ...

    def worktree_removal_lease(
        self,
        *,
        run_id: str | None,
        worktree_path: Path,
    ) -> AbstractContextManager[None]: ...


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
    source: str = "runner"
    head_oid: str | None = None
    main_oid: str | None = None
    unique_commits: bool = False


@dataclass(frozen=True)
class _ArchiveCandidateFingerprint:
    source: str
    archive_name: str
    kind: str
    mode: int
    bytes: int | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class _WorktreeStateSnapshot:
    head_oid: str
    dirty_entries: int
    status_sha256: str
    tracked_patch_sha256: str
    workspace_root_json: str
    candidates: tuple[_ArchiveCandidateFingerprint, ...]


class _WorktreeBecameActive(RuntimeError):
    """Raised when fresh runner evidence protects a removal candidate."""


_RemovalGuard = Callable[[], None]
_WorktreeRemover = Callable[[Path, _RemovalGuard], None]


class _BoundedArchiveWriter:
    """File proxy that prevents a staging archive from exceeding its durable budget."""

    def __init__(self, fileobj: Any, *, max_bytes: int) -> None:
        self._fileobj = fileobj
        self._max_bytes = max_bytes

    def write(self, data: bytes) -> int:
        if self._fileobj.tell() + len(data) > self._max_bytes:
            raise RuntimeError(
                f"worktree quarantine archive exceeded {self._max_bytes} byte staging budget"
            )
        return int(self._fileobj.write(data))

    def read(self, size: int = -1) -> bytes:
        return bytes(self._fileobj.read(size))

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        return int(self._fileobj.seek(offset, whence))

    def tell(self) -> int:
        return int(self._fileobj.tell())

    def flush(self) -> None:
        self._fileobj.flush()

    def close(self) -> None:
        self.flush()


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
    retained_worktree_bytes: int = 0
    quarantine_bytes: int = 0

    def to_dict(self, *, include_items: bool = True) -> dict[str, Any]:
        result = asdict(self)
        if not include_items:
            result.pop("items", None)
        return result


def combine_worktree_reports(
    reports: list[WorktreeReport],
    *,
    max_terminal_directories: int,
    max_terminal_bytes: int,
    quarantine_dir: Path | None = None,
) -> WorktreeReport:
    """Combine independently reconciled roots under one retention budget."""
    items = [item for report in reports for item in report.items]
    remaining = [item for item in items if item.classification not in {"removed", "active"}]
    retained_worktree_bytes = sum(item.bytes for item in remaining)
    quarantine_bytes = (
        _directory_bytes(quarantine_dir)
        if quarantine_dir is not None
        else max((report.quarantine_bytes for report in reports), default=0)
    )
    remaining_bytes = retained_worktree_bytes + quarantine_bytes
    return WorktreeReport(
        apply=any(report.apply for report in reports),
        directories=sum(report.directories for report in reports),
        bytes_before=sum(report.bytes_before for report in reports),
        removed=sum(report.removed for report in reports),
        reclaimed_bytes=sum(report.reclaimed_bytes for report in reports),
        archived=sum(report.archived for report in reports),
        active=sum(report.active for report in reports),
        retained=sum(report.retained for report in reports),
        removal_failures=sum(report.removal_failures for report in reports),
        remaining_terminal_directories=len(remaining),
        remaining_terminal_bytes=remaining_bytes,
        max_terminal_directories=max_terminal_directories,
        max_terminal_bytes=max_terminal_bytes,
        within_bounds=(
            len(remaining) <= max_terminal_directories and remaining_bytes <= max_terminal_bytes
        ),
        items=items,
        retained_worktree_bytes=retained_worktree_bytes,
        quarantine_bytes=quarantine_bytes,
    )


class GitHubRemoteVerifier:
    """Fail-closed verifier for linked PRs and explicit issue outcomes."""

    def __init__(self, *, repo_dir: Path, github: Any, repository: str):
        self.repo_dir = repo_dir
        self.github = github
        self.repository = repository
        self._pr_cache: dict[int, RemoteProof] = {}
        self._issue_cache: dict[int, Any] = {}
        self._branch_cache: dict[str, RemoteProof] = {}

    def verify_main(self) -> RemoteProof:
        """Resolve and refresh main through an explicit GitHub repository identity."""
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
            return RemoteProof(
                ok=False,
                kind="main_repository_invalid",
                detail={"repository": self.repository},
                error="trusted GitHub repository identity is invalid",
            )
        lookup = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                f"repos/{self.repository}/commits/main",
                "--jq",
                ".sha",
            ],
            cwd=self.repo_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        if lookup.returncode != 0:
            return RemoteProof(
                ok=False,
                kind="main_lookup_failed",
                detail={"repository": self.repository},
                error=(lookup.stderr or lookup.stdout or "GitHub main lookup failed").strip(),
            )
        main_oid = lookup.stdout.strip().lower()
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", main_oid):
            return RemoteProof(
                ok=False,
                kind="main_lookup_invalid",
                detail={"repository": self.repository},
                error="GitHub main lookup returned an invalid commit OID",
            )

        return self._refresh_trusted_ref(
            source_ref="refs/heads/main",
            trusted_ref="refs/jobseek-reconcile/authoritative-main",
            expected_oid=main_oid,
            proof_kind="authoritative_main",
            failure_prefix="main",
            detail={"repository": self.repository, "headRefOid": main_oid},
        )

    def _refresh_trusted_ref(
        self,
        *,
        source_ref: str,
        trusted_ref: str,
        expected_oid: str,
        proof_kind: str,
        failure_prefix: str,
        detail: dict[str, Any],
    ) -> RemoteProof:
        """Fetch one explicit trusted-repository ref and pin its immutable OID."""
        trusted_url = f"https://github.com/{self.repository}.git"
        refresh = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "protocol.ext.allow=never",
                "-c",
                "protocol.file.allow=never",
                "-c",
                "protocol.git.allow=never",
                "-c",
                "protocol.ssh.allow=never",
                "-c",
                "credential.helper=",
                "fetch",
                "--force",
                "--no-tags",
                "--no-recurse-submodules",
                "--no-write-fetch-head",
                trusted_url,
                f"+{source_ref}:{trusted_ref}",
            ],
            cwd=self.repo_dir,
            env={
                **_git_proof_env(),
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        if refresh.returncode != 0:
            return RemoteProof(
                ok=False,
                kind=f"{failure_prefix}_refresh_failed",
                detail=detail,
                error=(
                    refresh.stderr
                    or refresh.stdout
                    or f"trusted GitHub {failure_prefix} refresh failed"
                ).strip(),
            )
        refreshed = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "rev-parse",
                "--verify",
                f"{trusted_ref}^{{commit}}",
            ],
            cwd=self.repo_dir,
            env=_git_proof_env(),
            text=True,
            capture_output=True,
            check=False,
        )
        refreshed_oid = refreshed.stdout.strip().lower()
        refreshed_detail = {
            **detail,
            "refreshed_oid": refreshed_oid or None,
            "trusted_ref": trusted_ref,
        }
        if refreshed.returncode != 0:
            return RemoteProof(
                ok=False,
                kind=f"{failure_prefix}_refresh_failed",
                detail=refreshed_detail,
                error=(
                    refreshed.stderr
                    or refreshed.stdout
                    or f"refreshed {failure_prefix} is not a commit"
                ).strip(),
            )
        if refreshed_oid != expected_oid:
            return RemoteProof(
                ok=False,
                kind=f"{failure_prefix}_refresh_mismatch",
                detail=refreshed_detail,
                error=(
                    f"GitHub {failure_prefix} moved or refresh did not match "
                    "the authoritative lookup"
                ),
            )
        return RemoteProof(ok=True, kind=proof_kind, detail=refreshed_detail)

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
            proof = self.verify_branch(branch)
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
                    resolution = self.github.issue_resolution(
                        issue,
                        repository=self.repository,
                    )
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
                "--repo",
                self.repository,
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

    def verify_branch(self, branch: str, *, allow_absent: bool = False) -> RemoteProof:
        cached = self._branch_cache.get(branch)
        if cached is None:
            cached = self._lookup_branch(branch)
            self._branch_cache[branch] = cached
        if cached.kind == "remote_branch_absent" and not allow_absent:
            return RemoteProof(
                ok=False,
                kind="branch_lookup_failed",
                detail=cached.detail,
                error="ledger branch has no verifiable remote ref or PR",
            )
        return cached

    def _lookup_branch(self, branch: str) -> RemoteProof:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "GET",
                f"repos/{self.repository}/git/matching-refs/heads/{quote(branch, safe='/')}",
            ],
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
        try:
            candidates = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return RemoteProof(
                ok=False,
                kind="branch_lookup_invalid",
                detail={"branch": branch, "repository": self.repository},
                error=str(exc),
            )
        if not isinstance(candidates, list):
            return RemoteProof(
                ok=False,
                kind="branch_lookup_invalid",
                detail={"branch": branch, "repository": self.repository},
                error="GitHub branch lookup returned an invalid response",
            )
        exact_ref = f"refs/heads/{branch}"
        exact = [
            candidate
            for candidate in candidates
            if isinstance(candidate, dict) and candidate.get("ref") == exact_ref
        ]
        if not exact:
            return RemoteProof(
                ok=True,
                kind="remote_branch_absent",
                detail={
                    "branch": branch,
                    "headRefOid": None,
                    "repository": self.repository,
                },
            )
        if len(exact) != 1 or not isinstance(exact[0].get("object"), dict):
            return RemoteProof(
                ok=False,
                kind="branch_lookup_invalid",
                detail={"branch": branch, "repository": self.repository},
                error="GitHub branch lookup returned ambiguous exact refs",
            )
        oid = exact[0]["object"].get("sha")
        oid = oid.strip().lower() if isinstance(oid, str) else ""
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", oid):
            return RemoteProof(
                ok=False,
                kind="branch_lookup_invalid",
                detail={"branch": branch, "repository": self.repository},
                error="GitHub branch lookup returned an invalid commit OID",
            )
        branch_hash = hashlib.sha256(branch.encode()).hexdigest()[:24]
        return self._refresh_trusted_ref(
            source_ref=exact_ref,
            trusted_ref=f"refs/jobseek-reconcile/branch-{branch_hash}",
            expected_oid=oid,
            proof_kind="remote_branch",
            failure_prefix="branch",
            detail={
                "branch": branch,
                "headRefOid": oid,
                "repository": self.repository,
            },
        )


def _prepare_worktree_root(worktrees_dir: Path) -> Path:
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    if worktrees_dir.is_symlink():
        raise RuntimeError(f"worktree root must not be a symlink: {worktrees_dir}")
    resolved = worktrees_dir.resolve(strict=True)
    if not resolved.is_dir():
        raise RuntimeError(f"worktree root is not a directory: {worktrees_dir}")
    return resolved


def _worktree_entries(worktrees_dir: Path) -> list[Path]:
    return sorted(worktrees_dir.iterdir())


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _inspect_worktree_path(path: Path, *, root_resolved: Path) -> tuple[Path | None, str | None]:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        return None, f"could not inspect worktree path: {exc}"
    if stat.S_ISLNK(mode):
        return None, "worktree root entry is a symlink"
    if not stat.S_ISDIR(mode):
        return None, "worktree root entry is not a directory"
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        return None, f"could not resolve worktree path: {exc}"
    if resolved.parent != root_resolved:
        return None, "resolved worktree path escapes its configured root"
    return resolved, None


def _validate_removal_target(
    *,
    repo_dir: Path,
    worktrees_dir: Path,
    path: Path,
    expected_resolved: Path,
    expected_head: str | None,
) -> None:
    root_resolved = _prepare_worktree_root(worktrees_dir)
    resolved, path_error = _inspect_worktree_path(path, root_resolved=root_resolved)
    if path_error or resolved is None:
        raise RuntimeError(path_error or "worktree path validation failed")
    if resolved != expected_resolved:
        raise RuntimeError("worktree path changed after classification")
    registration = _registered_worktrees(repo_dir).get(str(resolved))
    if registration is None:
        raise RuntimeError("worktree is no longer registered in the expected repository")
    if registration.get("locked"):
        raise RuntimeError("worktree became locked after classification")
    if expected_head is not None and registration.get("head") != expected_head:
        raise RuntimeError("worktree HEAD changed after classification")


def _validated_worktree_snapshot(
    *,
    repo_dir: Path,
    worktrees_dir: Path,
    path: Path,
    expected_resolved: Path,
    expected_head: str | None,
    activity_checker: Callable[[Path], None],
) -> _WorktreeStateSnapshot:
    """Revalidate identity/activity and fingerprint the current removable state."""
    _validate_removal_target(
        repo_dir=repo_dir,
        worktrees_dir=worktrees_dir,
        path=path,
        expected_resolved=expected_resolved,
        expected_head=expected_head,
    )
    activity_checker(expected_resolved)
    snapshot = _worktree_state_snapshot(path, expected_head=expected_head)
    _validate_removal_target(
        repo_dir=repo_dir,
        worktrees_dir=worktrees_dir,
        path=path,
        expected_resolved=expected_resolved,
        expected_head=expected_head,
    )
    activity_checker(expected_resolved)
    return snapshot


def _require_same_worktree_state(
    expected: _WorktreeStateSnapshot,
    observed: _WorktreeStateSnapshot,
    *,
    stage: str,
) -> None:
    if observed != expected:
        raise RuntimeError(f"worktree changed {stage}; refusing stale removal")


def _pre_remove_transition_is_safe(
    before: _WorktreeStateSnapshot,
    after: _WorktreeStateSnapshot,
) -> bool:
    """Allow the runner hook to delete only already-archived workspace evidence."""
    if before.head_oid != after.head_oid:
        return False
    if before.tracked_patch_sha256 != after.tracked_patch_sha256:
        return False

    before_regular = {
        candidate
        for candidate in before.candidates
        if not candidate.archive_name.startswith("workspace/")
    }
    after_regular = {
        candidate
        for candidate in after.candidates
        if not candidate.archive_name.startswith("workspace/")
    }
    if before_regular != after_regular:
        return False

    before_workspace = {
        candidate
        for candidate in before.candidates
        if candidate.archive_name.startswith("workspace/")
    }
    after_workspace = {
        candidate
        for candidate in after.candidates
        if candidate.archive_name.startswith("workspace/")
    }
    if not after_workspace.issubset(before_workspace):
        return False
    return (
        after.workspace_root_json == before.workspace_root_json
        or after.workspace_root_json == "null"
    )


def _guarded_retire_worktree(
    *,
    repo_dir: Path,
    worktrees_dir: Path,
    archive_dir: Path,
    path: Path,
    expected_resolved: Path,
    item: WorktreeItem,
    activity_checker: Callable[[Path], None],
    archive_id: str,
    archive_required: bool,
    max_archive_bytes: int,
    remover: _WorktreeRemover,
    removal_lease: Callable[[], AbstractContextManager[None]],
    pre_remove: Callable[[WorktreeItem], None] | None = None,
) -> bool:
    """Archive and remove only while identity, activity, and content stay stable."""
    expected = _validated_worktree_snapshot(
        repo_dir=repo_dir,
        worktrees_dir=worktrees_dir,
        path=path,
        expected_resolved=expected_resolved,
        expected_head=item.head_oid,
        activity_checker=activity_checker,
    )
    item.dirty_entries = expected.dirty_entries
    must_archive = (
        archive_required or expected.dirty_entries > 0 or expected.workspace_root_json != "null"
    )
    item.planned_action = "archive_remove" if must_archive else "remove"
    archived = False

    if must_archive:

        def verify_before_publish() -> None:
            current = _validated_worktree_snapshot(
                repo_dir=repo_dir,
                worktrees_dir=worktrees_dir,
                path=path,
                expected_resolved=expected_resolved,
                expected_head=item.head_oid,
                activity_checker=activity_checker,
            )
            _require_same_worktree_state(
                expected,
                current,
                stage="while its archive was being created",
            )

        archive_path, archive_sha = _archive_worktree(
            path,
            archive_dir=archive_dir,
            run_id=archive_id,
            item=item,
            include_unique_commits=item.unique_commits,
            unique_commit_base_oid=item.main_oid,
            max_archive_bytes=max_archive_bytes,
            expected_snapshot=expected,
            pre_publish_check=verify_before_publish,
        )
        item.archive_path = str(archive_path)
        item.archive_sha256 = archive_sha
        archived = True
        current = _validated_worktree_snapshot(
            repo_dir=repo_dir,
            worktrees_dir=worktrees_dir,
            path=path,
            expected_resolved=expected_resolved,
            expected_head=item.head_oid,
            activity_checker=activity_checker,
        )
        _require_same_worktree_state(expected, current, stage="after it was archived")

    if pre_remove is not None:
        pre_remove(item)
        current = _validated_worktree_snapshot(
            repo_dir=repo_dir,
            worktrees_dir=worktrees_dir,
            path=path,
            expected_resolved=expected_resolved,
            expected_head=item.head_oid,
            activity_checker=activity_checker,
        )
        if current != expected:
            if not archived or not _pre_remove_transition_is_safe(expected, current):
                raise RuntimeError("worktree changed during pre-remove cleanup; retaining it")
            expected = current

    def validate_original_before_claim() -> None:
        current = _validated_worktree_snapshot(
            repo_dir=repo_dir,
            worktrees_dir=worktrees_dir,
            path=path,
            expected_resolved=expected_resolved,
            expected_head=item.head_oid,
            activity_checker=activity_checker,
        )
        _require_same_worktree_state(expected, current, stage="immediately before removal claim")

    with removal_lease():
        validate_original_before_claim()
        claimed_path = _claim_registered_worktree(
            repo_dir=repo_dir,
            worktrees_dir=worktrees_dir,
            path=path,
            expected_resolved=expected_resolved,
            expected_head=item.head_oid,
        )
        claimed_resolved = claimed_path.resolve(strict=True)

        def validate_claim_at_remover_entry() -> None:
            if _path_exists_no_follow(path):
                raise RuntimeError("original worktree path was recreated after removal claim")
            current = _validated_worktree_snapshot(
                repo_dir=repo_dir,
                worktrees_dir=worktrees_dir,
                path=claimed_path,
                expected_resolved=claimed_resolved,
                expected_head=item.head_oid,
                activity_checker=activity_checker,
            )
            _require_same_worktree_state(expected, current, stage="after atomic removal claim")

        try:
            validate_claim_at_remover_entry()
            remover(claimed_path, validate_claim_at_remover_entry)
            if _path_exists_no_follow(claimed_path):
                raise RuntimeError("worktree remover returned but its atomic claim still exists")
            if str(claimed_resolved) in _registered_worktrees(repo_dir):
                raise RuntimeError(
                    "worktree remover returned but its atomic claim is still registered"
                )
            if _path_exists_no_follow(path):
                raise RuntimeError("original worktree path was recreated during claimed removal")
        except BaseException:
            if _path_exists_no_follow(claimed_path):
                _restore_claimed_worktree(
                    repo_dir=repo_dir,
                    claimed_path=claimed_path,
                    original_path=path,
                )
            raise
    return archived


def reconcile_worktrees(
    *,
    root: Path,
    repo_dir: Path,
    worktrees_dir: Path,
    archive_dir: Path,
    ledger: Ledger,
    remote_verifier: Callable[[dict[str, Any]], RemoteProof],
    authoritative_main_verifier: Callable[[], RemoteProof],
    pid_checker: Callable[[int, str], bool],
    max_terminal_directories: int,
    max_terminal_bytes: int,
    apply: bool,
    only_paths: set[Path] | None = None,
    pre_remove: Callable[[WorktreeItem], None] | None = None,
    remove_worktree: _WorktreeRemover | None = None,
) -> WorktreeReport:
    """Classify and optionally retire terminal runner worktrees."""
    del root  # Kept explicit in the API because the caller's policy is root-scoped.
    root_resolved = _prepare_worktree_root(worktrees_dir)
    if apply:
        recover_pending_rmtree_claims(worktrees_dir)
    paths = _worktree_entries(worktrees_dir)
    selected = {_absolute_path(path) for path in only_paths} if only_paths else None
    if selected is not None:
        paths = [path for path in paths if _absolute_path(path) in selected]
    if not paths:
        return _empty_report(
            apply=apply,
            max_terminal_directories=max_terminal_directories,
            max_terminal_bytes=max_terminal_bytes,
            quarantine_bytes=_directory_bytes(archive_dir),
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
    remover = remove_worktree or (
        lambda path, final_guard: _remove_registered_worktree(
            repo_dir,
            path,
            final_guard=final_guard,
        )
    )
    now = int(time.time())
    authoritative_main: RemoteProof | None = None

    for path in paths:
        resolved, path_error = _inspect_worktree_path(path, root_resolved=root_resolved)
        lookup_path = resolved or _absolute_path(path)
        size = _directory_bytes(path)
        bytes_before += size
        run = run_by_path.get(str(lookup_path))
        registration = registered.get(str(lookup_path), {}) if resolved is not None else {}
        if path_error:
            dirty_entries, status_error = -1, None
        else:
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
            registered=resolved is not None and str(resolved) in registered,
            locked=bool(registration.get("locked")),
            pid_live=pid_live,
            dirty_entries=dirty_entries,
            head_oid=(
                str(registration.get("head")) if isinstance(registration.get("head"), str) else None
            ),
        )

        _classify(item, run=run, path_error=path_error, status_error=status_error)
        if item.classification == "terminal_candidate" and run is not None:
            if authoritative_main is None:
                try:
                    authoritative_main = authoritative_main_verifier()
                except Exception as exc:  # noqa: BLE001 - destructive proof must fail closed
                    authoritative_main = RemoteProof(
                        ok=False,
                        kind="main_lookup_failed",
                        error=f"authoritative main lookup failed: {exc}",
                    )
            proof = authoritative_main
            main_oid = _remote_head_oid(authoritative_main.detail) if proof.ok else None
            if proof.ok and main_oid is None:
                proof = RemoteProof(
                    ok=False,
                    kind="main_lookup_invalid",
                    detail={"authoritative_main": asdict(authoritative_main)},
                    error="authoritative main proof contained no commit OID",
                )
            elif proof.ok and main_oid is not None:
                item.main_oid = main_oid
                proof = remote_verifier(run)
                if proof.ok:
                    proof = _runner_head_proof(
                        repo_dir=repo_dir,
                        item=item,
                        remote_proof=proof,
                        main_oid=main_oid,
                        main_proof=authoritative_main,
                    )
            item.remote_proof = asdict(proof)
            if not proof.ok:
                item.classification = "remote_unverified"
                item.reason = proof.error or "remote state could not be verified"
                item.planned_action = "retain"
            else:
                item.unique_commits = proof.kind == "local_unique_commits"

        if item.classification == "terminal_candidate":
            workspace_artifacts = Path(item.path) / "apps" / "crawler" / ".workspace"
            must_archive = (
                item.dirty_entries > 0
                or item.unique_commits
                or item.state in DEBUG_OUTCOMES
                or _path_exists_no_follow(workspace_artifacts)
            )
            item.planned_action = "archive_remove" if must_archive else "remove"
            if apply:
                _record_event(
                    ledger,
                    item,
                    action="removal_started",
                    observed_at=now,
                )
                was_archived = False
                try:
                    if resolved is None:
                        raise RuntimeError("worktree path was not safely resolved")
                    if run is None:
                        raise RuntimeError("runner ledger entry disappeared before removal")

                    def runner_activity_check(
                        current_resolved: Path,
                        expected_run: dict[str, Any] = run,
                        expected_resolved: Path = resolved,
                    ) -> None:
                        del current_resolved
                        _runner_activity_check(
                            ledger=ledger,
                            expected_run=expected_run,
                            expected_resolved=expected_resolved,
                            pid_checker=pid_checker,
                        )

                    was_archived = _guarded_retire_worktree(
                        repo_dir=repo_dir,
                        worktrees_dir=worktrees_dir,
                        archive_dir=archive_dir,
                        path=path,
                        expected_resolved=resolved,
                        item=item,
                        activity_checker=runner_activity_check,
                        archive_id=item.run_id or item.name,
                        archive_required=(item.unique_commits or item.state in DEBUG_OUTCOMES),
                        max_archive_bytes=max_terminal_bytes,
                        remover=remover,
                        removal_lease=lambda run_id=item.run_id, worktree_path=path: (
                            ledger.worktree_removal_lease(
                                run_id=run_id,
                                worktree_path=worktree_path,
                            )
                        ),
                        pre_remove=pre_remove,
                    )
                    if was_archived:
                        archived += 1
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
                except _WorktreeBecameActive as exc:
                    item.classification = "active"
                    item.reason = str(exc)
                    item.error = None
                    item.pid_live = True
                    item.planned_action = "retain"
                    _record_event(
                        ledger,
                        item,
                        action="retained",
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
                finally:
                    if item.archive_path is not None and not was_archived:
                        archived += 1
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
    retained_worktree_bytes = sum(item.bytes for item in remaining_terminal)
    quarantine_bytes = _directory_bytes(archive_dir)
    remaining_terminal_bytes = retained_worktree_bytes + quarantine_bytes
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
        retained_worktree_bytes=retained_worktree_bytes,
        quarantine_bytes=quarantine_bytes,
    )


def reconcile_managed_worktrees(
    *,
    repo_dir: Path,
    worktrees_dir: Path,
    archive_dir: Path,
    ledger: Ledger,
    authoritative_main_verifier: Callable[[], RemoteProof],
    branch_verifier: Callable[[str], RemoteProof],
    pid_checker: Callable[[int, str], bool],
    live_path_checker: Callable[[Path], bool],
    max_terminal_directories: int,
    max_terminal_bytes: int,
    apply: bool,
    context_by_path: dict[str, dict[str, Any]] | None = None,
    remove_worktree: _WorktreeRemover | None = None,
) -> WorktreeReport:
    """Reconcile worktrees registered to the separate ``ws`` managed clone.

    Managed worktrees are not direct runner-ledger paths.  They are safe to
    retire only when they are registered, unlocked, not live or associated
    with an active run, inspectable, and either preserved remotely/in main or
    archived with their unique commit objects first.
    """
    contexts = context_by_path or {}
    root_resolved = _prepare_worktree_root(worktrees_dir)
    if apply:
        recover_pending_rmtree_claims(worktrees_dir)
    paths = _worktree_entries(worktrees_dir)
    if not paths:
        return _empty_report(
            apply=apply,
            max_terminal_directories=max_terminal_directories,
            max_terminal_bytes=max_terminal_bytes,
            quarantine_bytes=_directory_bytes(archive_dir),
        )

    registration_error = None
    try:
        registered = _registered_worktrees(repo_dir)
    except (OSError, subprocess.CalledProcessError) as exc:
        registered = {}
        registration_error = f"managed repository registration lookup failed: {exc}"
    runs = ledger.worktree_runs()
    active_runs = [run for run in runs if _run_is_active(run, pid_checker=pid_checker)]
    active_since = min(
        (
            int(run.get("started_at") or run.get("created_at") or 0)
            for run in active_runs
            if int(run.get("started_at") or run.get("created_at") or 0) > 0
        ),
        default=None,
    )
    items: list[WorktreeItem] = []
    bytes_before = 0
    removed = 0
    reclaimed = 0
    archived = 0
    failures = 0
    remover = remove_worktree or (
        lambda path, final_guard: _remove_registered_worktree(
            repo_dir,
            path,
            final_guard=final_guard,
        )
    )
    now = int(time.time())
    authoritative_main: RemoteProof | None = None

    for path in paths:
        resolved, path_error = _inspect_worktree_path(path, root_resolved=root_resolved)
        lookup_path = resolved or _absolute_path(path)
        resolved_key = str(lookup_path)
        size = _directory_bytes(path)
        bytes_before += size
        registration = registered.get(resolved_key, {}) if resolved is not None else {}
        context = contexts.get(resolved_key, {})
        if path_error:
            dirty_entries, status_error = -1, None
        else:
            dirty_entries, status_error = _dirty_entry_count(path)
        run_id = str(context.get("run_id")) if context.get("run_id") else None
        pid = context.get("pid")
        context_pid_live = bool(run_id and isinstance(pid, int) and pid_checker(pid, run_id))
        active_window = False
        if active_since is not None and path_error is None:
            try:
                active_window = path.stat().st_ctime >= active_since
            except OSError as exc:
                status_error = status_error or f"could not stat managed worktree: {exc}"
        path_live = bool(resolved is not None and live_path_checker(resolved))
        state = str(context.get("state") or "managed")
        branch = registration.get("branch") or context.get("branch")
        item = WorktreeItem(
            path=str(path),
            name=path.name,
            bytes=size,
            run_id=run_id,
            issue=context.get("issue") if isinstance(context.get("issue"), int) else None,
            state=state,
            export_status=(
                str(context.get("export_status"))
                if isinstance(context.get("export_status"), str)
                else None
            ),
            pr_number=(
                context.get("pr_number") if isinstance(context.get("pr_number"), int) else None
            ),
            branch=str(branch) if isinstance(branch, str) else None,
            registered=resolved is not None and resolved_key in registered,
            locked=bool(registration.get("locked")),
            pid_live=context_pid_live or path_live or active_window,
            dirty_entries=dirty_entries,
            source="managed",
            head_oid=(
                str(registration.get("head")) if isinstance(registration.get("head"), str) else None
            ),
        )

        if path_error:
            item.classification = "unsafe_path"
            item.reason = path_error
        elif item.pid_live or state in ACTIVE_STATES:
            item.classification = "active"
            if active_window and not (context_pid_live or path_live or state in ACTIVE_STATES):
                item.reason = "managed worktree was created during an active runner window"
            else:
                item.reason = "active runner context or live process protects managed worktree"
        elif item.locked:
            item.classification = "locked"
            item.reason = "git worktree is locked"
        elif not item.registered:
            item.classification = "unregistered"
            item.reason = "directory is not registered in the managed repository"
        elif status_error:
            item.classification = "status_failed"
            item.reason = status_error
        elif registration_error:
            item.classification = "remote_unverified"
            item.reason = registration_error
        else:
            if authoritative_main is None:
                try:
                    authoritative_main = authoritative_main_verifier()
                except Exception as exc:  # noqa: BLE001 - destructive proof must fail closed
                    authoritative_main = RemoteProof(
                        ok=False,
                        kind="main_lookup_failed",
                        error=f"authoritative managed main lookup failed: {exc}",
                    )
            proof = authoritative_main
            main_oid = _remote_head_oid(authoritative_main.detail) if proof.ok else None
            if proof.ok and main_oid is None:
                proof = RemoteProof(
                    ok=False,
                    kind="main_lookup_invalid",
                    detail={"authoritative_main": asdict(authoritative_main)},
                    error="authoritative managed main proof contained no commit OID",
                )
            elif proof.ok and main_oid is not None:
                item.main_oid = main_oid
                if item.branch:
                    try:
                        branch_proof = branch_verifier(item.branch)
                    except Exception as exc:  # noqa: BLE001 - destructive proof must fail closed
                        branch_proof = RemoteProof(
                            ok=False,
                            kind="branch_lookup_failed",
                            detail={"branch": item.branch},
                            error=f"authoritative managed branch lookup failed: {exc}",
                        )
                else:
                    branch_proof = RemoteProof(ok=True, kind="no_remote_branch")
                if branch_proof.ok:
                    proof = _runner_head_proof(
                        repo_dir=repo_dir,
                        item=item,
                        remote_proof=branch_proof,
                        main_oid=main_oid,
                        main_proof=authoritative_main,
                    )
                else:
                    proof = branch_proof
            item.remote_proof = asdict(proof)
            if not proof.ok:
                item.classification = "remote_unverified"
                item.reason = proof.error or "managed worktree history could not be verified"
            else:
                item.unique_commits = proof.kind == "local_unique_commits"
                item.classification = "terminal_candidate"
                item.reason = "managed worktree is inactive and its local state is preservable"

        if item.classification == "terminal_candidate":
            workspace_artifacts = path / "apps" / "crawler" / ".workspace"
            must_archive = (
                item.dirty_entries > 0
                or item.unique_commits
                or _path_exists_no_follow(workspace_artifacts)
            )
            item.planned_action = "archive_remove" if must_archive else "remove"
            if apply:
                _record_event(ledger, item, action="removal_started", observed_at=now)
                was_archived = False
                try:
                    if resolved is None:
                        raise RuntimeError("worktree path was not safely resolved")

                    def managed_activity_check(
                        current_resolved: Path,
                        expected_item: WorktreeItem = item,
                        expected_context: dict[str, Any] = context,
                        original_resolved: Path = resolved,
                    ) -> None:
                        _managed_activity_check(
                            ledger=ledger,
                            item=expected_item,
                            expected_context=expected_context,
                            expected_resolved=original_resolved,
                            additional_resolved=(
                                current_resolved if current_resolved != original_resolved else None
                            ),
                            pid_checker=pid_checker,
                            live_path_checker=live_path_checker,
                        )

                    was_archived = _guarded_retire_worktree(
                        repo_dir=repo_dir,
                        worktrees_dir=worktrees_dir,
                        archive_dir=archive_dir,
                        path=path,
                        expected_resolved=resolved,
                        item=item,
                        activity_checker=managed_activity_check,
                        archive_id=_managed_archive_id(item),
                        archive_required=item.unique_commits,
                        max_archive_bytes=max_terminal_bytes,
                        remover=remover,
                        removal_lease=lambda run_id=item.run_id, worktree_path=path: (
                            ledger.worktree_removal_lease(
                                run_id=run_id,
                                worktree_path=worktree_path,
                            )
                        ),
                    )
                    if was_archived:
                        archived += 1
                    if path.exists():
                        raise RuntimeError(
                            "worktree removal returned but the directory still exists"
                        )
                    item.classification = "removed"
                    item.reason = "managed worktree state was verified or durably archived"
                    item.reclaimed_bytes = size
                    removed += 1
                    reclaimed += size
                    _record_event(
                        ledger,
                        item,
                        action="removed",
                        observed_at=int(time.time()),
                    )
                except _WorktreeBecameActive as exc:
                    item.classification = "active"
                    item.reason = str(exc)
                    item.error = None
                    item.pid_live = True
                    item.planned_action = "retain"
                    _record_event(
                        ledger,
                        item,
                        action="retained",
                        observed_at=int(time.time()),
                    )
                except Exception as exc:  # noqa: BLE001 - removal must fail closed
                    item.classification = "removal_failed"
                    item.reason = "managed worktree cleanup failed"
                    item.error = str(exc)
                    item.planned_action = "retain"
                    failures += 1
                    _record_event(
                        ledger,
                        item,
                        action="removal_failed",
                        observed_at=int(time.time()),
                    )
                finally:
                    if item.archive_path is not None and not was_archived:
                        archived += 1
        elif apply:
            _record_event(ledger, item, action="retained", observed_at=now)
        items.append(item)

    remaining = [item for item in items if item.classification not in {"removed", "active"}]
    retained_worktree_bytes = sum(item.bytes for item in remaining)
    quarantine_bytes = _directory_bytes(archive_dir)
    remaining_bytes = retained_worktree_bytes + quarantine_bytes
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
        remaining_terminal_directories=len(remaining),
        remaining_terminal_bytes=remaining_bytes,
        max_terminal_directories=max_terminal_directories,
        max_terminal_bytes=max_terminal_bytes,
        within_bounds=(
            len(remaining) <= max_terminal_directories and remaining_bytes <= max_terminal_bytes
        ),
        items=items,
        retained_worktree_bytes=retained_worktree_bytes,
        quarantine_bytes=quarantine_bytes,
    )


def _empty_report(
    *,
    apply: bool,
    max_terminal_directories: int,
    max_terminal_bytes: int,
    quarantine_bytes: int = 0,
) -> WorktreeReport:
    within_bounds = quarantine_bytes <= max_terminal_bytes
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
        remaining_terminal_bytes=quarantine_bytes,
        max_terminal_directories=max_terminal_directories,
        max_terminal_bytes=max_terminal_bytes,
        within_bounds=within_bounds,
        items=[],
        retained_worktree_bytes=0,
        quarantine_bytes=quarantine_bytes,
    )


def _run_is_active(
    run: dict[str, Any],
    *,
    pid_checker: Callable[[int, str], bool],
) -> bool:
    if str(run.get("state") or "") in ACTIVE_STATES:
        return True
    pid = run.get("pid")
    run_id = run.get("run_id")
    return bool(isinstance(pid, int) and isinstance(run_id, str) and pid_checker(pid, run_id))


def _runner_activity_check(
    *,
    ledger: Ledger,
    expected_run: dict[str, Any],
    expected_resolved: Path,
    pid_checker: Callable[[int, str], bool],
) -> None:
    matches: list[dict[str, Any]] = []
    for run in ledger.worktree_runs():
        raw_path = run.get("worktree_path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        try:
            current_path = Path(raw_path).resolve()
        except OSError as exc:
            raise RuntimeError(f"could not revalidate runner ledger path: {exc}") from exc
        if current_path == expected_resolved:
            matches.append(run)

    if any(_run_is_active(run, pid_checker=pid_checker) for run in matches):
        raise _WorktreeBecameActive("runner ledger or live process became active")
    expected_run_id = expected_run.get("run_id")
    if not matches or matches[-1].get("run_id") != expected_run_id:
        raise RuntimeError("runner ledger association changed during reconciliation")
    current_run = matches[-1]
    stable_fields = (
        "run_id",
        "issue",
        "state",
        "pid",
        "worktree_path",
        "pr_number",
        "branch",
        "updated_at",
        "heartbeat_at",
    )
    if any(current_run.get(field) != expected_run.get(field) for field in stable_fields):
        raise RuntimeError("runner ledger changed during reconciliation")


def _managed_activity_check(
    *,
    ledger: Ledger,
    item: WorktreeItem,
    expected_context: dict[str, Any],
    expected_resolved: Path,
    additional_resolved: Path | None,
    pid_checker: Callable[[int, str], bool],
    live_path_checker: Callable[[Path], bool],
) -> None:
    runs = ledger.worktree_runs()
    if any(_run_is_active(run, pid_checker=pid_checker) for run in runs):
        raise _WorktreeBecameActive("runner activity now protects managed worktrees")
    if live_path_checker(expected_resolved):
        raise _WorktreeBecameActive("managed worktree now has a live workspace reference")
    if additional_resolved is not None and live_path_checker(additional_resolved):
        raise _WorktreeBecameActive("claimed worktree now has a live workspace reference")

    if item.run_id is None:
        return
    current = [run for run in runs if run.get("run_id") == item.run_id]
    if len(current) != 1:
        raise RuntimeError("managed worktree ledger context changed during reconciliation")
    stable_fields = (
        "run_id",
        "issue",
        "state",
        "pid",
        "pr_number",
        "branch",
        "updated_at",
        "heartbeat_at",
    )
    if any(current[0].get(field) != expected_context.get(field) for field in stable_fields):
        raise RuntimeError("managed worktree ledger context changed during reconciliation")


def _runner_head_proof(
    *,
    repo_dir: Path,
    item: WorktreeItem,
    remote_proof: RemoteProof,
    main_oid: str,
    main_proof: RemoteProof,
) -> RemoteProof:
    """Require the local runner HEAD to be durable remotely or archiveable."""
    remote_oid = _remote_head_oid(remote_proof.detail)
    head_proof = _managed_head_proof(
        repo_dir=repo_dir,
        item=item,
        exact_remote_oid=remote_oid,
        main_oid=main_oid,
    )
    detail = dict(head_proof.detail)
    detail["remote_verification"] = asdict(remote_proof)
    detail["authoritative_main"] = asdict(main_proof)
    return RemoteProof(
        ok=head_proof.ok,
        kind=head_proof.kind,
        detail=detail,
        error=head_proof.error,
    )


def _remote_head_oid(detail: dict[str, Any]) -> str | None:
    current: Any = detail
    for _ in range(3):
        if not isinstance(current, dict):
            return None
        oid = current.get("headRefOid")
        if isinstance(oid, str) and oid:
            return oid
        current = current.get("remote")
    return None


def _managed_head_proof(
    *,
    repo_dir: Path,
    item: WorktreeItem,
    main_oid: str,
    exact_remote_oid: str | None = None,
) -> RemoteProof:
    head = item.head_oid
    if not head:
        return RemoteProof(
            ok=False,
            kind="missing_head",
            error="registered managed worktree has no HEAD object",
        )
    detail = {"head_oid": head, "main_oid": main_oid, "branch": item.branch}

    ancestor = subprocess.run(
        ["git", "--no-replace-objects", "merge-base", "--is-ancestor", head, main_oid],
        cwd=repo_dir,
        env=_git_proof_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    if ancestor.returncode == 0:
        return RemoteProof(ok=True, kind="head_ancestor_main", detail=detail)
    if ancestor.returncode not in {0, 1}:
        return RemoteProof(
            ok=False,
            kind="ancestor_check_failed",
            detail=detail,
            error=(ancestor.stderr or "could not compare managed HEAD to main").strip(),
        )

    detail["remote_oid"] = exact_remote_oid
    if exact_remote_oid == head:
        return RemoteProof(ok=True, kind="exact_remote_branch", detail=detail)

    tree_comparison = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--quiet",
            "--exit-code",
            head,
            main_oid,
            "--",
        ],
        cwd=repo_dir,
        env=_git_proof_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    if tree_comparison.returncode == 0:
        return RemoteProof(ok=True, kind="tree_equal_main", detail=detail)
    if tree_comparison.returncode != 1:
        return RemoteProof(
            ok=False,
            kind="tree_comparison_failed",
            detail=detail,
            error=(tree_comparison.stderr or "could not compare managed tree to main").strip(),
        )
    return RemoteProof(ok=True, kind="local_unique_commits", detail=detail)


def _managed_archive_id(item: WorktreeItem) -> str:
    head = (item.head_oid or "no-head")[:12]
    path_hash = hashlib.sha256(item.path.encode()).hexdigest()[:12]
    return f"managed-{item.name}-{head}-{path_hash}"


def _classify(
    item: WorktreeItem,
    *,
    run: dict[str, Any] | None,
    path_error: str | None,
    status_error: str | None,
) -> None:
    if path_error:
        item.classification = "unsafe_path"
        item.reason = path_error
        return
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
            registered[current] = {"locked": False, "branch": None, "head": None}
        elif line.startswith("HEAD ") and current:
            registered[current]["head"] = line.removeprefix("HEAD ")
        elif line.startswith("branch refs/heads/") and current:
            registered[current]["branch"] = line.removeprefix("branch refs/heads/")
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


def _worktree_state_snapshot(
    worktree: Path,
    *,
    expected_head: str | None,
) -> _WorktreeStateSnapshot:
    head = subprocess.run(
        ["git", "--no-replace-objects", "rev-parse", "--verify", "HEAD^{commit}"],
        cwd=worktree,
        env=_git_proof_env(),
        text=True,
        capture_output=True,
        check=False,
    )
    if head.returncode != 0:
        raise RuntimeError((head.stderr or head.stdout or "could not read worktree HEAD").strip())
    head_oid = head.stdout.strip()
    if expected_head is not None and head_oid != expected_head:
        raise RuntimeError("worktree HEAD changed while its state was fingerprinted")

    status = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        cwd=worktree,
        env=_git_proof_env(),
        capture_output=True,
        check=False,
    )
    if status.returncode != 0:
        error = (status.stderr or b"git status failed").decode(errors="replace").strip()
        raise RuntimeError(error)

    candidates, workspace_root_metadata = _archive_candidates(worktree)
    candidate_fingerprints = tuple(
        _candidate_fingerprint_no_follow(
            worktree=worktree,
            source=source,
            archive_name=archive_name,
        )
        for source, archive_name in sorted(candidates.items(), key=lambda pair: pair[1])
    )
    return _WorktreeStateSnapshot(
        head_oid=head_oid,
        dirty_entries=len([entry for entry in status.stdout.split(b"\0") if entry]),
        status_sha256=hashlib.sha256(status.stdout).hexdigest(),
        tracked_patch_sha256=_git_diff_sha256(worktree),
        workspace_root_json=json.dumps(
            workspace_root_metadata,
            sort_keys=True,
            separators=(",", ":"),
        ),
        candidates=candidate_fingerprints,
    )


def _git_diff_sha256(worktree: Path) -> str:
    """Hash the binary tracked-state patch without buffering it in memory."""
    digest = hashlib.sha256()
    with tempfile.TemporaryFile(mode="w+b") as stderr_snapshot:
        process = subprocess.Popen(
            [
                "git",
                "--no-replace-objects",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "HEAD",
            ],
            cwd=worktree,
            env=_git_proof_env(),
            stdout=subprocess.PIPE,
            stderr=stderr_snapshot,
        )
        if process.stdout is None:
            process.kill()
            process.wait()
            raise RuntimeError("git diff did not expose a readable output stream")
        try:
            while True:
                chunk = process.stdout.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            returncode = process.wait()
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait()
            raise
        finally:
            process.stdout.close()
        if returncode != 0:
            stderr_snapshot.seek(0)
            error = stderr_snapshot.read(64 * 1024).decode(errors="replace").strip()
            raise RuntimeError(error or "git diff --binary failed")
    return digest.hexdigest()


def _directory_bytes(root: Path) -> int:
    try:
        root_stat = root.lstat()
    except FileNotFoundError:
        return 0
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        return root_stat.st_size
    root_fd = open_absolute_directory_no_follow(root)
    try:
        opened = os.fstat(root_fd)
        if not _same_inode(opened, root_stat) or not stat.S_ISDIR(opened.st_mode):
            raise RuntimeError("directory changed while opening for size accounting")
        return _directory_bytes_at(root_fd, opened)
    finally:
        os.close(root_fd)


def _regular_file_bytes_no_follow(path: Path) -> int:
    try:
        parent_fd = open_absolute_directory_no_follow(path.parent)
    except RuntimeError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return 0
        raise
    file_fd: int | None = None
    try:
        try:
            expected = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return 0
        if not stat.S_ISREG(expected.st_mode):
            raise RuntimeError("worktree quarantine destination is not a regular file")
        file_fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        opened = os.fstat(file_fd)
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_inode(opened, expected) or not _same_inode(current, expected):
            raise RuntimeError("worktree quarantine destination changed during accounting")
        return int(opened.st_size)
    except OSError as exc:
        raise RuntimeError(f"could not safely account quarantine destination: {exc}") from exc
    finally:
        if file_fd is not None:
            os.close(file_fd)
        os.close(parent_fd)


def _archive_staging_pid(name: str) -> int | None:
    try:
        prefix, raw_pid, suffix = name.rsplit(".", 2)
    except ValueError:
        return None
    if not raw_pid.isdigit():
        try:
            prefix, raw_pid, token, suffix = name.rsplit(".", 3)
        except ValueError:
            return None
        if re.fullmatch(r"[0-9a-f]{24}", token) is None:
            return None
    if not prefix.startswith(".") or suffix not in {"tmp", "bundle"} or not raw_pid.isdigit():
        return None
    pid = int(raw_pid)
    return pid if pid > 0 else None


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _prune_stale_archive_staging(archive_dir: Path) -> int:
    """Remove only owned staging files whose creating process no longer exists."""
    try:
        archive_fd = open_absolute_directory_no_follow(archive_dir)
    except RuntimeError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return 0
        raise
    reclaimed = 0
    try:
        for name in os.listdir(archive_fd):
            pid = _archive_staging_pid(name)
            if pid is None or pid == os.getpid() or _pid_is_alive(pid):
                continue
            validate_child_name(name)
            try:
                expected = os.stat(name, dir_fd=archive_fd, follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError(f"archive staging changed before cleanup: {exc}") from exc
            if not stat.S_ISREG(expected.st_mode):
                raise RuntimeError(f"archive staging entry is not a regular file: {name}")
            unlink_child_at(archive_fd, name, expected=expected)
            reclaimed += int(expected.st_size)
    finally:
        os.close(archive_fd)
    return reclaimed


def _directory_bytes_at(directory_fd: int, directory_stat: os.stat_result) -> int:
    total = directory_stat.st_size
    try:
        names = os.listdir(directory_fd)
    except OSError as exc:
        raise RuntimeError(f"could not safely enumerate directory size: {exc}") from exc
    for name in names:
        validate_child_name(name)
        try:
            entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            total += entry_stat.st_size
            if not stat.S_ISDIR(entry_stat.st_mode):
                continue
            child_fd = os.open(name, directory_open_flags(), dir_fd=directory_fd)
        except OSError as exc:
            raise RuntimeError(f"directory changed during size accounting: {exc}") from exc
        try:
            opened = os.fstat(child_fd)
            if not _same_inode(opened, entry_stat) or not stat.S_ISDIR(opened.st_mode):
                raise RuntimeError("directory changed while opening for size accounting")
            total += _directory_bytes_at(child_fd, opened) - entry_stat.st_size
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not _same_inode(current, entry_stat) or not stat.S_ISDIR(current.st_mode):
                raise RuntimeError("directory changed during size accounting")
        finally:
            os.close(child_fd)
    return total


def _repair_registered_worktree(repo_dir: Path, path: Path) -> None:
    repair = subprocess.run(
        ["git", "worktree", "repair", str(path)],
        cwd=repo_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if repair.returncode != 0:
        raise RuntimeError((repair.stderr or repair.stdout or "git worktree repair failed").strip())


def _claim_registered_worktree(
    *,
    repo_dir: Path,
    worktrees_dir: Path,
    path: Path,
    expected_resolved: Path,
    expected_head: str | None,
) -> Path:
    """Atomically hide a removal target behind an unpredictable same-root claim."""
    _validate_removal_target(
        repo_dir=repo_dir,
        worktrees_dir=worktrees_dir,
        path=path,
        expected_resolved=expected_resolved,
        expected_head=expected_head,
    )
    expected_stat = path.lstat()
    path_hash = hashlib.sha256(os.fsencode(path.name)).hexdigest()[:16]
    claim_name = f".jobseek-remove-{path_hash}-{secrets.token_hex(16)}"
    validate_child_name(claim_name)
    claimed_path = path.parent / claim_name
    if _path_exists_no_follow(claimed_path):
        raise RuntimeError("unpredictable worktree removal claim already exists")
    os.rename(path, claimed_path)
    claimed_stat = claimed_path.lstat()
    if not _same_inode(expected_stat, claimed_stat) or not stat.S_ISDIR(claimed_stat.st_mode):
        raise RuntimeError("worktree changed while its removal claim was created")
    try:
        _repair_registered_worktree(repo_dir, claimed_path)
    except BaseException:
        if not _path_exists_no_follow(path):
            os.rename(claimed_path, path)
            _repair_registered_worktree(repo_dir, path)
        raise
    return claimed_path


def _restore_claimed_worktree(
    *,
    repo_dir: Path,
    claimed_path: Path,
    original_path: Path,
) -> None:
    """Restore a failed claim only when the original name is still unoccupied."""
    if _path_exists_no_follow(original_path):
        raise RuntimeError(
            f"original worktree path was recreated; retained claimed worktree at {claimed_path}"
        )
    os.rename(claimed_path, original_path)
    _repair_registered_worktree(repo_dir, original_path)


def _remove_registered_worktree(
    repo_dir: Path,
    path: Path,
    *,
    final_guard: _RemovalGuard,
) -> None:
    """Remove after the caller exclusively fences every supported runner writer."""
    final_guard()
    result = subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=repo_dir,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "git worktree remove failed").strip())


def _publish_archive(
    temporary: Path,
    destination: Path,
    *,
    final_guard: _RemovalGuard,
) -> None:
    """Publish immutably, then retract the new link if source proof changed."""
    temporary_stat = temporary.lstat()
    try:
        os.link(temporary, destination, follow_symlinks=False)
    except FileExistsError as exc:
        raise RuntimeError(f"immutable worktree archive already exists: {destination}") from exc
    try:
        published_stat = destination.lstat()
        if not _same_inode(temporary_stat, published_stat):
            raise RuntimeError("published worktree archive does not match its staging inode")
        final_guard()
    except BaseException:
        archive_fd = open_absolute_directory_no_follow(destination.parent)
        try:
            current = os.stat(destination.name, dir_fd=archive_fd, follow_symlinks=False)
            if not _same_inode(current, temporary_stat):
                raise RuntimeError("published worktree archive changed before retraction")
            unlink_child_at(archive_fd, destination.name, expected=current)
        finally:
            os.close(archive_fd)
        raise


def _worktree_snapshot_sha256(snapshot: _WorktreeStateSnapshot) -> str:
    payload = json.dumps(
        asdict(snapshot),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _archive_candidates(worktree: Path) -> tuple[dict[Path, str], dict[str, Any] | None]:
    untracked = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-c",
            "core.fsmonitor=false",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=worktree,
        env=_git_proof_env(),
        capture_output=True,
        check=False,
    )
    if untracked.returncode != 0:
        error = (untracked.stderr or b"git ls-files failed").decode(errors="replace").strip()
        raise RuntimeError(error)
    candidates: dict[Path, str] = {}
    for raw in untracked.stdout.split(b"\0"):
        if raw:
            relative = Path(os.fsdecode(raw))
            candidates[worktree / relative] = f"untracked/{relative.as_posix()}"
    workspace_root = worktree / "apps" / "crawler" / ".workspace"
    workspace_candidates, workspace_root_metadata = _workspace_archive_candidates(
        worktree=worktree,
        workspace_root=workspace_root,
    )
    candidates.update(workspace_candidates)
    return candidates, workspace_root_metadata


def _archive_worktree(
    worktree: Path,
    *,
    archive_dir: Path,
    run_id: str,
    item: WorktreeItem,
    include_unique_commits: bool = False,
    unique_commit_base_oid: str | None = None,
    max_archive_bytes: int,
    expected_snapshot: _WorktreeStateSnapshot,
    pre_publish_check: Callable[[], None],
) -> tuple[Path, str]:
    safe_run_id = "".join(char if char.isalnum() or char in "-_." else "_" for char in run_id)
    source_snapshot_sha256 = _worktree_snapshot_sha256(expected_snapshot)
    archive_token = secrets.token_hex(12)
    destination = (
        archive_dir / f"{safe_run_id}-{source_snapshot_sha256[:24]}-{archive_token}.tar.gz"
    )
    _prune_stale_archive_staging(archive_dir)
    # Clean tracked checkout content is never written to the evidence archive.
    # Budget only the fingerprinted candidates plus bounded metadata; the
    # streaming writer and final actual-size check still enforce the hard cap.
    candidate_bytes = sum(candidate.bytes or 0 for candidate in expected_snapshot.candidates)
    base_projected_bytes = max(
        1,
        candidate_bytes + ARCHIVE_METADATA_RESERVE_BYTES,
    )
    if include_unique_commits:
        if unique_commit_base_oid is None:
            raise RuntimeError("unique commit archive has no verified base OID")
        base_projected_bytes += _unique_commit_object_bytes(
            worktree,
            base_oid=unique_commit_base_oid,
        )
    current_archive_bytes = _directory_bytes(archive_dir)
    replaced_destination_bytes = _regular_file_bytes_no_follow(destination)
    retained_archive_bytes = max(0, current_archive_bytes - replaced_destination_bytes)
    if retained_archive_bytes + base_projected_bytes > max_archive_bytes:
        raise RuntimeError(
            "worktree quarantine capacity gate rejected archive "
            f"({retained_archive_bytes} retained + {base_projected_bytes} projected > "
            f"{max_archive_bytes} bytes)"
        )
    patch_budget = max_archive_bytes - retained_archive_bytes - base_projected_bytes
    with tempfile.TemporaryFile(mode="w+b") as patch_snapshot:
        patch_bytes = _stream_git_diff_snapshot(
            worktree,
            snapshot=patch_snapshot,
            max_bytes=patch_budget,
        )
        patch_snapshot.seek(0)
        patch_sha256 = hashlib.sha256()
        while True:
            chunk = patch_snapshot.read(1024 * 1024)
            if not chunk:
                break
            patch_sha256.update(chunk)
        if patch_sha256.hexdigest() != expected_snapshot.tracked_patch_sha256:
            raise RuntimeError("tracked worktree state changed while creating its archive")
        patch_snapshot.seek(0)
        projected_bytes = base_projected_bytes + patch_bytes
        if retained_archive_bytes + projected_bytes > max_archive_bytes:
            raise RuntimeError(
                "worktree quarantine capacity gate rejected archive "
                f"({retained_archive_bytes} retained + {projected_bytes} projected > "
                f"{max_archive_bytes} bytes)"
            )
        return _archive_worktree_from_patch_snapshot(
            worktree=worktree,
            archive_dir=archive_dir,
            safe_run_id=safe_run_id,
            destination=destination,
            item=item,
            include_unique_commits=include_unique_commits,
            unique_commit_base_oid=unique_commit_base_oid,
            max_archive_bytes=max_archive_bytes,
            retained_archive_bytes=retained_archive_bytes,
            patch_snapshot=patch_snapshot,
            patch_bytes=patch_bytes,
            expected_snapshot=expected_snapshot,
            pre_publish_check=pre_publish_check,
            source_snapshot_sha256=source_snapshot_sha256,
        )


def _stream_git_diff_snapshot(
    worktree: Path,
    *,
    snapshot: Any,
    max_bytes: int,
) -> int:
    """Stream a binary patch into an anonymous bounded file without buffering stdout."""
    total = 0
    overflow = False
    with tempfile.TemporaryFile(mode="w+b") as stderr_snapshot:
        process = subprocess.Popen(
            [
                "git",
                "--no-replace-objects",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--binary",
                "HEAD",
            ],
            cwd=worktree,
            env=_git_proof_env(),
            stdout=subprocess.PIPE,
            stderr=stderr_snapshot,
        )
        if process.stdout is None:
            process.kill()
            process.wait()
            raise RuntimeError("git diff did not expose a readable output stream")
        try:
            while True:
                chunk = process.stdout.read(1024 * 1024)
                if not chunk:
                    break
                if total + len(chunk) > max_bytes:
                    overflow = True
                    process.kill()
                    break
                snapshot.write(chunk)
                total += len(chunk)
            returncode = process.wait()
        except BaseException:
            if process.poll() is None:
                process.kill()
                process.wait()
            raise
        finally:
            process.stdout.close()
        if overflow:
            raise RuntimeError(
                "worktree quarantine capacity gate rejected binary patch "
                f"(more than {max_bytes} bytes available)"
            )
        if returncode != 0:
            stderr_snapshot.seek(0)
            error = stderr_snapshot.read(64 * 1024).decode(errors="replace").strip()
            raise RuntimeError(error or "git diff --binary failed")
    snapshot.flush()
    snapshot.seek(0)
    return total


def _archive_worktree_from_patch_snapshot(
    *,
    worktree: Path,
    archive_dir: Path,
    safe_run_id: str,
    destination: Path,
    item: WorktreeItem,
    include_unique_commits: bool,
    unique_commit_base_oid: str | None,
    max_archive_bytes: int,
    retained_archive_bytes: int,
    patch_snapshot: Any,
    patch_bytes: int,
    expected_snapshot: _WorktreeStateSnapshot,
    pre_publish_check: Callable[[], None],
    source_snapshot_sha256: str,
) -> tuple[Path, str]:

    archive_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(archive_dir, 0o700)
    staging_token = secrets.token_hex(12)
    temporary = archive_dir / f".{safe_run_id}.{os.getpid()}.{staging_token}.tmp"
    bundle_path = archive_dir / f".{safe_run_id}.{os.getpid()}.{staging_token}.bundle"

    candidates, workspace_root_metadata = _archive_candidates(worktree)

    inventory = []
    bundle_manifest: dict[str, Any] | None = None
    bundle_staging_bytes = 0
    try:
        if include_unique_commits:
            if unique_commit_base_oid is None:
                raise RuntimeError("unique commit archive has no verified base OID")
            bundle = subprocess.run(
                [
                    "git",
                    "--no-replace-objects",
                    "bundle",
                    "create",
                    str(bundle_path),
                    "HEAD",
                    f"^{unique_commit_base_oid}",
                ],
                cwd=worktree,
                env=_git_proof_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            if bundle.returncode != 0:
                raise RuntimeError(
                    (
                        bundle.stderr or bundle.stdout or "unique commit bundle creation failed"
                    ).strip()
                )
            verification = subprocess.run(
                ["git", "--no-replace-objects", "bundle", "verify", str(bundle_path)],
                cwd=worktree,
                env=_git_proof_env(),
                text=True,
                capture_output=True,
                check=False,
            )
            if verification.returncode != 0:
                raise RuntimeError(
                    (
                        verification.stderr
                        or verification.stdout
                        or "unique commit bundle verification failed"
                    ).strip()
                )
            os.chmod(bundle_path, 0o600)
            bundle_sha, bundle_bytes = _hash_file(bundle_path)
            bundle_staging_bytes = bundle_bytes
            bundle_manifest = {
                "archive_name": "unique-commits.bundle",
                "bytes": bundle_bytes,
                "sha256": bundle_sha,
                "head_oid": item.head_oid,
                "base_oid": unique_commit_base_oid,
            }
        staging_budget = max_archive_bytes - retained_archive_bytes - bundle_staging_bytes
        if staging_budget <= 0:
            raise RuntimeError(
                "worktree quarantine staging capacity gate rejected archive "
                f"({retained_archive_bytes} retained + {bundle_staging_bytes} bundle >= "
                f"{max_archive_bytes} bytes)"
            )
        with temporary.open("xb") as temporary_file:
            bounded_file = _BoundedArchiveWriter(temporary_file, max_bytes=staging_budget)
            with tarfile.open(fileobj=bounded_file, mode="w:gz") as archive:
                if patch_bytes:
                    _tar_add_snapshot(
                        archive,
                        "tracked.patch",
                        patch_snapshot,
                        patch_bytes,
                    )
                if bundle_manifest is not None:
                    archive.add(bundle_path, arcname="unique-commits.bundle", recursive=False)
                for source, archive_name in sorted(candidates.items(), key=lambda pair: pair[1]):
                    inventory.append(
                        _tar_add_candidate_no_follow(
                            archive,
                            worktree=worktree,
                            source=source,
                            archive_name=archive_name,
                        )
                    )
                archived_candidates = tuple(
                    _inventory_fingerprint(candidate) for candidate in inventory
                )
                if archived_candidates != expected_snapshot.candidates:
                    raise RuntimeError(
                        "untracked or workspace evidence changed while creating its archive"
                    )
                workspace_root_json = json.dumps(
                    workspace_root_metadata,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if workspace_root_json != expected_snapshot.workspace_root_json:
                    raise RuntimeError("workspace evidence root changed while creating its archive")
                manifest = {
                    "schema_version": 1,
                    "created_at": int(time.time()),
                    "run_id": item.run_id,
                    "issue": item.issue,
                    "state": item.state,
                    "worktree_path": item.path,
                    "source": item.source,
                    "worktree_bytes": item.bytes,
                    "dirty_entries": item.dirty_entries,
                    "head_oid": item.head_oid,
                    "unique_commits": item.unique_commits,
                    "unique_commit_bundle": bundle_manifest,
                    "remote_proof": item.remote_proof,
                    "tracked_patch_bytes": patch_bytes,
                    "source_snapshot_sha256": source_snapshot_sha256,
                    "workspace_root": workspace_root_metadata,
                    "files": inventory,
                }
                _tar_add_bytes(
                    archive,
                    "manifest.json",
                    json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n",
                )
            bounded_file.flush()
            os.fsync(temporary_file.fileno())
        os.chmod(temporary, 0o600)
        latest_archive_bytes = _directory_bytes(archive_dir)
        latest_destination_bytes = _regular_file_bytes_no_follow(destination)
        temporary_bytes = _regular_file_bytes_no_follow(temporary)
        latest_bundle_bytes = _regular_file_bytes_no_follow(bundle_path)
        retained_archive_bytes = max(
            0,
            latest_archive_bytes - latest_destination_bytes - temporary_bytes - latest_bundle_bytes,
        )
        if retained_archive_bytes + temporary_bytes > max_archive_bytes:
            raise RuntimeError(
                "worktree quarantine hard capacity gate rejected archive "
                f"({retained_archive_bytes} retained + {temporary_bytes} actual > "
                f"{max_archive_bytes} bytes)"
            )
        _publish_archive(
            temporary,
            destination,
            final_guard=pre_publish_check,
        )
    finally:
        temporary.unlink(missing_ok=True)
        bundle_path.unlink(missing_ok=True)
    digest, _ = _hash_file(destination)
    return destination, digest


def _inventory_fingerprint(candidate: dict[str, Any]) -> _ArchiveCandidateFingerprint:
    kind = str(candidate.get("type") or "")
    sha256 = candidate.get("sha256")
    if kind == "symlink":
        sha256 = candidate.get("target_sha256")
    return _ArchiveCandidateFingerprint(
        source=str(candidate.get("source") or ""),
        archive_name=str(candidate.get("archive_name") or ""),
        kind=kind,
        mode=int(candidate.get("mode") or 0),
        bytes=(int(candidate["bytes"]) if isinstance(candidate.get("bytes"), int) else None),
        sha256=str(sha256) if isinstance(sha256, str) else None,
    )


def _candidate_fingerprint_no_follow(
    *,
    worktree: Path,
    source: Path,
    archive_name: str,
) -> _ArchiveCandidateFingerprint:
    """Hash one archive candidate through a descriptor-anchored, no-follow read."""
    try:
        relative = source.relative_to(worktree)
    except ValueError as exc:
        raise RuntimeError(f"archive candidate escapes worktree: {source}") from exc

    parent_fd, final_name = _open_parent_directory_no_follow(worktree, relative)
    try:
        entry_stat = os.stat(final_name, dir_fd=parent_fd, follow_symlinks=False)
        mode = entry_stat.st_mode & 0o777
        if stat.S_ISLNK(entry_stat.st_mode):
            target = os.readlink(final_name, dir_fd=parent_fd)
            current = os.stat(final_name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not _same_inode(current, entry_stat)
                or current.st_mtime_ns != entry_stat.st_mtime_ns
            ):
                raise RuntimeError(f"archive candidate changed while reading: {relative}")
            return _ArchiveCandidateFingerprint(
                source=relative.as_posix(),
                archive_name=archive_name,
                kind="symlink",
                mode=mode,
                sha256=hashlib.sha256(os.fsencode(target)).hexdigest(),
            )
        if not stat.S_ISREG(entry_stat.st_mode):
            raise RuntimeError(f"archive candidate is not a regular file or symlink: {relative}")

        file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        file_fd = os.open(final_name, file_flags, dir_fd=parent_fd)
        try:
            opened_stat = os.fstat(file_fd)
            if not stat.S_ISREG(opened_stat.st_mode) or not _same_inode(opened_stat, entry_stat):
                raise RuntimeError(f"archive candidate changed while opening: {relative}")
            digest = hashlib.sha256()
            size = 0
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
            final_stat = os.fstat(file_fd)
            current = os.stat(final_name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not _same_inode(current, entry_stat)
                or final_stat.st_size != opened_stat.st_size
                or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
                or size != opened_stat.st_size
            ):
                raise RuntimeError(f"archive candidate changed while reading: {relative}")
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise RuntimeError(f"unsafe archive candidate {relative}: {exc}") from exc
    finally:
        os.close(parent_fd)

    return _ArchiveCandidateFingerprint(
        source=relative.as_posix(),
        archive_name=archive_name,
        kind="file",
        mode=mode,
        bytes=size,
        sha256=digest.hexdigest(),
    )


def _tar_add_candidate_no_follow(
    archive: tarfile.TarFile,
    *,
    worktree: Path,
    source: Path,
    archive_name: str,
) -> dict[str, Any]:
    """Archive one candidate through descriptor-anchored, no-follow opens."""
    try:
        relative = source.relative_to(worktree)
    except ValueError as exc:
        raise RuntimeError(f"archive candidate escapes worktree: {source}") from exc

    parent_fd, final_name = _open_parent_directory_no_follow(worktree, relative)
    try:
        entry_stat = os.stat(final_name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(entry_stat.st_mode):
            target = os.readlink(final_name, dir_fd=parent_fd)
            info = tarfile.TarInfo(archive_name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            info.mode = entry_stat.st_mode & 0o777
            info.mtime = int(entry_stat.st_mtime)
            archive.addfile(info)
            return {
                "archive_name": archive_name,
                "source": relative.as_posix(),
                "type": "symlink",
                "mode": entry_stat.st_mode & 0o777,
                "target_sha256": hashlib.sha256(os.fsencode(target)).hexdigest(),
            }
        if not stat.S_ISREG(entry_stat.st_mode):
            raise RuntimeError(f"archive candidate is not a regular file or symlink: {relative}")

        file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        file_fd = os.open(final_name, file_flags, dir_fd=parent_fd)
        try:
            opened_stat = os.fstat(file_fd)
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or opened_stat.st_dev != entry_stat.st_dev
                or opened_stat.st_ino != entry_stat.st_ino
            ):
                raise RuntimeError(f"archive candidate changed while opening: {relative}")
            digest = hashlib.sha256()
            size = 0
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as snapshot:
                while True:
                    chunk = os.read(file_fd, 1024 * 1024)
                    if not chunk:
                        break
                    snapshot.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                final_stat = os.fstat(file_fd)
                if (
                    final_stat.st_size != opened_stat.st_size
                    or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
                    or size != opened_stat.st_size
                ):
                    raise RuntimeError(f"archive candidate changed while reading: {relative}")
                snapshot.seek(0)
                info = tarfile.TarInfo(archive_name)
                info.size = size
                info.mode = opened_stat.st_mode & 0o777
                info.mtime = int(opened_stat.st_mtime)
                archive.addfile(info, snapshot)
        finally:
            os.close(file_fd)
    except OSError as exc:
        raise RuntimeError(f"unsafe archive candidate {relative}: {exc}") from exc
    finally:
        os.close(parent_fd)

    return {
        "archive_name": archive_name,
        "source": relative.as_posix(),
        "type": "file",
        "mode": opened_stat.st_mode & 0o777,
        "bytes": size,
        "sha256": digest.hexdigest(),
    }


def _open_parent_directory_no_follow(worktree: Path, relative: Path) -> tuple[int, str]:
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"invalid archive candidate path: {relative}")
    try:
        directory_fd = open_absolute_directory_no_follow(worktree)
    except RuntimeError as exc:
        raise RuntimeError(f"could not safely open worktree for archive: {exc}") from exc
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, directory_open_flags(), dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
    except OSError as exc:
        os.close(directory_fd)
        raise RuntimeError(f"unsafe archive parent for {relative}: {exc}") from exc
    return directory_fd, parts[-1]


def _path_exists_no_follow(path: Path) -> bool:
    try:
        path.lstat()
    except OSError:
        return False
    return True


def _workspace_archive_candidates(
    *,
    worktree: Path,
    workspace_root: Path,
) -> tuple[dict[Path, str], dict[str, Any] | None]:
    """Inventory workspace evidence without traversing directory symlinks."""
    try:
        worktree_stat = worktree.lstat()
        relative_root = workspace_root.relative_to(worktree)
    except (OSError, ValueError):
        return {}, None
    if stat.S_ISLNK(worktree_stat.st_mode) or not stat.S_ISDIR(worktree_stat.st_mode):
        return {}, {"source": str(relative_root), "type": "unsafe_container"}

    source_name = str(relative_root)
    try:
        directory_fd = open_absolute_directory_no_follow(worktree)
    except RuntimeError as exc:
        raise RuntimeError(f"could not safely open worktree evidence root: {exc}") from exc
    try:
        opened_worktree = os.fstat(directory_fd)
        if not _same_inode(opened_worktree, worktree_stat):
            raise RuntimeError("worktree changed while opening evidence root")
        for index, part in enumerate(relative_root.parts):
            try:
                entry_stat = os.stat(part, dir_fd=directory_fd, follow_symlinks=False)
            except FileNotFoundError:
                return {}, None
            if stat.S_ISLNK(entry_stat.st_mode):
                target = os.readlink(part, dir_fd=directory_fd)
                return {}, {
                    "source": source_name,
                    "type": (
                        "symlink"
                        if index == len(relative_root.parts) - 1
                        else "unsafe_parent_symlink"
                    ),
                    "symlink_component": str(Path(*relative_root.parts[: index + 1])),
                    "target_sha256": hashlib.sha256(target.encode()).hexdigest(),
                }
            if not stat.S_ISDIR(entry_stat.st_mode):
                return {}, {"source": source_name, "type": "non_directory"}
            try:
                child_fd = os.open(part, directory_open_flags(), dir_fd=directory_fd)
            except OSError as exc:
                raise RuntimeError(f"workspace evidence parent changed: {exc}") from exc
            opened = os.fstat(child_fd)
            if not _same_inode(opened, entry_stat) or not stat.S_ISDIR(opened.st_mode):
                os.close(child_fd)
                raise RuntimeError("workspace evidence parent changed while opening")
            os.close(directory_fd)
            directory_fd = child_fd

        candidates: dict[Path, str] = {}
        _collect_workspace_candidates_at(
            directory_fd,
            workspace_root=workspace_root,
            relative=Path(),
            candidates=candidates,
        )
        return candidates, {"source": source_name, "type": "directory"}
    finally:
        os.close(directory_fd)


def _collect_workspace_candidates_at(
    directory_fd: int,
    *,
    workspace_root: Path,
    relative: Path,
    candidates: dict[Path, str],
) -> None:
    try:
        names = sorted(os.listdir(directory_fd))
    except OSError as exc:
        raise RuntimeError(f"could not safely enumerate workspace evidence: {exc}") from exc
    for name in names:
        validate_child_name(name)
        entry_relative = relative / name
        try:
            entry_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise RuntimeError(f"workspace evidence changed during enumeration: {exc}") from exc
        source = workspace_root / entry_relative
        if stat.S_ISREG(entry_stat.st_mode) or stat.S_ISLNK(entry_stat.st_mode):
            candidates[source] = f"workspace/{entry_relative.as_posix()}"
            continue
        if not stat.S_ISDIR(entry_stat.st_mode):
            continue
        try:
            child_fd = os.open(name, directory_open_flags(), dir_fd=directory_fd)
        except OSError as exc:
            raise RuntimeError(f"workspace evidence directory changed: {exc}") from exc
        try:
            opened = os.fstat(child_fd)
            if not _same_inode(opened, entry_stat) or not stat.S_ISDIR(opened.st_mode):
                raise RuntimeError("workspace evidence directory changed while opening")
            _collect_workspace_candidates_at(
                child_fd,
                workspace_root=workspace_root,
                relative=entry_relative,
                candidates=candidates,
            )
            current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if not _same_inode(current, entry_stat) or not stat.S_ISDIR(current.st_mode):
                raise RuntimeError("workspace evidence directory changed during enumeration")
        finally:
            os.close(child_fd)


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _unique_commit_object_bytes(worktree: Path, *, base_oid: str) -> int:
    objects = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "rev-list",
            "--objects",
            "HEAD",
            f"^{base_oid}",
        ],
        cwd=worktree,
        env=_git_proof_env(),
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    object_ids = [line.split(" ", 1)[0] for line in objects.splitlines() if line]
    if not object_ids:
        return 0
    sizes = subprocess.run(
        ["git", "--no-replace-objects", "cat-file", "--batch-check=%(objectsize)"],
        cwd=worktree,
        env=_git_proof_env(),
        input="\n".join(object_ids) + "\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    try:
        return sum(int(line) for line in sizes.splitlines())
    except ValueError as exc:
        raise RuntimeError("could not estimate unique commit archive size") from exc


def _tar_add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o600
    info.mtime = int(time.time())
    archive.addfile(info, io.BytesIO(data))


def _tar_add_snapshot(
    archive: tarfile.TarFile,
    name: str,
    snapshot: Any,
    size: int,
) -> None:
    snapshot.seek(0)
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o600
    info.mtime = int(time.time())
    archive.addfile(info, snapshot)


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
        source=item.source,
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
