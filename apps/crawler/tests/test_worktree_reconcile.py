from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.workspace import worktree_reconcile as reconcile_module
from src.workspace.codex_runner import RunnerLedger
from src.workspace.worktree_reconcile import (
    GitHubRemoteVerifier,
    RemoteProof,
    combine_worktree_reports,
    prune_redundant_workspace_archives,
    reconcile_managed_worktrees,
    reconcile_worktrees,
)


def _run(*command: str, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def _repo_with_worktree(tmp_path: Path, name: str = "run-worktree") -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _run("git", "init", "-b", "main", cwd=repo)
    _run("git", "config", "user.name", "Test Runner", cwd=repo)
    _run("git", "config", "user.email", "runner@example.test", cwd=repo)
    (repo / "tracked.txt").write_text("base\n")
    _run("git", "add", "tracked.txt", cwd=repo)
    _run("git", "commit", "-m", "base", cwd=repo)
    _run("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    worktrees = tmp_path / "runner" / "worktrees"
    worktrees.mkdir(parents=True)
    worktree = worktrees / name
    _run("git", "worktree", "add", "--detach", str(worktree), "HEAD", cwd=repo)
    return repo, worktree


def _terminal_run(
    ledger: RunnerLedger,
    worktree: Path,
    *,
    run_id: str = "issue-101-1-aaaaaaaa",
    state: str = "failed",
    issue: int = 101,
    pr_number: int | None = 7,
    branch: str | None = "add-company/acme",
) -> None:
    assert ledger.acquire(run_id=run_id, issue=issue, active_slot="company-resolver")
    ledger.update(
        run_id,
        worktree_path=str(worktree),
        pr_number=pr_number,
        branch=branch,
    )
    ledger.finish(run_id, state, error="terminal test run")


def _record_verified_trace(ledger: RunnerLedger, run_id: str) -> None:
    from src.workspace.trace_backfill import record_verified_export

    record_verified_export(
        ledger_path=ledger.path,
        run_id=run_id,
        remote_dir=f"training-bundles/v2/gold/{run_id}",
        manifest={
            "schema_version": "jobseek-codex-training-bundle/v2",
            "quality": {"tier": "gold"},
            "bundle_content_sha256": f"verified-{run_id}",
            "thread_count": 1,
            "subagent_count": 0,
            "files": [],
        },
        verified={},
    )


def _prune_archives(
    tmp_path: Path,
    repo: Path,
    ledger: RunnerLedger,
    *,
    apply: bool,
):
    main_oid = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return prune_redundant_workspace_archives(
        repo_dir=repo,
        archive_dir=tmp_path / "runner" / "state" / "worktree-quarantine",
        ledger=ledger,
        remote_verifier=lambda _run: RemoteProof(
            ok=True,
            kind="test_remote",
            detail={"headRefOid": main_oid},
        ),
        authoritative_main_verifier=lambda: RemoteProof(
            ok=True,
            kind="test_main",
            detail={"headRefOid": main_oid},
        ),
        apply=apply,
    )


def _create_workspace_archive(
    tmp_path: Path,
    *,
    state: str = "submitted",
    dirty: bool = False,
    unique_commit: bool = False,
) -> tuple[Path, Path, RunnerLedger, str]:
    repo, worktree = _repo_with_worktree(tmp_path)
    (repo / ".git" / "info" / "exclude").write_text("apps/crawler/.workspace/\n")
    workspace = worktree / "apps" / "crawler" / ".workspace" / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n")
    if dirty:
        (worktree / "untracked-evidence.txt").write_text("preserve me\n")
    if unique_commit:
        (worktree / "tracked.txt").write_text("local-only commit\n")
        _run("git", "add", "tracked.txt", cwd=worktree)
        _run("git", "commit", "-m", "local only", cwd=worktree)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    run_id = "issue-101-1-aaaaaaaa"
    _terminal_run(ledger, worktree, run_id=run_id, state=state)
    reconciled = _reconcile(tmp_path, repo, ledger, apply=True)
    assert reconciled.archived == 1
    archive_path = Path(str(reconciled.items[0].archive_path))
    assert archive_path.is_file()
    return repo, archive_path, ledger, run_id


def _reconcile(
    tmp_path: Path,
    repo: Path,
    ledger: RunnerLedger,
    *,
    apply: bool,
    verifier=None,
    authoritative_main_verifier=None,
    remove_worktree=None,
    pre_remove=None,
    pid_checker=None,
    max_directories: int = 3,
    max_bytes: int = 10 * 1024**3,
):
    def local_main_verifier() -> RemoteProof:
        result = subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=repo,
            check=False,
            capture_output=True,
            text=True,
        )
        return RemoteProof(
            ok=result.returncode == 0,
            kind="test_main" if result.returncode == 0 else "main_lookup_failed",
            detail={"headRefOid": result.stdout.strip()} if result.returncode == 0 else {},
            error=result.stderr.strip() or None,
        )

    return reconcile_worktrees(
        root=tmp_path / "runner",
        repo_dir=repo,
        worktrees_dir=tmp_path / "runner" / "worktrees",
        archive_dir=tmp_path / "runner" / "state" / "worktree-quarantine",
        ledger=ledger,
        remote_verifier=verifier or (lambda run: RemoteProof(ok=True, kind="test")),
        authoritative_main_verifier=authoritative_main_verifier or local_main_verifier,
        pid_checker=pid_checker or (lambda pid, run_id: False),
        max_terminal_directories=max_directories,
        max_terminal_bytes=max_bytes,
        apply=apply,
        pre_remove=pre_remove,
        remove_worktree=remove_worktree,
    )


def _managed_repo_with_worktree(
    tmp_path: Path,
    name: str = "managed-worktree",
) -> tuple[Path, Path, Path]:
    seed = tmp_path / "seed"
    seed.mkdir(parents=True)
    _run("git", "init", "-b", "main", cwd=seed)
    _run("git", "config", "user.name", "Test Runner", cwd=seed)
    _run("git", "config", "user.email", "runner@example.test", cwd=seed)
    (seed / "tracked.txt").write_text("base\n")
    _run("git", "add", "tracked.txt", cwd=seed)
    _run("git", "commit", "-m", "base", cwd=seed)

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(seed), str(origin)],
        check=True,
        capture_output=True,
        text=True,
    )
    managed = tmp_path / "home" / ".jobseek" / "repo"
    managed.parent.mkdir(parents=True)
    subprocess.run(
        ["git", "clone", str(origin), str(managed)],
        check=True,
        capture_output=True,
        text=True,
    )
    _run("git", "config", "user.name", "Test Runner", cwd=managed)
    _run("git", "config", "user.email", "runner@example.test", cwd=managed)
    worktrees = managed.parent / "worktrees"
    worktrees.mkdir()
    worktree = worktrees / name
    _run(
        "git",
        "worktree",
        "add",
        "-b",
        f"fix-crawler/{name}",
        str(worktree),
        "origin/main",
        cwd=managed,
    )
    return managed, worktrees, worktree


def _reconcile_managed(
    tmp_path: Path,
    managed: Path,
    worktrees: Path,
    ledger: RunnerLedger,
    *,
    apply: bool,
    live_path_checker=None,
    authoritative_main_verifier=None,
    branch_verifier=None,
    remove_worktree=None,
    max_directories: int = 3,
    max_bytes: int = 10 * 1024**3,
):
    def local_main_verifier() -> RemoteProof:
        result = subprocess.run(
            ["git", "rev-parse", "refs/remotes/origin/main"],
            cwd=managed,
            check=False,
            capture_output=True,
            text=True,
        )
        return RemoteProof(
            ok=result.returncode == 0,
            kind="test_main" if result.returncode == 0 else "main_lookup_failed",
            detail={"headRefOid": result.stdout.strip()} if result.returncode == 0 else {},
            error=result.stderr.strip() or None,
        )

    def local_branch_verifier(branch: str) -> RemoteProof:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", "origin", f"refs/heads/{branch}"],
            cwd=managed,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 2:
            return RemoteProof(
                ok=True,
                kind="remote_branch_absent",
                detail={"branch": branch, "headRefOid": None},
            )
        oid = result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else None
        return RemoteProof(
            ok=result.returncode == 0 and oid is not None,
            kind="remote_branch" if oid is not None else "branch_lookup_failed",
            detail={"branch": branch, "headRefOid": oid},
            error=result.stderr.strip() or None,
        )

    return reconcile_managed_worktrees(
        repo_dir=managed,
        worktrees_dir=worktrees,
        archive_dir=tmp_path / "runner" / "state" / "worktree-quarantine",
        ledger=ledger,
        authoritative_main_verifier=(authoritative_main_verifier or local_main_verifier),
        branch_verifier=branch_verifier or local_branch_verifier,
        pid_checker=lambda pid, run_id: False,
        live_path_checker=live_path_checker or (lambda path: False),
        max_terminal_directories=max_directories,
        max_terminal_bytes=max_bytes,
        apply=apply,
        remove_worktree=remove_worktree,
    )


