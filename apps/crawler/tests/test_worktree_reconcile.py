from __future__ import annotations

import json
import os
import subprocess
import tarfile
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


def _reconcile(
    tmp_path: Path,
    repo: Path,
    ledger: RunnerLedger,
    *,
    apply: bool,
    verifier=None,
    remove_worktree=None,
    pre_remove=None,
    max_directories: int = 3,
    max_bytes: int = 10 * 1024**3,
):
    return reconcile_worktrees(
        root=tmp_path / "runner",
        repo_dir=repo,
        worktrees_dir=tmp_path / "runner" / "worktrees",
        archive_dir=tmp_path / "runner" / "state" / "worktree-quarantine",
        ledger=ledger,
        remote_verifier=verifier or (lambda run: RemoteProof(ok=True, kind="test")),
        pid_checker=lambda pid, run_id: False,
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
    max_directories: int = 3,
    max_bytes: int = 10 * 1024**3,
):
    return reconcile_managed_worktrees(
        repo_dir=managed,
        worktrees_dir=worktrees,
        archive_dir=tmp_path / "runner" / "state" / "worktree-quarantine",
        ledger=ledger,
        pid_checker=lambda pid, run_id: False,
        live_path_checker=live_path_checker or (lambda path: False),
        max_terminal_directories=max_directories,
        max_terminal_bytes=max_bytes,
        apply=apply,
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

    def fail_remove(path: Path) -> None:
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


def test_repeated_removal_failure_replaces_one_deterministic_archive(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo, worktree = _repo_with_worktree(tmp_path)
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")
    evidence = worktree / "unique-evidence.txt"
    evidence.write_text("preserve this evidence\n")
    monkeypatch.setattr(reconcile_module.time, "time", lambda: 1_800_000_000)

    def fail_remove(path: Path) -> None:
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
    first_bytes = archives[0].read_bytes()
    projected_bytes = first.items[0].bytes + 1024 * 1024
    replacement_aware_limit = quarantine.stat().st_size + projected_bytes
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
        max_bytes=replacement_aware_limit,
    )
    archives = list(quarantine.glob("*.tar.gz"))
    assert second.archived == 1
    assert second.removal_failures == 1
    assert len(archives) == 1
    assert archives[0].read_bytes() == first_bytes
    assert not stale_temp.exists()
    assert not stale_bundle.exists()
    with tarfile.open(archives[0], "r:gz") as archive:
        archived_evidence = archive.extractfile("untracked/unique-evidence.txt")
        assert archived_evidence is not None
        assert archived_evidence.read() == b"preserve this evidence\n"


def test_staging_recovery_preserves_files_owned_by_live_process(tmp_path: Path) -> None:
    quarantine = tmp_path / "worktree-quarantine"
    quarantine.mkdir()
    active = quarantine / f".active-run.{os.getpid()}.tmp"
    stale = quarantine / ".stale-run.99999999.bundle"
    active.write_bytes(b"active")
    stale.write_bytes(b"stale")

    reclaimed = reconcile_module._prune_stale_archive_staging(quarantine)

    assert reclaimed == len(b"stale")
    assert active.read_bytes() == b"active"
    assert not stale.exists()


def test_deleted_large_head_blob_cannot_overshoot_archive_capacity(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _run("git", "init", "-b", "main", cwd=repo)
    _run("git", "config", "user.name", "Test Runner", cwd=repo)
    _run("git", "config", "user.email", "runner@example.test", cwd=repo)
    (repo / "large.bin").write_bytes(os.urandom(4 * 1024 * 1024))
    _run("git", "add", "large.bin", cwd=repo)
    _run("git", "commit", "-m", "large base blob", cwd=repo)
    worktree = tmp_path / "runner" / "worktrees" / "run-worktree"
    worktree.parent.mkdir(parents=True)
    _run("git", "worktree", "add", "--detach", str(worktree), "HEAD", cwd=repo)
    (worktree / "large.bin").unlink()
    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    _terminal_run(ledger, worktree, state="retryable")

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


def test_submitted_outcome_uses_linked_pr_as_remote_proof(tmp_path: Path) -> None:
    class GitHub:
        def issue_resolution(self, issue: int):
            raise AssertionError("an open submitted PR must not require issue closure")

    verifier = GitHubRemoteVerifier(repo_dir=tmp_path, github=GitHub())
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
        def issue_resolution(self, issue: int):
            return SimpleNamespace(state="CLOSED", outcome="rejected")

    verifier = GitHubRemoteVerifier(repo_dir=tmp_path, github=GitHub())
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
