from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.workspace.codex_runner import RunnerLedger
from src.workspace.worktree_reconcile import (
    GitHubRemoteVerifier,
    RemoteProof,
    reconcile_worktrees,
)


def _run(*command: str, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)


def _repo_with_worktree(tmp_path: Path, name: str = "run-worktree") -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
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
    max_directories: int = 3,
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
        max_terminal_bytes=10 * 1024**3,
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