def test_active_worktree_is_never_removed(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    assert ledger.acquire(run_id="active", issue=101, active_slot="company-resolver")
    ledger.update("active", worktree_path=str(worktree), pid=123)

    report = _reconcile(tmp_path, repo, ledger, apply=True)

    assert worktree.exists()
    assert report.active == 1
    assert report.removed == 0
    assert report.items[0].classification == "active"


def test_clean_terminal_worktree_has_exact_dry_run_then_durable_removal(
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)

    plan = _reconcile(tmp_path, repo, ledger, apply=False)

    assert plan.items[0].planned_action == "remove"
    assert plan.items[0].dirty_entries == 0
    assert worktree.exists()
    assert ledger.worktree_reconciliation_events() == []

    applied = _reconcile(tmp_path, repo, ledger, apply=True)

    assert not worktree.exists()
    assert applied.removed == 1
    assert applied.reclaimed_bytes > 0
    events = ledger.worktree_reconciliation_events()
    assert [event["action"] for event in events] == ["removal_started", "removed"]
    assert events[-1]["reclaimed_bytes"] == applied.reclaimed_bytes
    assert events[-1]["remote_proof_json"]


def test_verified_resolved_workspace_evidence_is_discarded_without_archive(
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    (repo / ".git" / "info" / "exclude").write_text("apps/crawler/.workspace/\n")
    workspace = worktree / "apps" / "crawler" / ".workspace" / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n")
    (workspace / "evidence.json").write_text('{"resolved": true}\n')
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    run_id = "issue-101-1-aaaaaaaa"
    _terminal_run(ledger, worktree, run_id=run_id, state="submitted")
    _record_verified_trace(ledger, run_id)

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        pre_remove=lambda _item: shutil.rmtree(workspace.parent),
    )

    assert report.removed == 1
    assert report.archived == 0
    assert report.quarantine_bytes == 0
    assert not worktree.exists()
    proof = json.loads(ledger.worktree_reconciliation_events()[-1]["remote_proof_json"])
    assert proof["trace_export_verified"] is True


def test_trace_attempt_status_cannot_authorize_workspace_discard(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    (repo / ".git" / "info" / "exclude").write_text("apps/crawler/.workspace/\n")
    workspace = worktree / "apps" / "crawler" / ".workspace" / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n")
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    run_id = "issue-101-1-aaaaaaaa"
    _terminal_run(ledger, worktree, run_id=run_id, state="submitted")
    ledger.record_trace_bundle_attempt(run_id, status="cleaned")

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        pre_remove=lambda _item: shutil.rmtree(workspace.parent),
    )

    assert report.removed == 1
    assert report.archived == 1
    assert report.quarantine_bytes > 0
    proof = json.loads(ledger.worktree_reconciliation_events()[-1]["remote_proof_json"])
    assert proof["trace_export_verified"] is False


def test_verified_retryable_workspace_evidence_is_still_archived(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    (repo / ".git" / "info" / "exclude").write_text("apps/crawler/.workspace/\n")
    workspace = worktree / "apps" / "crawler" / ".workspace" / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n")
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    run_id = "issue-101-1-aaaaaaaa"
    _terminal_run(ledger, worktree, run_id=run_id, state="retryable")
    _record_verified_trace(ledger, run_id)

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        pre_remove=lambda _item: shutil.rmtree(workspace.parent),
    )

    assert report.removed == 1
    assert report.archived == 1
    assert report.quarantine_bytes > 0


def test_verified_workspace_only_archive_is_pruned_with_durable_events(
    tmp_path: Path,
) -> None:
    repo, archive_path, ledger, run_id = _create_workspace_archive(tmp_path)
    archive_bytes = archive_path.stat().st_size
    _record_verified_trace(ledger, run_id)

    plan = _prune_archives(tmp_path, repo, ledger, apply=False)

    assert plan.eligible == 1
    assert plan.pruned == 0
    assert archive_path.exists()

    applied = _prune_archives(tmp_path, repo, ledger, apply=True)

    assert applied.eligible == 1
    assert applied.pruned == 1
    assert applied.reclaimed_bytes == archive_bytes
    assert not archive_path.exists()
    assert [event["action"] for event in ledger.worktree_reconciliation_events()][-2:] == [
        "archive_retention_prune_started",
        "archive_retention_pruned",
    ]


def test_trace_attempt_cannot_authorize_historical_archive_pruning(tmp_path: Path) -> None:
    repo, archive_path, ledger, run_id = _create_workspace_archive(tmp_path)
    ledger.record_trace_bundle_attempt(run_id, status="cleaned")

    report = _prune_archives(tmp_path, repo, ledger, apply=True)

    assert report.inspected == 0
    assert report.pruned == 0
    assert archive_path.exists()


@pytest.mark.parametrize(
    ("state", "dirty", "unique_commit"),
    [
        ("retryable", False, False),
        ("submitted", True, False),
        ("submitted", False, True),
    ],
)
def test_debug_dirty_and_unique_commit_archives_are_never_pruned(
    tmp_path: Path,
    state: str,
    dirty: bool,
    unique_commit: bool,
) -> None:
    repo, archive_path, ledger, run_id = _create_workspace_archive(
        tmp_path,
        state=state,
        dirty=dirty,
        unique_commit=unique_commit,
    )
    _record_verified_trace(ledger, run_id)

    report = _prune_archives(tmp_path, repo, ledger, apply=True)

    assert report.pruned == 0
    assert archive_path.exists()


def test_replaced_archive_fails_closed_during_retention_pruning(tmp_path: Path) -> None:
    repo, archive_path, ledger, run_id = _create_workspace_archive(tmp_path)
    _record_verified_trace(ledger, run_id)
    archive_path.write_bytes(archive_path.read_bytes() + b"replacement")

    report = _prune_archives(tmp_path, repo, ledger, apply=True)

    assert report.pruned == 0
    assert report.errors == 1
    assert archive_path.exists()


def test_archive_retention_requires_durable_pre_unlink_event(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo, archive_path, ledger, run_id = _create_workspace_archive(tmp_path)
    _record_verified_trace(ledger, run_id)
    real_record = ledger.record_worktree_reconciliation

    def fail_started(**fields) -> None:
        if fields.get("action") == "archive_retention_prune_started":
            raise RuntimeError("ledger unavailable")
        real_record(**fields)

    monkeypatch.setattr(ledger, "record_worktree_reconciliation", fail_started)

    report = _prune_archives(tmp_path, repo, ledger, apply=True)

    assert report.pruned == 0
    assert report.errors == 1
    assert archive_path.exists()


def test_archive_retention_rechecks_archived_head_remote_preservation(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    (repo / ".git" / "info" / "exclude").write_text("apps/crawler/.workspace/\n")
    workspace = worktree / "apps" / "crawler" / ".workspace" / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n")
    (worktree / "tracked.txt").write_text("remote branch commit\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "remote branch", cwd=worktree)
    archived_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    run_id = "issue-101-1-aaaaaaaa"
    _terminal_run(ledger, worktree, run_id=run_id, state="submitted")
    reconciled = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        verifier=lambda _run: RemoteProof(
            ok=True,
            kind="pull_request",
            detail={"headRefOid": archived_head},
        ),
    )
    archive_path = Path(str(reconciled.items[0].archive_path))
    _record_verified_trace(ledger, run_id)

    report = _prune_archives(tmp_path, repo, ledger, apply=True)

    assert report.pruned == 0
    assert report.retained_unverified == 1
    assert archive_path.exists()


def test_unrecorded_archive_is_never_considered_for_retention_pruning(
    tmp_path: Path,
) -> None:
    repo, archive_path, ledger, run_id = _create_workspace_archive(tmp_path)
    _record_verified_trace(ledger, run_id)
    unrecorded = archive_path.with_name("unrecorded.tar.gz")
    unrecorded.write_bytes(archive_path.read_bytes())

    report = _prune_archives(tmp_path, repo, ledger, apply=True)

    assert report.pruned == 1
    assert not archive_path.exists()
    assert unrecorded.exists()


def test_archive_retention_rejects_lexically_normalized_ledger_path(tmp_path: Path) -> None:
    repo, archive_path, ledger, run_id = _create_workspace_archive(tmp_path)
    _record_verified_trace(ledger, run_id)
    lexical_alias = str(archive_path.parent / "missing" / ".." / archive_path.name)
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE worktree_reconciliation_events SET archive_path = ? WHERE archive_path = ?",
            (lexical_alias, str(archive_path)),
        )

    report = _prune_archives(tmp_path, repo, ledger, apply=True)

    assert report.inspected == 0
    assert report.pruned == 0
    assert archive_path.exists()


def test_archive_retention_scan_is_bounded_and_rotates_past_corrupt_entries(
    tmp_path: Path,
) -> None:
    repo, archive_path, ledger, run_id = _create_workspace_archive(tmp_path)
    _record_verified_trace(ledger, run_id)
    removed = next(
        event
        for event in ledger.worktree_reconciliation_events()
        if event["action"] == "removed" and event["archive_path"] == str(archive_path)
    )
    archive_bytes = archive_path.read_bytes()
    columns = (
        "observed_at",
        "worktree_path",
        "source",
        "run_id",
        "issue",
        "state",
        "classification",
        "reason",
        "action",
        "bytes_before",
        "dirty_entries",
        "remote_proof_json",
        "archive_sha256",
        "reclaimed_bytes",
        "error",
    )
    for index in range(30):
        corrupt = archive_path.parent / f"000-corrupt-{index:02d}.tar.gz"
        corrupt.write_bytes(archive_bytes + bytes([index]))
        ledger.record_worktree_reconciliation(
            **{column: removed[column] for column in columns},
            archive_path=str(corrupt),
        )

    first = _prune_archives(tmp_path, repo, ledger, apply=True)
    second = _prune_archives(tmp_path, repo, ledger, apply=True)

    assert first.inspected == 25
    assert first.errors == 25
    assert first.pruned == 0
    assert second.inspected == 25
    assert second.pruned == 1
    assert not archive_path.exists()


def test_archive_recovery_events_only_return_durable_pre_unlink_evidence(
    tmp_path: Path,
) -> None:
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    common = {
        "observed_at": 1,
        "worktree_path": "/runner/worktrees/run-1",
        "source": "runner",
        "run_id": "run-1",
        "issue": 1,
        "state": "retryable",
        "classification": "debug-artifact",
        "reason": "test evidence",
        "bytes_before": 10,
        "dirty_entries": 1,
        "remote_proof_json": None,
        "reclaimed_bytes": 0,
        "error": None,
    }
    ledger.record_worktree_reconciliation(
        **common,
        action="archived",
        archive_path="/quarantine/first.tar.gz",
        archive_sha256="a" * 64,
    )
    ledger.record_worktree_reconciliation(
        **common,
        action="archive_compaction_started",
        archive_path="/quarantine/second.tar.gz",
        archive_sha256="b" * 64,
    )
    ledger.record_worktree_reconciliation(
        **common,
        action="archive_retention_prune_started",
        archive_path="/quarantine/third.tar.gz",
        archive_sha256="c" * 64,
    )

    assert ledger.worktree_archive_recovery_events() == [
        {
            "archive_path": "/quarantine/second.tar.gz",
            "archive_sha256": "b" * 64,
        },
        {
            "archive_path": "/quarantine/third.tar.gz",
            "archive_sha256": "c" * 64,
        },
    ]


@pytest.mark.parametrize(
    ("proof_kind", "detail"),
    [
        ("pull_request", "direct"),
        ("remote_branch", "direct"),
        ("issue_outcome", "nested"),
        ("no_remote_artifact", "none"),
    ],
)
def test_clean_unpushed_runner_head_is_bundled_for_every_remote_proof_shape(
    tmp_path: Path,
    proof_kind: str,
    detail: str,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    remote_oid = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (worktree / "tracked.txt").write_text("clean local-only commit\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "local only", cwd=worktree)
    local_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert local_oid != remote_oid
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        == ""
    )
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="submitted")
    remote_detail: dict[str, object]
    if detail == "direct":
        remote_detail = {"headRefOid": remote_oid}
    elif detail == "nested":
        remote_detail = {"remote": {"headRefOid": remote_oid}}
    else:
        remote_detail = {}

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        verifier=lambda run: RemoteProof(
            ok=True,
            kind=proof_kind,
            detail=remote_detail,
        ),
    )

    item = report.items[0]
    assert report.archived == 1
    assert report.removed == 1
    assert item.unique_commits
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "local_unique_commits"
    assert item.archive_path
    assert not worktree.exists()
    bundle_copy = tmp_path / f"{proof_kind}.bundle"
    with tarfile.open(item.archive_path, "r:gz") as archive:
        bundle = archive.extractfile("unique-commits.bundle")
        assert bundle is not None
        bundle_copy.write_bytes(bundle.read())
    listed = subprocess.run(
        ["git", "bundle", "list-heads", str(bundle_copy)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert local_oid in listed.stdout


def test_clean_runner_head_matching_remote_pr_needs_no_archive(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    (worktree / "tracked.txt").write_text("published clean commit\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "published", cwd=worktree)
    local_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="submitted")

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        verifier=lambda run: RemoteProof(
            ok=True,
            kind="pull_request",
            detail={"headRefOid": local_oid},
        ),
    )

    item = report.items[0]
    assert report.archived == 0
    assert report.removed == 1
    assert not item.unique_commits
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "exact_remote_branch"
    assert not worktree.exists()


def test_poisoned_local_main_cannot_hide_unpushed_runner_commit(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    authoritative_main_oid = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (worktree / "tracked.txt").write_text("local-only commit\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "local only", cwd=worktree)
    local_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _run("git", "update-ref", "refs/remotes/origin/main", local_oid, cwd=worktree)
    _run("git", "replace", local_oid, authoritative_main_oid, cwd=worktree)
    assert (
        subprocess.run(
            ["git", "rev-parse", "origin/main"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == local_oid
    )
    assert (
        subprocess.run(
            ["git", "diff", "--quiet", local_oid, authoritative_main_oid, "--"],
            cwd=repo,
            check=False,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--quiet",
                local_oid,
                authoritative_main_oid,
                "--",
            ],
            cwd=repo,
            check=False,
        ).returncode
        == 1
    )
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="submitted")

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        verifier=lambda run: RemoteProof(
            ok=True,
            kind="pull_request",
            detail={"headRefOid": authoritative_main_oid},
        ),
        authoritative_main_verifier=lambda: RemoteProof(
            ok=True,
            kind="authoritative_main",
            detail={
                "repository": "colophon-group/jobseek",
                "headRefOid": authoritative_main_oid,
            },
        ),
    )

    item = report.items[0]
    assert report.archived == 1
    assert report.removed == 1
    assert item.unique_commits
    assert item.main_oid == authoritative_main_oid
    assert item.archive_path
    assert not worktree.exists()
    bundle_copy = tmp_path / "poisoned-main.bundle"
    with tarfile.open(item.archive_path, "r:gz") as archive:
        bundle = archive.extractfile("unique-commits.bundle")
        assert bundle is not None
        bundle_copy.write_bytes(bundle.read())
        manifest_file = archive.extractfile("manifest.json")
        assert manifest_file is not None
        manifest = json.load(manifest_file)
    assert manifest["unique_commit_bundle"]["base_oid"] == authoritative_main_oid
    listed = subprocess.run(
        ["git", "bundle", "list-heads", str(bundle_copy)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert local_oid in listed.stdout


def test_forged_graft_cannot_hide_clean_runner_commit_from_bundle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    (repo / "main-only.txt").write_text("authoritative main change\n")
    _run("git", "add", "main-only.txt", cwd=repo)
    _run("git", "commit", "-m", "authoritative main", cwd=repo)
    authoritative_main_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (worktree / "tracked.txt").write_text("clean local-only commit\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "local only", cwd=worktree)
    local_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _run("git", "update-ref", "refs/remotes/origin/main", local_oid, cwd=worktree)
    common_dir = Path(
        subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=worktree,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    graft_file = common_dir / "info" / "grafts"
    graft_file.parent.mkdir(parents=True, exist_ok=True)
    graft_file.write_text(f"{authoritative_main_oid} {local_oid}\n")
    assert (
        subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "merge-base",
                "--is-ancestor",
                local_oid,
                authoritative_main_oid,
            ],
            cwd=repo,
            check=False,
            capture_output=True,
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "merge-base",
                "--is-ancestor",
                local_oid,
                authoritative_main_oid,
            ],
            cwd=repo,
            env={**os.environ, "GIT_GRAFT_FILE": os.devnull},
            check=False,
            capture_output=True,
        ).returncode
        == 1
    )
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="submitted")
    original_run = subprocess.run
    observed_proofs: set[str] = set()

    def require_neutral_graft(command, *args, **kwargs):
        command_parts = list(command)
        proof_kind: str | None = None
        if command_parts[:4] == ["git", "--no-replace-objects", "merge-base", "--is-ancestor"]:
            proof_kind = "merge-base"
        elif command_parts[:3] == ["git", "--no-replace-objects", "diff"]:
            proof_kind = "tree-diff"
        elif command_parts[:4] == ["git", "--no-replace-objects", "rev-list", "--objects"]:
            proof_kind = "object-sizing"
        elif command_parts[:3] == ["git", "--no-replace-objects", "cat-file"]:
            proof_kind = "object-sizes"
        elif command_parts[:4] == ["git", "--no-replace-objects", "bundle", "create"]:
            proof_kind = "bundle-create"
        elif command_parts[:4] == ["git", "--no-replace-objects", "bundle", "verify"]:
            proof_kind = "bundle-verify"
        if proof_kind is not None:
            proof_env = kwargs.get("env")
            assert proof_env is not None
            assert proof_env["GIT_GRAFT_FILE"] == os.devnull
            assert proof_env["GIT_NO_REPLACE_OBJECTS"] == "1"
            observed_proofs.add(proof_kind)
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.subprocess, "run", require_neutral_graft)

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        verifier=lambda run: RemoteProof(
            ok=True,
            kind="pull_request",
            detail={"headRefOid": authoritative_main_oid},
        ),
        authoritative_main_verifier=lambda: RemoteProof(
            ok=True,
            kind="authoritative_main",
            detail={
                "repository": "colophon-group/jobseek",
                "headRefOid": authoritative_main_oid,
            },
        ),
    )

    item = report.items[0]
    assert report.archived == 1
    assert report.removed == 1
    assert item.unique_commits
    assert item.archive_path
    assert not worktree.exists()
    assert observed_proofs == {
        "merge-base",
        "tree-diff",
        "object-sizing",
        "object-sizes",
        "bundle-create",
        "bundle-verify",
    }
    bundle_copy = tmp_path / "forged-graft.bundle"
    with tarfile.open(item.archive_path, "r:gz") as archive:
        bundle = archive.extractfile("unique-commits.bundle")
        assert bundle is not None
        bundle_copy.write_bytes(bundle.read())
    listed = original_run(
        ["git", "bundle", "list-heads", str(bundle_copy)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    assert local_oid in listed.stdout


def test_authoritative_main_lookup_failure_retains_runner_worktree(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="submitted")

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        verifier=lambda run: (_ for _ in ()).throw(
            AssertionError("remote artifact must not be trusted without main")
        ),
        authoritative_main_verifier=lambda: RemoteProof(
            ok=False,
            kind="main_lookup_failed",
            detail={"repository": "colophon-group/jobseek"},
            error="GitHub unavailable",
        ),
    )

    item = report.items[0]
    assert report.removed == 0
    assert report.archived == 0
    assert worktree.exists()
    assert item.classification == "remote_unverified"
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "main_lookup_failed"
    assert item.reason == "GitHub unavailable"


def test_dirty_retryable_worktree_is_archived_before_removal(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    (worktree / "untracked.txt").write_text("unique evidence\n")
    workspace = worktree / "apps" / "crawler" / ".workspace"
    workspace.mkdir(parents=True)
    (workspace / "state.json").write_text('{"step":"probe"}\n')

    report = _reconcile(tmp_path, repo, ledger, apply=True)

    item = report.items[0]
    assert report.archived == 1
    assert report.removed == 1
    assert item.archive_path
    assert item.archive_sha256
    archive_path = Path(item.archive_path)
    assert archive_path.exists()
    assert archive_path.stat().st_mode & 0o777 == 0o600
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    assert "manifest.json" in names
    assert "untracked/untracked.txt" in names
    assert "workspace/state.json" in names


def test_workspace_root_symlink_is_recorded_without_reading_external_content(
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    external = tmp_path / "external-secrets"
    external.mkdir()
    secret = b"must-not-enter-worktree-archive"
    (external / "credential.txt").write_bytes(secret)
    workspace = worktree / "apps" / "crawler" / ".workspace"
    workspace.parent.mkdir(parents=True)
    workspace.symlink_to(external, target_is_directory=True)

    report = _reconcile(tmp_path, repo, ledger, apply=True)

    item = report.items[0]
    assert report.archived == 1
    assert report.removed == 1
    assert item.archive_path
    assert (external / "credential.txt").read_bytes() == secret
    with tarfile.open(item.archive_path, "r:gz") as archive:
        names = set(archive.getnames())
        assert not any("credential.txt" in name for name in names)
        assert archive.getmember("untracked/apps/crawler/.workspace").issym()
        manifest_file = archive.extractfile("manifest.json")
        assert manifest_file is not None
        manifest_bytes = manifest_file.read()
        manifest = json.loads(manifest_bytes)
        assert secret not in manifest_bytes
    assert manifest["workspace_root"]["type"] == "symlink"
    assert "target_sha256" in manifest["workspace_root"]


def test_nested_workspace_parent_swap_cannot_archive_or_delete_external_content(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    nested = worktree / "apps" / "crawler" / ".workspace" / "slug" / "nested"
    nested.mkdir(parents=True)
    (nested / "evidence.txt").write_text("local evidence\n")
    external = tmp_path / "external-evidence"
    external.mkdir()
    secret = b"must-never-enter-the-archive"
    (external / "evidence.txt").write_bytes(secret)
    original_nested = nested.with_name("nested-original")
    original_add = reconcile_module._tar_add_candidate_no_follow
    swapped = False

    def swap_parent_before_archive(*args, **kwargs):
        nonlocal swapped
        if kwargs["archive_name"] == "workspace/slug/nested/evidence.txt" and not swapped:
            nested.rename(original_nested)
            nested.symlink_to(external, target_is_directory=True)
            swapped = True
        return original_add(*args, **kwargs)

    monkeypatch.setattr(
        reconcile_module,
        "_tar_add_candidate_no_follow",
        swap_parent_before_archive,
    )

    report = _reconcile(tmp_path, repo, ledger, apply=True)

    assert swapped
    assert report.removed == 0
    assert report.removal_failures == 1
    assert report.items[0].classification == "removal_failed"
    assert worktree.exists()
    assert nested.is_symlink()
    assert (external / "evidence.txt").read_bytes() == secret
    quarantine = tmp_path / "runner" / "state" / "worktree-quarantine"
    assert list(quarantine.glob("*.tar.gz")) == []


def test_workspace_enumeration_stays_on_open_directory_when_nested_path_is_swapped(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    nested = worktree / "apps" / "crawler" / ".workspace" / "slug" / "nested"
    nested.mkdir(parents=True)
    (nested / "local-evidence.txt").write_text("local evidence\n")
    external = tmp_path / "external-enumeration"
    external.mkdir()
    (external / "external-secret-name.txt").write_bytes(b"x" * 2 * 1024 * 1024)
    original_nested = nested.with_name("nested-original")
    original_collect = reconcile_module._collect_workspace_candidates_at
    enumerated_names: set[str] = set()
    swapped = False

    def swap_before_nested_scandir(directory_fd, **kwargs):
        nonlocal swapped
        if kwargs["relative"] == Path("slug/nested") and not swapped:
            nested.rename(original_nested)
            nested.symlink_to(external, target_is_directory=True)
            swapped = True
        result = original_collect(directory_fd, **kwargs)
        enumerated_names.update(path.name for path in kwargs["candidates"])
        return result

    monkeypatch.setattr(
        reconcile_module,
        "_collect_workspace_candidates_at",
        swap_before_nested_scandir,
    )

    report = _reconcile(tmp_path, repo, ledger, apply=True)

    assert swapped
    assert "local-evidence.txt" in enumerated_names
    assert "external-secret-name.txt" not in enumerated_names
    assert report.removed == 0
    assert report.removal_failures == 1
    assert worktree.exists()
    assert (external / "external-secret-name.txt").stat().st_size == 2 * 1024 * 1024


def test_directory_size_stays_on_open_directory_when_nested_path_is_swapped(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "sized-root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "small.txt").write_bytes(b"small")
    nested_inode = nested.stat().st_ino
    original_nested = root / "nested-original"
    external = tmp_path / "external-size"
    external.mkdir()
    (external / "large-secret.bin").write_bytes(b"x" * 2 * 1024 * 1024)
    original_size_at = reconcile_module._directory_bytes_at
    swapped = False

    def swap_before_nested_size(directory_fd, directory_stat):
        nonlocal swapped
        if directory_stat.st_ino == nested_inode and not swapped:
            nested.rename(original_nested)
            nested.symlink_to(external, target_is_directory=True)
            swapped = True
        return original_size_at(directory_fd, directory_stat)

    monkeypatch.setattr(reconcile_module, "_directory_bytes_at", swap_before_nested_size)

    with pytest.raises(RuntimeError, match="changed during size accounting"):
        reconcile_module._directory_bytes(root)

    assert swapped
    assert (external / "large-secret.bin").stat().st_size == 2 * 1024 * 1024


def test_missing_ledger_and_locked_worktrees_fail_closed_and_count_toward_bounds(
    tmp_path: Path,
) -> None:
    repo, missing = _repo_with_worktree(tmp_path, "missing")
    locked = tmp_path / "runner" / "worktrees" / "locked"
    _run("git", "worktree", "add", "--detach", str(locked), "HEAD", cwd=repo)
    _run("git", "worktree", "lock", "--reason", "test lock", str(locked), cwd=repo)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, locked, run_id="locked", pr_number=None, branch=None)

    report = _reconcile(tmp_path, repo, ledger, apply=True, max_directories=0)

    assert missing.exists()
    assert locked.exists()
    assert not report.within_bounds
    classifications = {item.name: item.classification for item in report.items}
    assert classifications == {"locked": "locked", "missing": "missing_ledger"}


def test_runner_root_symlink_to_registered_external_worktree_is_rejected(
    tmp_path: Path,
) -> None:
    repo, original = _repo_with_worktree(tmp_path)
    _run("git", "worktree", "remove", "--force", str(original), cwd=repo)
    outside = tmp_path / "outside-runner-worktree"
    _run("git", "worktree", "add", "--detach", str(outside), "HEAD", cwd=repo)
    link = tmp_path / "runner" / "worktrees" / "linked"
    link.symlink_to(outside, target_is_directory=True)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, outside)

    report = _reconcile(tmp_path, repo, ledger, apply=True, max_directories=0)

    assert outside.exists()
    assert link.is_symlink()
    assert report.removed == 0
    assert not report.within_bounds
    assert report.items[0].classification == "unsafe_path"
    assert "symlink" in report.items[0].reason


def test_runner_target_is_revalidated_after_pre_remove_hook(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)
    outside = tmp_path / "swapped-runner-worktree"

    def swap_for_symlink(item) -> None:
        worktree.rename(outside)
        worktree.symlink_to(outside, target_is_directory=True)

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        pre_remove=swap_for_symlink,
    )

    assert outside.exists()
    assert worktree.is_symlink()
    assert report.removed == 0
    assert report.removal_failures == 1
    assert report.items[0].classification == "removal_failed"
    assert "symlink" in (report.items[0].error or "")


def test_runner_late_untracked_file_is_archived_before_removal(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)
    main_oid = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    def add_late_file() -> RemoteProof:
        (worktree / "late-untracked.txt").write_text("created during remote proof\n")
        return RemoteProof(ok=True, kind="test_main", detail={"headRefOid": main_oid})

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        authoritative_main_verifier=add_late_file,
    )

    item = report.items[0]
    assert report.archived == 1
    assert report.removed == 1
    assert item.dirty_entries > 0
    assert item.planned_action == "archive_remove"
    assert item.archive_path
    with tarfile.open(item.archive_path, "r:gz") as archive:
        archived = archive.extractfile("untracked/late-untracked.txt")
        assert archived is not None
        assert archived.read() == b"created during remote proof\n"


def test_runner_becoming_active_during_remote_proof_is_retained(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    run_id = "issue-101-1-aaaaaaaa"
    _terminal_run(ledger, worktree, run_id=run_id)
    main_oid = subprocess.run(
        ["git", "rev-parse", "origin/main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    def reactivate_run() -> RemoteProof:
        ledger.update(run_id, state="running", pid=12345)
        return RemoteProof(ok=True, kind="test_main", detail={"headRefOid": main_oid})

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        authoritative_main_verifier=reactivate_run,
    )

    assert worktree.exists()
    assert report.active == 1
    assert report.removed == 0
    assert report.removal_failures == 0
    assert report.items[0].classification == "active"
    assert [event["action"] for event in ledger.worktree_reconciliation_events()] == [
        "removal_started",
        "retained",
    ]


def test_runner_file_created_at_atomic_claim_entry_is_retained(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)

    original_rename = os.rename

    def mutate_at_claim(source, destination, *args, **kwargs):
        if Path(source) == worktree and Path(destination).name.startswith(".jobseek-remove-"):
            (worktree / "claim-entry.txt").write_text("too late for stale removal\n")
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.os, "rename", mutate_at_claim)

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
    )

    assert worktree.exists()
    assert (worktree / "claim-entry.txt").read_text() == "too late for stale removal\n"
    assert report.removed == 0
    assert report.archived == 0
    assert report.removal_failures == 1
    assert report.items[0].classification == "removal_failed"
    assert "atomic removal claim" in (report.items[0].error or "")
    registered = reconcile_module._registered_worktrees(repo)
    assert str(worktree.resolve()) in registered
    assert not any(".jobseek-remove-" in path for path in registered)


def test_runner_activity_at_atomic_claim_entry_is_retained(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    run_id = "issue-101-1-aaaaaaaa"
    _terminal_run(ledger, worktree, run_id=run_id)
    ledger.update(run_id, pid=98765)
    process_live = False

    original_rename = os.rename

    def reactivate_at_claim(source, destination, *args, **kwargs):
        nonlocal process_live
        if Path(source) == worktree and Path(destination).name.startswith(".jobseek-remove-"):
            process_live = True
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.os, "rename", reactivate_at_claim)

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        pid_checker=lambda pid, observed_run_id: (
            process_live and pid == 98765 and observed_run_id == run_id
        ),
    )

    assert worktree.exists()
    assert report.active == 1
    assert report.removed == 0
    assert report.removal_failures == 0
    assert report.items[0].classification == "active"
    registered = reconcile_module._registered_worktrees(repo)
    assert str(worktree.resolve()) in registered
    assert not any(".jobseek-remove-" in path for path in registered)


def test_runner_original_path_recreated_at_git_remove_is_preserved(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)
    original_run = subprocess.run
    recreated = False

    def recreate_at_git_remove(command, *args, **kwargs):
        nonlocal recreated
        if list(command[:4]) == ["git", "worktree", "remove", "--force"] and Path(
            command[4]
        ).name.startswith(".jobseek-remove-"):
            worktree.mkdir()
            (worktree / "late-original-path.txt").write_text("preserved replacement\n")
            recreated = True
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.subprocess, "run", recreate_at_git_remove)

    report = _reconcile(tmp_path, repo, ledger, apply=True)

    assert recreated
    assert worktree.exists()
    assert (worktree / "late-original-path.txt").read_text() == "preserved replacement\n"
    assert report.removed == 0
    assert report.removal_failures == 1
    assert "recreated" in (report.items[0].error or "")


def test_runner_delayed_production_update_is_fenced_at_git_remove_entry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    run_id = "issue-101-1-aaaaaaaa"
    _terminal_run(ledger, worktree, run_id=run_id)
    original_run = subprocess.run
    update_started = threading.Event()
    update_finished = threading.Event()
    update_errors: list[BaseException] = []

    def late_production_update() -> None:
        update_started.set()
        try:
            ledger.update(run_id, state="running", pid=424242)
        except BaseException as exc:
            update_errors.append(exc)
        finally:
            update_finished.set()

    def probe_lease_at_git_remove(command, *args, **kwargs):
        if list(command[:4]) == ["git", "worktree", "remove", "--force"] and Path(
            command[4]
        ).name.startswith(".jobseek-remove-"):
            with sqlite3.connect(ledger.path) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                contender = threading.Thread(target=late_production_update)
                contender.start()
                assert update_started.wait(timeout=1)
                time.sleep(0.05)
                assert not update_finished.is_set()
                result = original_run(command, *args, **kwargs)
                blocker.commit()
                contender.join(timeout=2)
                assert not contender.is_alive()
                return result
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.subprocess, "run", probe_lease_at_git_remove)

    report = _reconcile(tmp_path, repo, ledger, apply=True)

    assert len(update_errors) == 1
    assert isinstance(update_errors[0], RuntimeError)
    assert "cleanup-fenced" in str(update_errors[0])
    assert not worktree.exists()
    assert report.removed == 1
    assert report.removal_failures == 0
    final_run = ledger.get_run(run_id)
    assert final_run is not None
    assert final_run["state"] == "failed"
    assert final_run["pid"] is None


def test_runner_execution_lease_blocks_cleanup_before_atomic_claim(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    run_id = "issue-101-1-aaaaaaaa"
    _terminal_run(ledger, worktree, run_id=run_id)

    with ledger.worktree_execution_lease(run_id):
        report = _reconcile(tmp_path, repo, ledger, apply=True)

    assert worktree.exists()
    assert report.removed == 0
    assert report.removal_failures == 1
    assert "execution lease" in (report.items[0].error or "")
    registered = reconcile_module._registered_worktrees(repo)
    assert str(worktree.resolve()) in registered


def test_runner_noop_remover_restores_claim_and_fails_closed(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)

    def noop_remover(path: Path, final_guard) -> None:
        final_guard()

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=noop_remover,
    )

    assert worktree.exists()
    assert report.removed == 0
    assert report.removal_failures == 1
    assert "atomic claim still exists" in (report.items[0].error or "")
    registered = reconcile_module._registered_worktrees(repo)
    assert str(worktree.resolve()) in registered
    assert not any(".jobseek-remove-" in path for path in registered)


def test_supported_runner_execution_lease_blocks_managed_cleanup(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    with ledger.worktree_execution_lease("active-supported-runner"):
        report = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=True)

    assert worktree.exists()
    assert report.removed == 0
    assert report.removal_failures == 1
    assert "execution lease" in (report.items[0].error or "")


def test_runner_mutation_during_pre_remove_retains_archived_worktree(
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)
    workspace = worktree / "apps" / "crawler" / ".workspace"
    workspace.mkdir(parents=True)
    evidence = workspace / "evidence.txt"
    evidence.write_text("archived first\n")

    def mutate_after_archive(item) -> None:
        evidence.unlink()
        workspace.rmdir()
        (worktree / "late-after-archive.txt").write_text("must retain\n")

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        pre_remove=mutate_after_archive,
    )

    item = report.items[0]
    assert worktree.exists()
    assert (worktree / "late-after-archive.txt").read_text() == "must retain\n"
    assert report.archived == 1
    assert report.removed == 0
    assert report.removal_failures == 1
    assert item.archive_path
    assert item.classification == "removal_failed"
    assert "pre-remove" in (item.error or "")


def test_runner_pre_remove_may_delete_only_archived_workspace_evidence(
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)
    workspace = worktree / "apps" / "crawler" / ".workspace"
    workspace.mkdir(parents=True)
    evidence = workspace / "evidence.txt"
    evidence.write_text("archive before cleanup\n")

    def remove_archived_workspace(_item) -> None:
        evidence.unlink()
        workspace.rmdir()

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        pre_remove=remove_archived_workspace,
    )

    item = report.items[0]
    assert report.archived == 1
    assert report.removed == 1
    assert item.archive_path
    with tarfile.open(item.archive_path, "r:gz") as archive:
        archived = archive.extractfile("workspace/evidence.txt")
        assert archived is not None
        assert archived.read() == b"archive before cleanup\n"


def test_remote_verification_failure_retains_terminal_worktree(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        verifier=lambda run: RemoteProof(
            ok=False,
            kind="pr_lookup_failed",
            error="network unavailable",
        ),
    )

    assert worktree.exists()
    assert report.items[0].classification == "remote_unverified"
    assert report.items[0].error is None


def test_removal_failure_is_recorded_and_retained(tmp_path: Path) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("simulated removal failure")

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )

    assert worktree.exists()
    assert report.removal_failures == 1
    assert report.items[0].classification == "removal_failed"
    assert report.items[0].error == "simulated removal failure"
    assert ledger.worktree_reconciliation_events()[-1]["action"] == "removal_failed"
    with pytest.raises(RuntimeError, match="cleanup-fenced"):
        ledger.update("issue-101-1-aaaaaaaa", state="running", pid=12345)


def test_repeated_removal_failure_publishes_fresh_archives_without_reuse(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    evidence = worktree / "unique-evidence.txt"
    evidence.write_text("preserve this evidence\n")
    monkeypatch.setattr(reconcile_module.time, "time", lambda: 1_800_000_000)

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("simulated persistent removal failure")

    first = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    quarantine = tmp_path / "runner" / "state" / "worktree-quarantine"
    archives = list(quarantine.glob("*.tar.gz"))
    assert first.archived == 1
    assert first.removal_failures == 1
    assert len(archives) == 1
    first_archive = archives[0]
    first_bytes = first_archive.read_bytes()
    stale_temp = quarantine / ".orphaned-run.99999999.tmp"
    stale_bundle = quarantine / ".orphaned-run.99999999.bundle"
    stale_temp.write_bytes(b"t" * 4096)
    stale_bundle.write_bytes(b"b" * 4096)

    second = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    archives = list(quarantine.glob("*.tar.gz"))
    assert second.archived == 1
    assert second.removal_failures == 1
    assert len(archives) == 2
    assert first_archive.read_bytes() == first_bytes
    assert not stale_temp.exists()
    assert not stale_bundle.exists()
    for archive_path in archives:
        with tarfile.open(archive_path, "r:gz") as archive:
            archived_evidence = archive.extractfile("untracked/unique-evidence.txt")
            assert archived_evidence is not None
            assert archived_evidence.read() == b"preserve this evidence\n"


def test_successful_removal_compacts_verified_same_snapshot_generations(
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    evidence = worktree / "unique-evidence.txt"
    evidence.write_text("preserve this evidence\n")

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("simulated persistent removal failure")

    first = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    second = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    prior_archives = {
        Path(first.items[0].archive_path or ""),
        Path(second.items[0].archive_path or ""),
    }
    assert len(prior_archives) == 2
    assert all(path.is_file() for path in prior_archives)

    completed = _reconcile(tmp_path, repo, ledger, apply=True)

    current_archive = Path(completed.items[0].archive_path or "")
    assert completed.removed == 1
    assert current_archive.is_file()
    assert all(not path.exists() for path in prior_archives)
    quarantine = tmp_path / "runner" / "state" / "worktree-quarantine"
    assert list(quarantine.glob("*.tar.gz")) == [current_archive]
    with tarfile.open(current_archive, "r:gz") as archive:
        archived_evidence = archive.extractfile("untracked/unique-evidence.txt")
        assert archived_evidence is not None
        assert archived_evidence.read() == b"preserve this evidence\n"
    events = ledger.worktree_reconciliation_events()
    assert [event["action"] for event in events].count("archive_compaction_started") == 2
    assert [event["action"] for event in events].count("archive_pruned") == 2
    event = events[-1]
    assert event["action"] == "archives_compacted"
    assert event["reclaimed_bytes"] > 0
    assert event["archive_path"] == str(current_archive)


def test_survivor_directory_entry_is_synced_before_compaction_claim(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    (worktree / "evidence.txt").write_text("preserve this evidence\n")

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("retain the first generation")

    _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    operations: list[str] = []
    real_fsync = reconcile_module.os.fsync
    real_claim_child_at = reconcile_module.claim_child_at

    def observe_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            operations.append("directory_fsync")
        real_fsync(fd)

    def observe_claim(parent_fd: int, name: str, *, expected, claimed_name=None) -> str:
        operations.append("claim")
        return real_claim_child_at(
            parent_fd,
            name,
            expected=expected,
            claimed_name=claimed_name,
        )

    monkeypatch.setattr(reconcile_module.os, "fsync", observe_fsync)
    monkeypatch.setattr(reconcile_module, "claim_child_at", observe_claim)

    completed = _reconcile(tmp_path, repo, ledger, apply=True)

    assert completed.removed == 1
    assert "claim" in operations
    assert operations.index("directory_fsync") < operations.index("claim")


def test_archive_replacement_during_compaction_is_preserved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    (worktree / "evidence.txt").write_text("preserve this evidence\n")

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("retain the first generation")

    first = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    prior_archive = Path(first.items[0].archive_path or "")
    replacement = b"replacement must survive\n"
    real_claim_child_at = reconcile_module.claim_child_at

    def replace_before_claim(
        parent_fd: int,
        name: str,
        *,
        expected,
        claimed_name: str | None = None,
    ) -> str:
        if name == prior_archive.name:
            os.unlink(name, dir_fd=parent_fd)
            replacement_fd = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                os.write(replacement_fd, replacement)
            finally:
                os.close(replacement_fd)
        return real_claim_child_at(
            parent_fd,
            name,
            expected=expected,
            claimed_name=claimed_name,
        )

    monkeypatch.setattr(reconcile_module, "claim_child_at", replace_before_claim)

    completed = _reconcile(tmp_path, repo, ledger, apply=True)

    assert completed.removed == 1
    assert prior_archive.read_bytes() == replacement
    current_archive = Path(completed.items[0].archive_path or "")
    assert current_archive.is_file()
    events = ledger.worktree_reconciliation_events()
    assert [event["action"] for event in events].count("archive_compaction_started") == 1
    assert "archive_pruned" not in [event["action"] for event in events]
    event = events[-1]
    assert event["action"] == "archive_compaction_incomplete"
    assert event["reclaimed_bytes"] == 0


@pytest.mark.parametrize("survivor_change", ["remove", "replace"])
def test_survivor_change_after_compaction_audit_restores_candidate(
    survivor_change: str,
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    (worktree / "evidence.txt").write_text("preserve this evidence\n")

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("retain the first generation")

    first = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    prior_archive = Path(first.items[0].archive_path or "")
    quarantine = prior_archive.parent
    replacement = b"replacement survivor\n"
    real_record = ledger.record_worktree_reconciliation
    changed_survivor: Path | None = None

    def change_survivor_after_audit(**fields) -> None:
        nonlocal changed_survivor
        real_record(**fields)
        if fields.get("action") != "archive_compaction_started" or changed_survivor:
            return
        candidate = Path(str(fields["archive_path"]))
        survivors = [path for path in quarantine.glob("*.tar.gz") if path != candidate]
        assert len(survivors) == 1
        changed_survivor = survivors[0]
        if survivor_change == "remove":
            changed_survivor.unlink()
        else:
            changed_survivor.write_bytes(replacement)

    monkeypatch.setattr(
        ledger,
        "record_worktree_reconciliation",
        change_survivor_after_audit,
    )

    completed = _reconcile(tmp_path, repo, ledger, apply=True)

    assert completed.removed == 1
    assert prior_archive.is_file()
    assert changed_survivor is not None
    if survivor_change == "remove":
        assert not changed_survivor.exists()
    else:
        assert changed_survivor.read_bytes() == replacement
    events = ledger.worktree_reconciliation_events()
    assert "archive_pruned" not in [event["action"] for event in events]
    assert events[-1]["action"] == "archive_compaction_incomplete"


def test_in_place_candidate_mutation_after_claim_is_restored(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    (worktree / "evidence.txt").write_text("preserve this evidence\n")

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("retain the first generation")

    first = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    prior_archive = Path(first.items[0].archive_path or "")
    replacement = b"in-place candidate mutation\n"
    real_claim_child_at = reconcile_module.claim_child_at

    def mutate_claimed_candidate(
        parent_fd: int,
        name: str,
        *,
        expected,
        claimed_name: str | None = None,
    ) -> str:
        claimed = real_claim_child_at(
            parent_fd,
            name,
            expected=expected,
            claimed_name=claimed_name,
        )
        if name == prior_archive.name:
            claimed_fd = os.open(claimed, os.O_WRONLY | os.O_TRUNC, dir_fd=parent_fd)
            try:
                os.write(claimed_fd, replacement)
            finally:
                os.close(claimed_fd)
        return claimed

    monkeypatch.setattr(reconcile_module, "claim_child_at", mutate_claimed_candidate)

    completed = _reconcile(tmp_path, repo, ledger, apply=True)

    assert completed.removed == 1
    assert prior_archive.read_bytes() == replacement
    current_archive = Path(completed.items[0].archive_path or "")
    assert current_archive.is_file()
    events = ledger.worktree_reconciliation_events()
    assert "archive_pruned" not in [event["action"] for event in events]
    assert events[-1]["action"] == "archive_compaction_incomplete"


def test_restore_collision_claim_is_never_pruned_as_staging_and_recovers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_dir = tmp_path / "worktree-quarantine"
    archive_dir.mkdir()
    candidate = archive_dir / f"run-{'a' * 24}-{'b' * 24}.tar.gz"
    original_evidence = b"verified archive evidence\n"
    replacement = b"replacement at original name\n"
    candidate.write_bytes(original_evidence)
    recorded_events = [
        {
            "archive_path": str(candidate),
            "archive_sha256": hashlib.sha256(original_evidence).hexdigest(),
        }
    ]
    parent_fd = reconcile_module.open_absolute_directory_no_follow(archive_dir)
    expected = os.stat(candidate.name, dir_fd=parent_fd, follow_symlinks=False)

    def collide_with_restore(claimed_name: str) -> None:
        replacement_fd = os.open(
            candidate.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_fd,
        )
        try:
            os.write(replacement_fd, replacement)
        finally:
            os.close(replacement_fd)
        raise RuntimeError("simulated survivor validation failure")

    try:
        with pytest.raises(RuntimeError, match="claim could not be restored"):
            reconcile_module._claim_and_unlink_archive_generation(
                parent_fd,
                candidate.name,
                expected=expected,
                pre_unlink_check=collide_with_restore,
            )
    finally:
        os.close(parent_fd)

    claims = list(archive_dir.glob(f"{reconcile_module.ARCHIVE_PRUNE_CLAIM_PREFIX}*"))
    assert len(claims) == 1
    claim = claims[0]
    assert claim.read_bytes() == original_evidence
    assert candidate.read_bytes() == replacement

    monkeypatch.setattr(reconcile_module, "_pid_is_alive", lambda pid: False)
    reconcile_module._prune_stale_archive_staging(archive_dir)
    assert claim.read_bytes() == original_evidence
    assert (
        reconcile_module._recover_archive_compaction_claims(
            archive_dir,
            recorded_events=recorded_events,
        )
        == 0
    )
    assert claim.read_bytes() == original_evidence

    candidate.unlink()
    assert (
        reconcile_module._recover_archive_compaction_claims(
            archive_dir,
            recorded_events=recorded_events,
        )
        == 1
    )
    assert not claim.exists()
    assert candidate.read_bytes() == original_evidence


@pytest.mark.parametrize(
    ("original_name", "expected_name_bytes"),
    [
        (
            f"company-request-7410-codex-governor-20260827T185355-{'a' * 24}-{'b' * 24}.tar.gz",
            108,
        ),
        (f"{'r' * 198}-{'a' * 24}-{'b' * 24}.tar.gz", 255),
    ],
)
def test_archive_prune_claim_is_bounded_for_managed_archive_names(
    original_name: str,
    expected_name_bytes: int,
) -> None:
    assert len(os.fsencode(original_name)) == expected_name_bytes
    claim_name = reconcile_module._archive_prune_claim_name(
        original_name,
        pid=12345,
        token="c" * 24,
    )

    assert len(os.fsencode(claim_name)) <= 240
    assert reconcile_module._parse_archive_prune_claim_name(claim_name) == (
        12345,
        hashlib.sha256(os.fsencode(original_name)).hexdigest(),
    )


def test_oversized_pid_archive_claim_is_retained_without_aborting_reconciliation(
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree)
    archive_dir = tmp_path / "runner" / "state" / "worktree-quarantine"
    archive_dir.mkdir(parents=True)
    original_name = f"run-{'a' * 24}-{'b' * 24}.tar.gz"
    original_name_sha256 = hashlib.sha256(os.fsencode(original_name)).hexdigest()
    oversized_claim_name = (
        f"{reconcile_module.ARCHIVE_PRUNE_CLAIM_PREFIX}"
        f"{reconcile_module.MAX_PLATFORM_PID + 1}-{'c' * 24}-"
        f"{original_name_sha256}.claim"
    )
    claim = archive_dir / oversized_claim_name
    claim_evidence = b"verified archive evidence\n"
    claim.write_bytes(claim_evidence)
    ledger.record_worktree_reconciliation(
        observed_at=1,
        worktree_path=str(worktree.resolve()),
        source="runner",
        run_id="run-1",
        issue=1,
        state="submitted",
        classification="debug-artifact",
        reason="test evidence",
        action="archive_compaction_started",
        bytes_before=len(claim_evidence),
        dirty_entries=1,
        remote_proof_json=None,
        archive_path=str(archive_dir / original_name),
        archive_sha256=hashlib.sha256(claim_evidence).hexdigest(),
        reclaimed_bytes=0,
        error=None,
    )

    assert reconcile_module._parse_archive_prune_claim_name(oversized_claim_name) is None
    completed = _reconcile(tmp_path, repo, ledger, apply=True)

    assert completed.removed == 1
    assert claim.read_bytes() == claim_evidence
    assert not (archive_dir / original_name).exists()


def test_archive_prune_recovery_retains_claim_that_no_longer_matches_ledger(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_dir = tmp_path / "worktree-quarantine"
    archive_dir.mkdir()
    original_name = f"run-{'a' * 24}-{'b' * 24}.tar.gz"
    claim_name = reconcile_module._archive_prune_claim_name(
        original_name,
        pid=12345,
        token="c" * 24,
    )
    claim = archive_dir / claim_name
    claim.write_bytes(b"changed evidence\n")
    recorded_events = [
        {
            "archive_path": str(archive_dir / original_name),
            "archive_sha256": hashlib.sha256(b"original evidence\n").hexdigest(),
        }
    ]
    monkeypatch.setattr(reconcile_module, "_pid_is_alive", lambda pid: False)

    assert (
        reconcile_module._recover_archive_compaction_claims(
            archive_dir,
            recorded_events=recorded_events,
        )
        == 0
    )
    assert claim.read_bytes() == b"changed evidence\n"
    assert not (archive_dir / original_name).exists()


def test_duplicate_generations_are_compacted_before_fresh_capacity_reservation(
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    (worktree / "evidence.txt").write_text("preserve this evidence\n")

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("retain duplicate generations")

    first = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    second = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    prior_archives = [
        Path(first.items[0].archive_path or ""),
        Path(second.items[0].archive_path or ""),
    ]
    prior_sizes = [path.stat().st_size for path in prior_archives]
    quarantine_bytes = reconcile_module._directory_bytes(prior_archives[0].parent)
    quarantine_overhead = quarantine_bytes - sum(prior_sizes)
    projected_fresh_bytes = reconcile_module._directory_bytes(worktree) + 1024 * 1024
    max_bytes = projected_fresh_bytes + quarantine_overhead + max(prior_sizes) + 1
    assert quarantine_bytes + projected_fresh_bytes > max_bytes

    completed = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        max_bytes=max_bytes,
    )

    assert completed.removed == 1, completed.items[0].error
    current_archive = Path(completed.items[0].archive_path or "")
    assert current_archive.is_file()
    assert list(current_archive.parent.glob("*.tar.gz")) == [current_archive]
    assert all(not path.exists() for path in prior_archives)


def test_archive_checksum_is_validated_before_manifest_decompression(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_dir = tmp_path / "worktree-quarantine"
    archive_dir.mkdir()
    candidate = archive_dir / f"run-{'a' * 24}-{'b' * 24}.tar.gz"
    candidate.write_bytes(b"not a trusted archive")
    parent_fd = reconcile_module.open_absolute_directory_no_follow(archive_dir)

    def fail_if_decompressed(*args, **kwargs):
        raise AssertionError("checksum-mismatched archive was decompressed")

    monkeypatch.setattr(reconcile_module.tarfile, "open", fail_if_decompressed)
    try:
        with pytest.raises(RuntimeError, match="checksum no longer matches"):
            reconcile_module._inspect_archive_generation_at(
                parent_fd,
                candidate.name,
                expected_sha256="c" * 64,
            )
    finally:
        os.close(parent_fd)


def test_archive_compaction_requires_a_durable_pre_unlink_event(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    (worktree / "evidence.txt").write_text("preserve this evidence\n")

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("retain the first generation")

    first = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    prior_archive = Path(first.items[0].archive_path or "")
    real_record = ledger.record_worktree_reconciliation

    def fail_compaction_audit(**fields) -> None:
        if fields.get("action") == "archive_compaction_started":
            raise RuntimeError("simulated audit failure")
        real_record(**fields)

    monkeypatch.setattr(ledger, "record_worktree_reconciliation", fail_compaction_audit)

    completed = _reconcile(tmp_path, repo, ledger, apply=True)

    assert completed.removed == 1
    assert prior_archive.is_file()
    current_archive = Path(completed.items[0].archive_path or "")
    assert current_archive.is_file()
    event = ledger.worktree_reconciliation_events()[-1]
    assert event["action"] == "archive_compaction_incomplete"
    assert event["error"] == "one or more superseded archive generations could not be verified"


@pytest.mark.parametrize("old_archive_damage", ["invalid", "missing", "replaced"])
def test_damaged_prior_archive_is_never_reused_for_removal(
    old_archive_damage: str,
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    evidence = worktree / "evidence.txt"
    evidence.write_text("irreplaceable evidence\n")

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("retain first archive")

    first = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    prior_archive = Path(first.items[0].archive_path or "")
    assert prior_archive.is_file()
    with tarfile.open(prior_archive, "r:gz") as archive:
        manifest_file = archive.extractfile("manifest.json")
        assert manifest_file is not None
        manifest_bytes = manifest_file.read()

    if old_archive_damage == "invalid":
        prior_archive.write_bytes(b"not a tar archive")
    else:
        with tarfile.open(prior_archive, "w:gz") as archive:
            manifest_info = tarfile.TarInfo("manifest.json")
            manifest_info.size = len(manifest_bytes)
            archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
            if old_archive_damage == "replaced":
                replacement = b"forged replacement\n"
                evidence_info = tarfile.TarInfo("untracked/evidence.txt")
                evidence_info.size = len(replacement)
                archive.addfile(evidence_info, io.BytesIO(replacement))

    second = _reconcile(tmp_path, repo, ledger, apply=True)

    assert second.removed == 1
    assert second.archived == 1
    fresh_archive = Path(second.items[0].archive_path or "")
    assert fresh_archive.is_file()
    assert fresh_archive != prior_archive
    assert prior_archive.is_file()
    with tarfile.open(fresh_archive, "r:gz") as archive:
        archived_evidence = archive.extractfile("untracked/evidence.txt")
        assert archived_evidence is not None
        assert archived_evidence.read() == b"irreplaceable evidence\n"


def test_staging_recovery_preserves_files_owned_by_live_process(tmp_path: Path) -> None:
    quarantine = tmp_path / "worktree-quarantine"
    quarantine.mkdir()
    active = quarantine / f".active-run.{os.getpid()}.tmp"
    active_tokenized = quarantine / f".active-run.{os.getpid()}.{'a' * 24}.bundle"
    stale = quarantine / ".stale-run.99999999.bundle"
    stale_tokenized = quarantine / f".stale-run.99999999.{'b' * 24}.tmp"
    active.write_bytes(b"active")
    active_tokenized.write_bytes(b"active-tokenized")
    stale.write_bytes(b"stale")
    stale_tokenized.write_bytes(b"stale-tokenized")

    reclaimed = reconcile_module._prune_stale_archive_staging(quarantine)

    assert reclaimed == len(b"stale") + len(b"stale-tokenized")
    assert active.read_bytes() == b"active"
    assert active_tokenized.read_bytes() == b"active-tokenized"
    assert not stale.exists()
    assert not stale_tokenized.exists()


def test_clean_tracked_checkout_bytes_do_not_consume_archive_capacity(
    tmp_path: Path,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    (worktree / "large-tracked.bin").write_bytes(os.urandom(4 * 1024 * 1024))
    _run("git", "add", "large-tracked.bin", cwd=worktree)
    _run("git", "commit", "-m", "large tracked checkout", cwd=worktree)
    head_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _run("git", "update-ref", "refs/remotes/origin/main", head_oid, cwd=repo)
    workspace = worktree / "apps" / "crawler" / ".workspace"
    workspace.mkdir(parents=True)
    evidence = b"terminal workspace evidence\n" * 512
    (workspace / "state.log").write_bytes(evidence)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="submitted")
    max_archive_bytes = 2 * 1024 * 1024

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        max_bytes=max_archive_bytes,
    )

    item = report.items[0]
    assert item.bytes > max_archive_bytes
    assert report.archived == 1
    assert report.removed == 1
    assert item.archive_path
    assert not worktree.exists()
    with tarfile.open(item.archive_path, "r:gz") as archive:
        names = set(archive.getnames())
        archived = archive.extractfile("workspace/state.log")
        assert archived is not None
        assert archived.read() == evidence
    assert "untracked/large-tracked.bin" not in names


def test_deleted_large_head_blob_is_streamed_and_cannot_overshoot_archive_capacity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _run("git", "init", "-b", "main", cwd=repo)
    _run("git", "config", "user.name", "Test Runner", cwd=repo)
    _run("git", "config", "user.email", "runner@example.test", cwd=repo)
    (repo / "large.bin").write_bytes(os.urandom(4 * 1024 * 1024))
    _run("git", "add", "large.bin", cwd=repo)
    _run("git", "commit", "-m", "large base blob", cwd=repo)
    _run("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    worktree = tmp_path / "runner" / "worktrees" / "run-worktree"
    worktree.parent.mkdir(parents=True)
    _run("git", "worktree", "add", "--detach", str(worktree), "HEAD", cwd=repo)
    (worktree / "large.bin").unlink()
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    original_run = subprocess.run

    def reject_buffered_binary_diff(command, *args, **kwargs):
        if list(command) == [
            "git",
            "--no-replace-objects",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--binary",
            "HEAD",
        ]:
            raise AssertionError("binary patch must not be captured by subprocess.run")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.subprocess, "run", reject_buffered_binary_diff)

    report = _reconcile(
        tmp_path,
        repo,
        ledger,
        apply=True,
        max_bytes=2 * 1024 * 1024,
    )

    assert report.archived == 0
    assert report.removed == 0
    assert report.removal_failures == 1
    assert worktree.exists()
    assert "capacity gate" in (report.items[0].error or "")
    quarantine = tmp_path / "runner" / "state" / "worktree-quarantine"
    assert not quarantine.exists() or list(quarantine.glob("*.tar.gz")) == []


def test_managed_clean_worktree_is_removed_and_recorded_separately(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    plan = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=False)

    assert plan.items[0].source == "managed"
    assert plan.items[0].planned_action == "remove"
    assert plan.items[0].remote_proof is not None
    assert plan.items[0].remote_proof["kind"] == "head_ancestor_main"
    assert worktree.exists()

    report = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=True)

    assert report.removed == 1
    assert not worktree.exists()
    events = ledger.worktree_reconciliation_events()
    assert [event["source"] for event in events] == ["managed", "managed"]
    assert [event["action"] for event in events] == ["removal_started", "removed"]


def test_managed_late_untracked_file_is_archived_before_removal(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    main_oid = subprocess.run(
        ["git", "rev-parse", "refs/remotes/origin/main"],
        cwd=managed,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    def add_late_file() -> RemoteProof:
        (worktree / "late-managed.txt").write_text("created during managed proof\n")
        return RemoteProof(ok=True, kind="test_main", detail={"headRefOid": main_oid})

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        authoritative_main_verifier=add_late_file,
    )

    item = report.items[0]
    assert report.archived == 1
    assert report.removed == 1
    assert item.dirty_entries > 0
    assert item.archive_path
    with tarfile.open(item.archive_path, "r:gz") as archive:
        archived = archive.extractfile("untracked/late-managed.txt")
        assert archived is not None
        assert archived.read() == b"created during managed proof\n"


def test_managed_becoming_live_during_remote_proof_is_retained(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    main_oid = subprocess.run(
        ["git", "rev-parse", "refs/remotes/origin/main"],
        cwd=managed,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    is_live = False

    def mark_live() -> RemoteProof:
        nonlocal is_live
        is_live = True
        return RemoteProof(ok=True, kind="test_main", detail={"headRefOid": main_oid})

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        live_path_checker=lambda path: is_live,
        authoritative_main_verifier=mark_live,
    )

    assert worktree.exists()
    assert report.active == 1
    assert report.removed == 0
    assert report.removal_failures == 0
    assert report.items[0].classification == "active"
    assert [event["action"] for event in ledger.worktree_reconciliation_events()] == [
        "removal_started",
        "retained",
    ]


def test_managed_file_created_at_atomic_claim_entry_is_retained(
    monkeypatch,
    tmp_path: Path,
) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    original_rename = os.rename

    def mutate_at_claim(source, destination, *args, **kwargs):
        if Path(source) == worktree and Path(destination).name.startswith(".jobseek-remove-"):
            (worktree / "managed-claim-entry.txt").write_text("retain managed evidence\n")
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.os, "rename", mutate_at_claim)

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
    )

    assert worktree.exists()
    assert (worktree / "managed-claim-entry.txt").read_text() == "retain managed evidence\n"
    assert report.removed == 0
    assert report.archived == 0
    assert report.removal_failures == 1
    assert report.items[0].classification == "removal_failed"
    assert "atomic removal claim" in (report.items[0].error or "")
    registered = reconcile_module._registered_worktrees(managed)
    assert str(worktree.resolve()) in registered
    assert not any(".jobseek-remove-" in path for path in registered)


@pytest.mark.parametrize("live_reference", ["original", "claim"])
def test_managed_live_path_at_atomic_claim_entry_is_retained(
    monkeypatch,
    tmp_path: Path,
    live_reference: str,
) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    is_live = False
    original_resolved = worktree.resolve()

    original_rename = os.rename

    def mark_live_at_claim(source, destination, *args, **kwargs):
        nonlocal is_live
        if Path(source) == worktree and Path(destination).name.startswith(".jobseek-remove-"):
            is_live = True
        return original_rename(source, destination, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.os, "rename", mark_live_at_claim)

    def exact_live_path(path: Path) -> bool:
        if not is_live:
            return False
        if live_reference == "original":
            return path == original_resolved
        return path.name.startswith(".jobseek-remove-")

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        live_path_checker=exact_live_path,
    )

    assert worktree.exists()
    assert report.active == 1
    assert report.removed == 0
    assert report.removal_failures == 0
    assert report.items[0].classification == "active"
    registered = reconcile_module._registered_worktrees(managed)
    assert str(worktree.resolve()) in registered
    assert not any(".jobseek-remove-" in path for path in registered)


def test_managed_original_path_recreated_at_git_remove_is_preserved(
    monkeypatch,
    tmp_path: Path,
) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    original_run = subprocess.run
    recreated = False

    def recreate_at_git_remove(command, *args, **kwargs):
        nonlocal recreated
        if list(command[:4]) == ["git", "worktree", "remove", "--force"] and Path(
            command[4]
        ).name.startswith(".jobseek-remove-"):
            worktree.mkdir()
            (worktree / "late-managed-original.txt").write_text("preserved managed path\n")
            recreated = True
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.subprocess, "run", recreate_at_git_remove)

    report = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=True)

    assert recreated
    assert worktree.exists()
    assert (worktree / "late-managed-original.txt").read_text() == "preserved managed path\n"
    assert report.removed == 0
    assert report.removal_failures == 1
    assert "recreated" in (report.items[0].error or "")


def test_managed_archive_mutation_does_not_replace_prior_archive(
    monkeypatch,
    tmp_path: Path,
) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    (worktree / "evidence.txt").write_text("stable evidence\n")

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("retain after first archive")

    first = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    first_archive = Path(first.items[0].archive_path or "")
    assert first.archived == 1
    assert first_archive.is_file()
    original_archive = first_archive.read_bytes()
    (worktree / "evidence.txt").write_text("second stable evidence\n")

    original_add = reconcile_module._tar_add_candidate_no_follow
    mutated = False

    def mutate_during_archive(*args, **kwargs):
        nonlocal mutated
        result = original_add(*args, **kwargs)
        if not mutated:
            (worktree / "evidence.txt").write_text("mutated during archive\n")
            mutated = True
        return result

    monkeypatch.setattr(
        reconcile_module,
        "_tar_add_candidate_no_follow",
        mutate_during_archive,
    )

    second = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=True)

    assert mutated
    assert worktree.exists()
    assert (worktree / "evidence.txt").read_text() == "mutated during archive\n"
    assert second.removed == 0
    assert second.archived == 0
    assert second.removal_failures == 1
    assert second.items[0].classification == "removal_failed"
    assert "changed" in (second.items[0].error or "")
    assert first_archive.read_bytes() == original_archive


def test_managed_link_entry_mutation_retracts_new_archive_and_preserves_prior(
    monkeypatch,
    tmp_path: Path,
) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    evidence = worktree / "evidence.txt"
    evidence.write_text("first archive candidate\n")

    def fail_remove(path: Path, final_guard) -> None:
        raise RuntimeError("retain after first archive")

    first = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        remove_worktree=fail_remove,
    )
    first_archive = Path(first.items[0].archive_path or "")
    assert first_archive.is_file()
    original_archive = first_archive.read_bytes()
    evidence.write_text("second archive candidate\n")

    original_link = os.link
    mutated = False

    def mutate_at_link(source, destination, *args, **kwargs):
        nonlocal mutated
        evidence.write_text("third live state\n")
        mutated = True
        return original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(reconcile_module.os, "link", mutate_at_link)

    second = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=True)

    assert mutated
    assert worktree.exists()
    assert evidence.read_text() == "third live state\n"
    assert second.removed == 0
    assert second.archived == 0
    assert second.removal_failures == 1
    assert "changed" in (second.items[0].error or "")
    assert first_archive.read_bytes() == original_archive
    quarantine = tmp_path / "runner" / "state" / "worktree-quarantine"
    assert list(quarantine.glob("*.tar.gz")) == [first_archive]


def test_managed_worktree_created_during_active_run_is_never_removed(tmp_path: Path) -> None:
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    assert ledger.acquire(run_id="active", issue=101, active_slot="company-resolver")
    ledger.update(
        "active",
        started_at=1,
        pid=123,
        worktree_path=str(tmp_path / "runner" / "worktrees" / "active"),
    )
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)

    report = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=True)

    assert worktree.exists()
    assert report.active == 1
    assert report.items[0].classification == "active"
    assert "active runner window" in report.items[0].reason


def test_managed_live_and_locked_worktrees_fail_closed(tmp_path: Path) -> None:
    managed, worktrees, live = _managed_repo_with_worktree(tmp_path, "live")
    locked = worktrees / "locked"
    _run(
        "git",
        "worktree",
        "add",
        "-b",
        "fix-crawler/locked",
        str(locked),
        "origin/main",
        cwd=managed,
    )
    _run("git", "worktree", "lock", "--reason", "test", str(locked), cwd=managed)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        live_path_checker=lambda path: path == live.resolve(),
        max_directories=0,
    )

    assert live.exists()
    assert locked.exists()
    assert not report.within_bounds
    assert {item.name: item.classification for item in report.items} == {
        "live": "active",
        "locked": "locked",
    }


def test_managed_root_symlink_to_registered_external_worktree_is_rejected(
    tmp_path: Path,
) -> None:
    managed, worktrees, original = _managed_repo_with_worktree(tmp_path)
    _run("git", "worktree", "remove", "--force", str(original), cwd=managed)
    outside = tmp_path / "outside-managed-worktree"
    _run(
        "git",
        "worktree",
        "add",
        "-b",
        "fix-crawler/outside-managed",
        str(outside),
        "origin/main",
        cwd=managed,
    )
    link = worktrees / "linked"
    link.symlink_to(outside, target_is_directory=True)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        max_directories=0,
    )

    assert outside.exists()
    assert link.is_symlink()
    assert report.removed == 0
    assert not report.within_bounds
    assert report.items[0].classification == "unsafe_path"
    assert "symlink" in report.items[0].reason


def test_managed_unique_commit_is_bundled_before_removal(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    (worktree / "tracked.txt").write_text("local unique commit\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "local unique", cwd=worktree)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=True)

    item = report.items[0]
    assert item.unique_commits
    assert item.archive_path
    assert not worktree.exists()
    with tarfile.open(item.archive_path, "r:gz") as archive:
        names = set(archive.getnames())
        assert archive.getmember("unique-commits.bundle").mode == 0o600
        manifest_file = archive.extractfile("manifest.json")
        assert manifest_file is not None
        manifest = json.load(manifest_file)
    assert "unique-commits.bundle" in names
    assert manifest["unique_commits"] is True
    assert manifest["unique_commit_bundle"]["head_oid"] == item.head_oid
    assert manifest["unique_commit_bundle"]["sha256"]


def test_managed_poisoned_origin_main_cannot_forge_ancestor_proof(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    authoritative_main_oid = subprocess.run(
        ["git", "rev-parse", "refs/remotes/origin/main"],
        cwd=managed,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (worktree / "tracked.txt").write_text("local-only managed commit\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "local only", cwd=worktree)
    local_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    poison = tmp_path / "attacker-origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(managed), str(poison)],
        check=True,
        capture_output=True,
        text=True,
    )
    _run("git", "--git-dir", str(poison), "update-ref", "refs/heads/main", local_oid, cwd=tmp_path)
    _run("git", "remote", "set-url", "origin", str(poison), cwd=managed)
    _run("git", "update-ref", "refs/remotes/origin/main", local_oid, cwd=managed)
    assert (
        subprocess.run(
            ["git", "ls-remote", "origin", "refs/heads/main"],
            cwd=managed,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()[0]
        == local_oid
    )
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        authoritative_main_verifier=lambda: RemoteProof(
            ok=True,
            kind="authoritative_main",
            detail={"headRefOid": authoritative_main_oid},
        ),
        branch_verifier=lambda branch: RemoteProof(
            ok=True,
            kind="remote_branch_absent",
            detail={"branch": branch, "headRefOid": None},
        ),
    )

    item = report.items[0]
    assert report.archived == 1
    assert report.removed == 1
    assert item.unique_commits
    assert item.main_oid == authoritative_main_oid
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "local_unique_commits"
    assert item.archive_path
    bundle_copy = tmp_path / "managed-poisoned-main.bundle"
    with tarfile.open(item.archive_path, "r:gz") as archive:
        bundle = archive.extractfile("unique-commits.bundle")
        assert bundle is not None
        bundle_copy.write_bytes(bundle.read())
    listed = subprocess.run(
        ["git", "bundle", "list-heads", str(bundle_copy)],
        cwd=managed,
        check=True,
        capture_output=True,
        text=True,
    )
    assert local_oid in listed.stdout


def test_managed_poisoned_origin_branch_cannot_forge_exact_remote_proof(
    tmp_path: Path,
) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    authoritative_main_oid = subprocess.run(
        ["git", "rev-parse", "refs/remotes/origin/main"],
        cwd=managed,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    (worktree / "tracked.txt").write_text("fake remote branch commit\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "fake published commit", cwd=worktree)
    local_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    poison = tmp_path / "attacker-branch-origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(managed), str(poison)],
        check=True,
        capture_output=True,
        text=True,
    )
    _run("git", "remote", "set-url", "origin", str(poison), cwd=managed)
    assert (
        subprocess.run(
            ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
            cwd=managed,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()[0]
        == local_oid
    )
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        authoritative_main_verifier=lambda: RemoteProof(
            ok=True,
            kind="authoritative_main",
            detail={"headRefOid": authoritative_main_oid},
        ),
        branch_verifier=lambda observed_branch: RemoteProof(
            ok=True,
            kind="remote_branch_absent",
            detail={"branch": observed_branch, "headRefOid": None},
        ),
    )

    item = report.items[0]
    assert report.archived == 1
    assert report.removed == 1
    assert item.unique_commits
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "local_unique_commits"
    assert item.remote_proof["detail"]["remote_oid"] is None


def test_managed_authoritative_lookup_failure_retains_worktree(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    _run(
        "git",
        "remote",
        "set-url",
        "origin",
        str(tmp_path / "child-controlled-missing.git"),
        cwd=managed,
    )
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        authoritative_main_verifier=lambda: RemoteProof(
            ok=False,
            kind="main_lookup_failed",
            detail={"repository": "colophon-group/jobseek"},
            error="trusted GitHub lookup failed",
        ),
        branch_verifier=lambda branch: (_ for _ in ()).throw(
            AssertionError("branch proof must not run without authoritative main")
        ),
    )

    item = report.items[0]
    assert report.archived == 0
    assert report.removed == 0
    assert worktree.exists()
    assert item.classification == "remote_unverified"
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "main_lookup_failed"
    assert item.reason == "trusted GitHub lookup failed"


def test_managed_authoritative_branch_failure_retains_worktree(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    authoritative_main_oid = subprocess.run(
        ["git", "rev-parse", "refs/remotes/origin/main"],
        cwd=managed,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        authoritative_main_verifier=lambda: RemoteProof(
            ok=True,
            kind="authoritative_main",
            detail={"headRefOid": authoritative_main_oid},
        ),
        branch_verifier=lambda branch: RemoteProof(
            ok=False,
            kind="branch_refresh_mismatch",
            detail={"branch": branch},
            error="trusted branch moved during refresh",
        ),
    )

    item = report.items[0]
    assert report.archived == 0
    assert report.removed == 0
    assert worktree.exists()
    assert item.classification == "remote_unverified"
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "branch_refresh_mismatch"
    assert item.reason == "trusted branch moved during refresh"


def test_managed_exact_remote_branch_is_proof_without_archive(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    (worktree / "tracked.txt").write_text("published commit\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "published", cwd=worktree)
    _run("git", "push", "origin", "HEAD", cwd=worktree)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=False)

    item = report.items[0]
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "exact_remote_branch"
    assert not item.unique_commits
    assert item.planned_action == "remove"


def test_managed_tree_equal_head_is_proof_without_archive(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    (worktree / "tracked.txt").write_text("equivalent change\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "local version", cwd=worktree)
    (managed / "tracked.txt").write_text("equivalent change\n")
    _run("git", "add", "tracked.txt", cwd=managed)
    _run("git", "commit", "-m", "landed version", cwd=managed)
    _run("git", "push", "origin", "main", cwd=managed)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=False)

    item = report.items[0]
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "tree_equal_main"
    assert not item.unique_commits
    assert item.planned_action == "remove"


def test_patch_id_whitespace_equivalence_still_requires_commit_bundle(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    (worktree / "tracked.txt").write_text("alpha beta\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "local whitespace", cwd=worktree)
    (managed / "tracked.txt").write_text("alpha  beta\n")
    _run("git", "add", "tracked.txt", cwd=managed)
    _run("git", "commit", "-m", "landed whitespace", cwd=managed)
    _run("git", "push", "origin", "main", cwd=managed)
    _run("git", "fetch", "origin", "main", cwd=managed)
    old_patch_proof = subprocess.run(
        ["git", "cherry", "origin/main", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert old_patch_proof and all(line.startswith("-") for line in old_patch_proof)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=True)

    item = report.items[0]
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "local_unique_commits"
    assert item.unique_commits
    assert item.archive_path


def test_merge_resolution_tree_omitted_by_git_cherry_is_bundled(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    (worktree / "tracked.txt").write_text("same patch\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "local shared change", cwd=worktree)
    _run("git", "checkout", "-b", "test-side", "origin/main", cwd=worktree)
    (worktree / "side.txt").write_text("same side patch\n")
    _run("git", "add", "side.txt", cwd=worktree)
    _run("git", "commit", "-m", "local side change", cwd=worktree)
    _run("git", "checkout", "fix-crawler/managed-worktree", cwd=worktree)
    _run("git", "merge", "--no-commit", "test-side", cwd=worktree)
    (worktree / "resolution-only.txt").write_text("unique merge resolution\n")
    _run("git", "add", "resolution-only.txt", cwd=worktree)
    _run("git", "commit", "-m", "merge with unique resolution", cwd=worktree)
    (managed / "tracked.txt").write_text("same patch\n")
    _run("git", "add", "tracked.txt", cwd=managed)
    _run("git", "commit", "-m", "landed shared change", cwd=managed)
    (managed / "side.txt").write_text("same side patch\n")
    _run("git", "add", "side.txt", cwd=managed)
    _run("git", "commit", "-m", "landed side change", cwd=managed)
    _run("git", "push", "origin", "main", cwd=managed)
    _run("git", "fetch", "origin", "main", cwd=managed)
    head_with_parents = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert len(head_with_parents) == 3
    old_patch_proof = subprocess.run(
        ["git", "cherry", "origin/main", "HEAD"],
        cwd=worktree,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert old_patch_proof and all(line.startswith("-") for line in old_patch_proof)
    tree_diff = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "origin/main", "--"],
        cwd=worktree,
        check=False,
    )
    assert tree_diff.returncode == 1
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(tmp_path, managed, worktrees, ledger, apply=True)

    item = report.items[0]
    assert item.remote_proof is not None
    assert item.remote_proof["kind"] == "local_unique_commits"
    assert item.unique_commits
    assert item.archive_path


def test_managed_broken_worktree_is_retained_and_counts_toward_bounds(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    (worktree / ".git").unlink()
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        max_directories=0,
    )

    assert worktree.exists()
    assert not report.within_bounds
    assert report.items[0].classification == "status_failed"
    assert ledger.worktree_reconciliation_events()[-1]["action"] == "retained"


def test_combined_report_enforces_one_bound_across_both_roots(tmp_path: Path) -> None:
    runner_repo, runner_worktree = _repo_with_worktree(tmp_path / "runner-side")
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    runner_report = _reconcile(
        tmp_path / "runner-side",
        runner_repo,
        ledger,
        apply=False,
        max_directories=1,
    )
    managed, worktrees, managed_worktree = _managed_repo_with_worktree(tmp_path / "managed-side")
    _run("git", "worktree", "lock", str(managed_worktree), cwd=managed)
    managed_report = _reconcile_managed(
        tmp_path / "managed-side",
        managed,
        worktrees,
        ledger,
        apply=False,
        max_directories=1,
    )

    report = combine_worktree_reports(
        [runner_report, managed_report],
        max_terminal_directories=1,
        max_terminal_bytes=10 * 1024**3,
    )

    assert runner_worktree.exists()
    assert report.remaining_terminal_directories == 2
    assert not report.within_bounds


def test_existing_worktree_quarantine_bytes_block_even_with_no_worktrees(
    tmp_path: Path,
) -> None:
    worktrees = tmp_path / "home" / ".jobseek" / "worktrees"
    worktrees.mkdir(parents=True)
    quarantine = tmp_path / "runner" / "state" / "worktree-quarantine"
    quarantine.mkdir(parents=True)
    (quarantine / "evidence.tar.gz").write_bytes(b"e" * 128)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(
        tmp_path,
        tmp_path / "home" / ".jobseek" / "repo",
        worktrees,
        ledger,
        apply=False,
        max_bytes=64,
    )

    assert report.directories == 0
    assert report.retained_worktree_bytes == 0
    assert report.quarantine_bytes >= 128
    assert report.remaining_terminal_bytes == report.quarantine_bytes
    assert not report.within_bounds


def test_new_unique_commit_archive_is_charged_to_byte_ceiling(tmp_path: Path) -> None:
    managed, worktrees, worktree = _managed_repo_with_worktree(tmp_path)
    (worktree / "tracked.txt").write_text("unique retained evidence\n")
    _run("git", "add", "tracked.txt", cwd=worktree)
    _run("git", "commit", "-m", "unique evidence", cwd=worktree)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")

    report = _reconcile_managed(
        tmp_path,
        managed,
        worktrees,
        ledger,
        apply=True,
        max_bytes=1,
    )

    assert report.removed == 0
    assert report.archived == 0
    assert report.removal_failures == 1
    assert report.remaining_terminal_directories == 1
    assert report.retained_worktree_bytes > 1
    assert report.quarantine_bytes == 0
    assert "capacity gate" in (report.items[0].error or "")
    assert worktree.exists()
    assert not report.within_bounds


def test_authoritative_main_uses_explicit_repository_and_matching_refresh(
    tmp_path: Path,
) -> None:
    oid = "a" * 40
    verifier = GitHubRemoteVerifier(
        repo_dir=tmp_path,
        github=SimpleNamespace(),
        repository="colophon-group/jobseek",
    )
    responses = [
        SimpleNamespace(returncode=0, stdout=f"{oid}\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout=f"{oid}\n", stderr=""),
    ]

    with patch(
        "src.workspace.worktree_reconcile.subprocess.run",
        side_effect=responses,
    ) as run:
        proof = verifier.verify_main()

    assert proof.ok
    assert proof.kind == "authoritative_main"
    assert proof.detail["headRefOid"] == oid
    assert run.call_args_list[0].args[0] == [
        "gh",
        "api",
        "--method",
        "GET",
        "repos/colophon-group/jobseek/commits/main",
        "--jq",
        ".sha",
    ]
    assert "https://github.com/colophon-group/jobseek.git" in run.call_args_list[1].args[0]


def test_authoritative_main_refresh_mismatch_fails_closed(tmp_path: Path) -> None:
    verifier = GitHubRemoteVerifier(
        repo_dir=tmp_path,
        github=SimpleNamespace(),
        repository="colophon-group/jobseek",
    )
    responses = [
        SimpleNamespace(returncode=0, stdout=f"{'a' * 40}\n", stderr=""),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout=f"{'b' * 40}\n", stderr=""),
    ]

    with patch(
        "src.workspace.worktree_reconcile.subprocess.run",
        side_effect=responses,
    ):
        proof = verifier.verify_main()

    assert not proof.ok
    assert proof.kind == "main_refresh_mismatch"
    assert proof.detail["headRefOid"] == "a" * 40
    assert proof.detail["refreshed_oid"] == "b" * 40


def test_authoritative_main_lookup_failure_fails_closed(tmp_path: Path) -> None:
    verifier = GitHubRemoteVerifier(
        repo_dir=tmp_path,
        github=SimpleNamespace(),
        repository="colophon-group/jobseek",
    )

    with patch(
        "src.workspace.worktree_reconcile.subprocess.run",
        return_value=SimpleNamespace(returncode=1, stdout="", stderr="GitHub unavailable"),
    ) as run:
        proof = verifier.verify_main()

    assert not proof.ok
    assert proof.kind == "main_lookup_failed"
    assert proof.error == "GitHub unavailable"
    assert run.call_count == 1


def test_authoritative_branch_refresh_mismatch_fails_closed(tmp_path: Path) -> None:
    verifier = GitHubRemoteVerifier(
        repo_dir=tmp_path,
        github=SimpleNamespace(),
        repository="colophon-group/jobseek",
    )
    branch = "fix-crawler/safe"
    expected_oid = "a" * 40
    responses = [
        SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "ref": f"refs/heads/{branch}",
                        "object": {"sha": expected_oid},
                    }
                ]
            ),
            stderr="",
        ),
        SimpleNamespace(returncode=0, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout=f"{'b' * 40}\n", stderr=""),
    ]

    with patch(
        "src.workspace.worktree_reconcile.subprocess.run",
        side_effect=responses,
    ):
        proof = verifier.verify_branch(branch, allow_absent=True)

    assert not proof.ok
    assert proof.kind == "branch_refresh_mismatch"
    assert proof.detail["headRefOid"] == expected_oid
    assert proof.detail["refreshed_oid"] == "b" * 40


@pytest.mark.parametrize("proof_source", ["pull_request", "branch"])
def test_remote_proof_ignores_poisoned_origin_repository(
    tmp_path: Path,
    proof_source: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _run("git", "init", "-b", "main", cwd=repo)
    _run(
        "git",
        "remote",
        "add",
        "origin",
        "https://github.com/attacker/fake-jobseek.git",
        cwd=repo,
    )
    oid = "a" * 40

    class GitHub:
        def issue_resolution(self, issue: int, *, repository: str):
            raise AssertionError("submitted PR proof must not require issue lookup")

    verifier = GitHubRemoteVerifier(
        repo_dir=repo,
        github=GitHub(),
        repository="colophon-group/jobseek",
    )
    if proof_source == "pull_request":
        responses = [
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "number": 7,
                        "state": "OPEN",
                        "isDraft": False,
                        "headRefName": "fix-crawler/safe",
                        "headRefOid": oid,
                        "mergedAt": None,
                        "url": "https://github.com/colophon-group/jobseek/pull/7",
                    }
                ),
                stderr="",
            )
        ]
        run = {
            "state": "submitted",
            "issue": 101,
            "pr_number": 7,
            "branch": "fix-crawler/safe",
        }
    else:
        responses = [
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "ref": "refs/heads/fix-crawler/safe",
                            "object": {"sha": oid},
                        }
                    ]
                ),
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout=f"{oid}\n", stderr=""),
        ]
        run = {
            "state": "failed",
            "issue": 101,
            "pr_number": None,
            "branch": "fix-crawler/safe",
        }

    with patch(
        "src.workspace.worktree_reconcile.subprocess.run",
        side_effect=responses,
    ) as subprocess_run:
        proof = verifier(run)

    command = subprocess_run.call_args_list[0].args[0]
    assert proof.ok
    assert proof.detail["headRefOid"] == oid
    assert "attacker/fake-jobseek" not in " ".join(command)
    assert "colophon-group/jobseek" in " ".join(command)


def test_submitted_outcome_uses_linked_pr_as_remote_proof(tmp_path: Path) -> None:
    class GitHub:
        def issue_resolution(self, issue: int, *, repository: str):
            raise AssertionError("an open submitted PR must not require issue closure")

    verifier = GitHubRemoteVerifier(
        repo_dir=tmp_path,
        github=GitHub(),
        repository="colophon-group/jobseek",
    )
    payload = {
        "number": 7,
        "state": "OPEN",
        "isDraft": False,
        "headRefName": "add-company/acme",
        "headRefOid": "abc123",
        "mergedAt": None,
        "url": "https://example.test/pr/7",
    }
    with patch(
        "src.workspace.worktree_reconcile.subprocess.run",
        return_value=SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    ):
        proof = verifier(
            {
                "state": "submitted",
                "issue": 101,
                "pr_number": 7,
                "branch": "add-company/acme",
            }
        )

    assert proof.ok
    assert proof.kind == "pull_request"
    assert proof.detail["headRefOid"] == "abc123"


def test_rejected_outcome_requires_matching_issue_marker(tmp_path: Path) -> None:
    class GitHub:
        def issue_resolution(self, issue: int, *, repository: str):
            assert repository == "colophon-group/jobseek"
            return SimpleNamespace(state="CLOSED", outcome="rejected")

    verifier = GitHubRemoteVerifier(
        repo_dir=tmp_path,
        github=GitHub(),
        repository="colophon-group/jobseek",
    )
    proof = verifier(
        {
            "state": "rejected",
            "issue": 101,
            "pr_number": None,
            "branch": None,
        }
    )

    assert proof.ok
    assert proof.kind == "issue_outcome"
    assert proof.detail["outcome"] == "rejected"
