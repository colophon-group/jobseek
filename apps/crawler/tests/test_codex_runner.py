from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.workspace import codex_runner as codex_runner_module
from src.workspace.codex_runner import (
    ClaimComment,
    CompanyResolverGovernor,
    GitHubCoordinator,
    GitHubStateError,
    IssueResolution,
    RunnerConfig,
    RunnerLedger,
    RunResult,
    UsageProbeResult,
    UsageSummary,
    UsageWindow,
    _safe_env,
    build_codex_command,
    build_codex_prompt,
    check_host_health,
    parse_codex_usage_jsonl,
    run_usage_probe,
)
from src.workspace.worktree_reconcile import GitHubRemoteVerifier, RemoteProof


class FakeGitHub:
    def __init__(
        self,
        *,
        issue: int | None = 101,
        issues: list[int] | None = None,
        claims_after_post: list[ClaimComment] | None = None,
        existing_prs: list[dict[str, str]] | None = None,
        issue_closed: bool = False,
        issue_outcome: str | None = None,
        fail_pr_lookup: bool = False,
        fail_claim_lookup: bool = False,
    ):
        self.issue = issue
        self.issues = issues if issues is not None else ([issue] if issue is not None else [])
        self.claims_after_post = claims_after_post
        self.existing_prs = existing_prs or []
        self.issue_closed = issue_closed
        self.issue_outcome = issue_outcome
        self.fail_pr_lookup = fail_pr_lookup
        self.fail_claim_lookup = fail_claim_lookup
        self.deleted: list[int] = []
        self.claimed: list[tuple[int, str, int]] = []
        self.pruned: list[tuple[str, int]] = []
        self.outcomes: list[tuple[int, str, str, str, int | None]] = []
        self._next_claim_id = 10

    def check_auth(self) -> bool:
        return True

    def fetch_oldest_open_issue(self, label: str) -> int | None:
        assert label == "company-request"
        return self.issue

    def list_open_issues(self, label: str) -> list[int]:
        assert label == "company-request"
        return self.issues

    def claim_issue(self, issue: int, run_id: str) -> int:
        claim_id = self._next_claim_id
        self._next_claim_id += 1
        self.claimed.append((issue, run_id, claim_id))
        return claim_id

    def check_existing_prs(self, issue: int) -> list[dict[str, str]]:
        if self.fail_pr_lookup:
            raise GitHubStateError("PR lookup failed")
        return self.existing_prs

    def issue_resolution(
        self,
        issue: int,
        *,
        repository: str | None = None,
    ) -> IssueResolution:
        if repository is not None:
            assert repository == "colophon-group/jobseek"
        return IssueResolution(
            state="CLOSED" if self.issue_closed else "OPEN",
            outcome=self.issue_outcome,
            reason=f"{self.issue_outcome} reason" if self.issue_outcome else None,
        )

    def record_run_outcome(
        self,
        issue: int,
        *,
        run_id: str,
        outcome: str,
        reason: str,
        retry_after_at: int | None = None,
    ) -> None:
        self.outcomes.append((issue, run_id, outcome, reason, retry_after_at))

    def list_claims(self, issue: int) -> list[ClaimComment]:
        if self.fail_claim_lookup:
            raise GitHubStateError("claim lookup failed")
        if self.claims_after_post is not None and self.claimed:
            return self.claims_after_post
        if not self.claimed:
            return []
        return [ClaimComment(id=self.claimed[-1][2], body="<!-- ws-claim -->\nours")]

    def delete_claim(self, comment_id: int) -> None:
        self.deleted.append(comment_id)

    def prune_stale_runner_claims(self, label: str, *, older_than_s: int) -> None:
        self.pruned.append((label, older_than_s))


def _config(tmp_path: Path, *, dry_run: bool = True) -> RunnerConfig:
    root = tmp_path / "runner"
    return RunnerConfig(
        root=root,
        dry_run=dry_run,
        codex_args=("python3", "-c", "print('{}')"),
        min_disk_free_gib=0,
        min_mem_available_gib=0,
        max_load_per_cpu=999,
    ).resolved()


def test_prompt_is_single_issue_and_does_not_pick() -> None:
    prompt = build_codex_prompt(123)

    assert "uv run ws task --issue 123" in prompt
    assert "Process only issue #123" in prompt
    assert "Do not run `ws task --pick`" in prompt
    assert "select another issue" in prompt
    assert "`ws task fail` enters coding mode" in prompt
    assert "unrecoverable `ws task fail`" not in prompt


def test_default_codex_args_pin_main_agent_model_policy() -> None:
    config = RunnerConfig.from_env({})

    assert config.codex_args == (
        "codex",
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
    )
    assert config.codex_model == "gpt-5.6-sol"
    assert config.codex_reasoning_effort == "high"
    assert config.trace_export_enabled
    assert config.trace_cleanup_enabled
    assert config.disk_alert_margin_gib == 2
    assert config.max_quarantine_runs == 50
    assert config.max_quarantine_gib == 2
    assert config.max_retained_session_files == 500
    assert config.max_retained_session_gib == 2
    assert config.max_unlinked_session_age_days == 7
    assert config.max_terminal_worktrees == 3
    assert config.max_terminal_worktree_gib == 2
    assert config.managed_repo_dir == Path.home() / ".jobseek" / "repo"
    assert config.managed_worktrees_dir == Path.home() / ".jobseek" / "worktrees"
    assert build_codex_command(config, "do the task") == [
        "codex",
        "exec",
        "--json",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model",
        "gpt-5.6-sol",
        "--config",
        "model_reasoning_effort=high",
        "do the task",
    ]


def test_managed_worktree_roots_can_be_overridden(tmp_path: Path) -> None:
    config = RunnerConfig.from_env(
        {
            "JOBSEEK_CODEX_RUNNER_ROOT": str(tmp_path / "runner"),
            "JOBSEEK_CODEX_MANAGED_REPO_DIR": str(tmp_path / "managed" / "repo"),
            "JOBSEEK_CODEX_MANAGED_WORKTREES_DIR": str(tmp_path / "managed" / "worktrees"),
        }
    )

    assert config.managed_repo_dir == tmp_path / "managed" / "repo"
    assert config.managed_worktrees_dir == tmp_path / "managed" / "worktrees"


def test_custom_runner_root_derives_managed_roots_when_env_omits_them(tmp_path: Path) -> None:
    root = tmp_path / "isolated-runner"

    config = RunnerConfig.from_env({"JOBSEEK_CODEX_RUNNER_ROOT": str(root)})

    assert config.managed_repo_dir == root / "managed" / "repo"
    assert config.managed_worktrees_dir == root / "managed" / "worktrees"


def test_terminal_trace_hook_records_verified_cleanup(monkeypatch, tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path / "runner",
        trace_export_enabled=True,
        trace_cleanup_enabled=True,
        codex_home=tmp_path / ".codex",
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    assert config.traces_dir is not None
    assert config.worktrees_dir is not None
    assert config.codex_home is not None
    trace_path = config.traces_dir / "run-1.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text('{"type":"turn.failed"}\n')
    worktree = config.worktrees_dir / "run-1"
    session_dir = config.codex_home / "sessions" / "2026" / "08" / "24"
    session_dir.mkdir(parents=True)
    root_session = session_dir / "root.jsonl"
    child_session = session_dir / "child.jsonl"
    root_session.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "root", "cwd": str(worktree), "source": "exec"},
            }
        )
        + "\n"
    )
    child_session.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {
                    "id": "child",
                    "cwd": str(worktree),
                    "source": {"subagent": {}},
                    "parent_thread_id": "root",
                    "agent_path": "/root/reviewer",
                },
            }
        )
        + "\n"
    )
    assert governor.ledger.acquire(run_id="run-1", issue=1, active_slot=config.active_slot)
    governor.ledger.update(
        "run-1",
        trace_path=str(trace_path),
        worktree_path=str(worktree),
    )
    governor.ledger.finish("run-1", "failed", error="exit 1")

    manifest = {
        "quality": {"tier": "silver"},
        "bundle_content_sha256": "abc",
    }
    monkeypatch.setattr("src.workspace.trace_backfill.build_bundle", lambda **kwargs: manifest)
    monkeypatch.setattr(
        "src.workspace.trace_backfill.upload_and_verify",
        lambda **kwargs: ("training-bundles/v2/silver/run-1", {"manifest.json": "abc"}),
    )
    events: list[str] = []
    monkeypatch.setattr(
        "src.workspace.trace_backfill.record_verified_export",
        lambda **kwargs: events.append("verified"),
    )
    cleaned: list[str] = []
    monkeypatch.setattr(
        "src.workspace.trace_backfill.cleanup_verified_sources",
        lambda **kwargs: (
            events.append("cleanup")
            or cleaned.append(kwargs["run_id"])
            or {"reclaimed_bytes": trace_path.stat().st_size}
        ),
    )
    monkeypatch.setattr(
        "src.workspace.trace_backfill.prune_hf_dataset_cache",
        lambda **kwargs: {"repo_id": kwargs["repo_id"], "revisions": 0, "reclaimed_bytes": 0},
    )

    result = governor._export_terminal_trace(
        RunResult(run_id="run-1", issue=1, state="failed", trace_path=trace_path)
    )

    assert result.state == "failed"
    assert result.trace_export_status == "cleaned"
    assert result.trace_export_tier == "silver"
    assert result.trace_export_remote_dir == "training-bundles/v2/silver/run-1"
    assert cleaned == ["run-1"]
    assert events == ["verified", "cleanup"]
    assert {
        (link["thread_id"], link["parent_thread_id"], link["role"], link["is_root"])
        for link in governor.ledger.codex_session_links("run-1")
    } == {
        ("root", None, "main", 1),
        ("child", "root", "reviewer", 0),
    }
    with governor.ledger._connect() as conn:
        attempt = conn.execute(
            "SELECT status, attempts FROM trace_bundle_export_attempts WHERE run_id = ?",
            ("run-1",),
        ).fetchone()
    assert dict(attempt) == {"status": "cleaned", "attempts": 1}


def test_failed_terminal_trace_export_is_retried(monkeypatch, tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path / "runner",
        trace_export_enabled=True,
        trace_cleanup_enabled=False,
        trace_retry_limit=1,
        codex_home=tmp_path / ".codex",
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    trace_path = config.traces_dir / "run-retry.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text('{"type":"turn.failed"}\n')
    assert governor.ledger.acquire(run_id="run-retry", issue=2, active_slot=config.active_slot)
    governor.ledger.update("run-retry", trace_path=str(trace_path))
    governor.ledger.finish("run-retry", "failed", error="exit 1")

    monkeypatch.setattr(
        "src.workspace.trace_backfill.build_bundle",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("temporary upload outage")),
    )
    first = governor._export_terminal_trace(
        RunResult(run_id="run-retry", issue=2, state="failed", trace_path=trace_path)
    )
    assert first.trace_export_status == "failed"
    assert trace_path.exists()

    manifest = {
        "quality": {"tier": "gold"},
        "bundle_content_sha256": "abc",
    }
    monkeypatch.setattr("src.workspace.trace_backfill.build_bundle", lambda **kwargs: manifest)
    monkeypatch.setattr(
        "src.workspace.trace_backfill.upload_and_verify",
        lambda **kwargs: ("training-bundles/v2/gold/run-retry", {}),
    )
    monkeypatch.setattr(
        "src.workspace.trace_backfill.record_verified_export", lambda **kwargs: None
    )

    governor._retry_failed_trace_exports()

    with governor.ledger._connect() as conn:
        attempt = conn.execute(
            "SELECT status, attempts FROM trace_bundle_export_attempts WHERE run_id = ?",
            ("run-retry",),
        ).fetchone()
    assert dict(attempt) == {"status": "verified", "attempts": 2}


def test_unattempted_terminal_automation_is_retried_after_process_death(
    monkeypatch, tmp_path: Path
) -> None:
    config = RunnerConfig(
        root=tmp_path / "runner",
        trace_export_enabled=True,
        trace_cleanup_enabled=False,
        trace_retry_limit=1,
        codex_home=tmp_path / ".codex",
    ).resolved()
    assert config.traces_dir is not None
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    run_id = "daily-error-review-2026-08-24-100-dead1234"
    trace_path = config.traces_dir / f"{run_id}.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"type":"turn.failed"}\n')
    assert governor.ledger.acquire(
        run_id=run_id,
        issue=None,
        active_slot="daily-error-review",
    )
    governor.ledger.update(run_id, trace_path=str(trace_path))
    governor.ledger.finish(run_id, "failed", error="process died before export")
    manifest = {
        "quality": {"tier": "gold"},
        "bundle_content_sha256": "abc",
    }
    built: list[str] = []
    monkeypatch.setattr(
        "src.workspace.trace_backfill.build_bundle",
        lambda **kwargs: built.append(kwargs["run_id"]) or manifest,
    )
    monkeypatch.setattr(
        "src.workspace.trace_backfill.upload_and_verify",
        lambda **kwargs: (f"training-bundles/v2/gold/{run_id}", {}),
    )
    monkeypatch.setattr(
        "src.workspace.trace_backfill.record_verified_export",
        lambda **kwargs: None,
    )

    governor._retry_failed_trace_exports()

    assert built == [run_id]
    with governor.ledger._connect() as conn:
        attempt = conn.execute(
            "SELECT status, attempts FROM trace_bundle_export_attempts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert dict(attempt) == {"status": "verified", "attempts": 1}


def test_unattempted_terminal_automation_is_selected_with_export_inventory(
    tmp_path: Path,
) -> None:
    from src.workspace.trace_backfill import record_verified_export

    ledger = RunnerLedger(tmp_path / "runner" / "state" / "ledger.sqlite")
    prior_run_id = "daily-error-review-2026-08-23-100-prior123"
    record_verified_export(
        ledger_path=ledger.path,
        run_id=prior_run_id,
        remote_dir=f"training-bundles/v2/gold/{prior_run_id}",
        manifest={
            "schema_version": "jobseek-codex-training-bundle/v2",
            "quality": {"tier": "gold"},
            "bundle_content_sha256": "prior",
            "thread_count": 0,
            "subagent_count": 0,
            "files": [],
        },
        verified={},
    )
    with ledger._connect() as conn:
        conn.execute(
            "UPDATE trace_bundle_exports SET cleaned_at = 1 WHERE run_id = ?",
            (prior_run_id,),
        )

    run_id = "daily-error-review-2026-08-24-100-dead1234"
    assert ledger.acquire(run_id=run_id, issue=None, active_slot="daily-error-review")
    ledger.finish(run_id, "failed", error="process died before export")

    retries = ledger.failed_trace_bundle_exports(
        limit=10,
        include_pending_cleanup=True,
    )

    assert [row["run_id"] for row in retries] == [run_id]


def test_pending_cleanup_retry_resumes_verified_inventory_without_rebuild(
    monkeypatch, tmp_path: Path
) -> None:
    from src.workspace.trace_backfill import record_verified_export

    config = RunnerConfig(
        root=tmp_path / "runner",
        trace_export_enabled=True,
        trace_cleanup_enabled=True,
        trace_retry_limit=1,
        codex_home=tmp_path / ".codex",
    ).resolved()
    assert config.traces_dir is not None
    assert config.codex_home is not None
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    run_id = "issue-7-100-retry123"
    trace_path = config.traces_dir / f"{run_id}.jsonl"
    trace_path.parent.mkdir(parents=True)
    trace_path.write_text('{"type":"turn.failed"}\n')
    root_session = config.codex_home / "sessions" / "2026" / "08" / "24" / "root.jsonl"
    root_session.parent.mkdir(parents=True)
    root_session.write_text('{"type":"session_meta"}\n')
    assert governor.ledger.acquire(run_id=run_id, issue=7, active_slot=config.active_slot)
    governor.ledger.update(run_id, trace_path=str(trace_path))
    governor.ledger.finish(run_id, "failed", error="exit 1")
    remote_dir = f"training-bundles/v2/gold/{run_id}"
    files = []
    verified = {}
    for bundle_path, role, source in (
        ("threads/main-root.jsonl", "main", root_session),
        ("codex-exec.jsonl", "codex_exec", trace_path),
    ):
        projected_sha = hashlib.sha256(bundle_path.encode()).hexdigest()
        files.append(
            {
                "path": bundle_path,
                "role": role,
                "sha256": projected_sha,
                "bytes": 1,
                "source_path": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_bytes": source.stat().st_size,
            }
        )
        verified[f"{remote_dir}/{bundle_path}"] = projected_sha
    manifest = {
        "schema_version": "jobseek-codex-training-bundle/v2",
        "quality": {"tier": "gold"},
        "bundle_content_sha256": "pending-cleanup",
        "thread_count": 1,
        "subagent_count": 0,
        "files": files,
    }
    record_verified_export(
        ledger_path=governor.ledger.path,
        run_id=run_id,
        remote_dir=remote_dir,
        manifest=manifest,
        verified=verified,
    )
    root_session.unlink()
    monkeypatch.setattr(
        "src.workspace.trace_backfill.build_bundle",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not rebuild")),
    )

    governor._retry_failed_trace_exports()

    assert not trace_path.exists()
    with governor.ledger._connect() as conn:
        export = conn.execute(
            "SELECT cleaned_at FROM trace_bundle_exports WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        attempt = conn.execute(
            "SELECT status, attempts FROM trace_bundle_export_attempts WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert export["cleaned_at"] is not None
    assert dict(attempt) == {"status": "cleaned", "attempts": 1}


def test_terminal_run_without_trace_is_accounted_for(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path / "runner",
        trace_export_enabled=True,
        codex_home=tmp_path / ".codex",
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    assert governor.ledger.acquire(run_id="run-missing", issue=3, active_slot=config.active_slot)
    governor.ledger.finish("run-missing", "interrupted", error="failed before Codex launch")

    result = governor._export_terminal_trace(
        RunResult(run_id="run-missing", issue=3, state="interrupted")
    )

    assert result.trace_export_status == "unavailable"
    assert result.trace_export_error == "terminal trace file unavailable"
    with governor.ledger._connect() as conn:
        attempt = conn.execute(
            "SELECT status, attempts FROM trace_bundle_export_attempts WHERE run_id = ?",
            ("run-missing",),
        ).fetchone()
    assert dict(attempt) == {"status": "unavailable", "attempts": 1}


def test_project_agents_pin_role_specific_model_policy() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    expected = {
        "jobseek-company-enricher.toml": ("gpt-5.6-terra", "medium"),
        "jobseek-logo-selector.toml": ("gpt-5.6-luna", "medium"),
        "jobseek-board-researcher.toml": ("gpt-5.6-terra", "high"),
        "jobseek-config-tester.toml": ("gpt-5.6-terra", "high"),
        "jobseek-error-review-researcher.toml": ("gpt-5.6-terra", "high"),
        "jobseek-labeller-normalizer.toml": ("gpt-5.6-luna", "low"),
        "jobseek-labeller-splitter.toml": ("gpt-5.6-luna", "medium"),
        "jobseek-labeller-extractor.toml": ("gpt-5.6-terra", "high"),
    }

    for filename, (model, effort) in expected.items():
        with (repo_root / ".codex" / "agents" / filename).open("rb") as handle:
            config = tomllib.load(handle)
        assert config["model"] == model
        assert config["model_reasoning_effort"] == effort


def test_ledger_allows_only_one_active_issue_and_slot(tmp_path: Path) -> None:
    ledger = RunnerLedger(tmp_path / "ledger.sqlite")

    assert ledger.acquire(run_id="run-1", issue=11, active_slot="company-resolver")
    assert not ledger.acquire(run_id="run-2", issue=12, active_slot="company-resolver")
    assert not ledger.acquire(run_id="run-3", issue=11, active_slot="other-slot")

    ledger.finish("run-1", "completed")

    assert ledger.acquire(run_id="run-4", issue=12, active_slot="company-resolver")


def test_ledger_recovers_expired_active_rows(tmp_path: Path) -> None:
    ledger = RunnerLedger(tmp_path / "ledger.sqlite")
    assert ledger.acquire(
        run_id="expired",
        issue=11,
        active_slot="company-resolver",
        lease_expires_at=1,
    )

    expired = ledger.expired_active_runs(active_slot="company-resolver", now=2)

    assert len(expired) == 1
    assert expired[0]["run_id"] == "expired"


def test_two_governors_racing_same_ledger_only_one_claims(tmp_path: Path) -> None:
    config = _config(tmp_path)
    ledger = RunnerLedger(config.ledger_path)
    first_gh = FakeGitHub(issue=101)
    second_gh = FakeGitHub(issue=101)

    first = CompanyResolverGovernor(config, ledger=ledger, github=first_gh)
    second = CompanyResolverGovernor(config, ledger=ledger, github=second_gh)

    first_admission = first.admit_one()
    second_admission = second.admit_one()

    assert first_admission is not None
    assert first_admission.issue == 101
    assert second_admission is None
    assert len(first_gh.claimed) == 1
    assert second_gh.claimed == []
    assert first_gh.pruned == [("company-request", config.lease_timeout_s)]


def test_lost_cross_host_claim_race_deletes_only_own_claim(tmp_path: Path) -> None:
    config = _config(tmp_path)
    github = FakeGitHub(
        issue=101,
        claims_after_post=[
            ClaimComment(id=5, body="<!-- ws-claim -->\nother"),
            ClaimComment(id=10, body="<!-- ws-claim -->\nours"),
        ],
    )
    governor = CompanyResolverGovernor(config, github=github)

    assert governor.admit_one() is None
    assert github.deleted == [10]

    run = governor.ledger.get_run(github.claimed[0][1])
    assert run is not None
    assert run["state"] == "skipped"
    assert run["error"] == "lost claim race"


def test_pr_appearing_after_claim_releases_own_claim(tmp_path: Path) -> None:
    config = _config(tmp_path)
    github = FakeGitHub(issue=101)
    governor = CompanyResolverGovernor(config, github=github)
    calls = 0

    def check_existing_prs(issue: int) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return []
        return [
            {
                "number": 7,
                "url": "https://example/pr/7",
                "headRefName": "add-company/acme",
                "isDraft": False,
            }
        ]

    github.check_existing_prs = check_existing_prs  # type: ignore[method-assign]

    assert governor.admit_one() is None
    assert github.deleted == [10]

    run = governor.ledger.get_run(github.claimed[0][1])
    assert run is not None
    assert run["state"] == "skipped"
    assert run["error"] == "linked PR became submitted before launch"


def test_existing_draft_is_not_admitted_for_cross_run_takeover(tmp_path: Path) -> None:
    config = _config(tmp_path)
    github = FakeGitHub(
        issue=101,
        existing_prs=[
            {
                "number": "7",
                "url": "https://example/pr/7",
                "headRefName": "add-company/acme",
                "isDraft": True,
            }
        ],
    )
    governor = CompanyResolverGovernor(config, github=github)

    admission = governor.admit_one()

    assert admission is None
    assert github.claimed == []


def test_submitted_pr_does_not_block_later_candidate(tmp_path: Path) -> None:
    config = _config(tmp_path)
    github = FakeGitHub(issue=None, issues=[101, 102])

    def check_existing_prs(issue: int) -> list[dict[str, object]]:
        if issue == 101:
            return [
                {
                    "number": 7,
                    "headRefName": "add-company/acme",
                    "isDraft": False,
                }
            ]
        return []

    github.check_existing_prs = check_existing_prs  # type: ignore[method-assign]
    governor = CompanyResolverGovernor(config, github=github)

    admission = governor.admit_one()

    assert admission is not None
    assert admission.issue == 102


def test_claimed_issue_does_not_block_later_candidate(tmp_path: Path) -> None:
    config = _config(tmp_path)
    github = FakeGitHub(issue=None, issues=[101, 102])
    github.list_claims = (  # type: ignore[method-assign]
        lambda issue: [ClaimComment(id=5, body="<!-- ws-claim -->\nother")] if issue == 101 else []
    )
    governor = CompanyResolverGovernor(config, github=github)

    admission = governor.admit_one()

    assert admission is not None
    assert admission.issue == 102


def test_backed_off_issue_does_not_block_later_candidate(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path)
    ledger = RunnerLedger(config.ledger_path)
    monkeypatch.setattr("src.workspace.codex_runner.time.time", lambda: 1_000)
    assert ledger.acquire(
        run_id="first",
        issue=101,
        active_slot=config.active_slot,
        attempt=1,
    )
    ledger.finish(
        "first",
        "retryable",
        outcome_reason="capacity",
        retry_after_at=2_000,
    )
    governor = CompanyResolverGovernor(
        config,
        ledger=ledger,
        github=FakeGitHub(issue=None, issues=[101, 102]),
    )

    admission = governor.admit_one()

    assert admission is not None
    assert admission.issue == 102


def test_retry_backoff_is_bounded_exponential(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path / "runner",
        retry_backoff_s=10,
        max_retry_backoff_s=25,
    ).resolved()
    ledger = RunnerLedger(config.ledger_path)
    governor = CompanyResolverGovernor(config, ledger=ledger, github=FakeGitHub(issue=None))
    expected = [10, 20, 25, 25]

    for attempt, delay in enumerate(expected, start=1):
        run_id = f"attempt-{attempt}"
        assert ledger.acquire(
            run_id=run_id,
            issue=101,
            active_slot=config.active_slot,
            attempt=attempt,
        )
        assert governor._retry_delay(run_id) == delay
        ledger.finish(run_id, "retryable")


def test_unknown_claim_state_fails_closed_before_posting_claim(tmp_path: Path) -> None:
    config = _config(tmp_path)
    github = FakeGitHub(issue=101, fail_claim_lookup=True)
    governor = CompanyResolverGovernor(config, github=github)

    assert governor.admit_one() is None

    assert github.claimed == []
    assert github.deleted == []


def test_github_claim_listing_flattens_paginated_comments(monkeypatch) -> None:
    from src.workspace import git

    captured_cmd: list[str] = []

    def fake_run(cmd, **kwargs):
        captured_cmd.extend(cmd)
        return SimpleNamespace(
            returncode=0,
            stderr="",
            stdout=json.dumps(
                [
                    [
                        {"id": 1, "body": "ordinary comment"},
                        {"id": 2, "body": "<!-- ws-claim -->\nfirst"},
                    ],
                    [{"id": 3, "body": "<!-- ws-claim -->\nsecond"}],
                ]
            ),
        )

    monkeypatch.setattr(git, "_resolve_repo", lambda: "owner/repo")
    monkeypatch.setattr(git, "_run", fake_run)

    claims = GitHubCoordinator().list_claims(101)

    assert "--paginate" in captured_cmd
    assert "--slurp" in captured_cmd
    assert [claim.id for claim in claims] == [2, 3]


def test_github_prunes_only_old_runner_owned_claims(monkeypatch) -> None:
    from src.workspace import git

    deleted: list[int] = []

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "issue", "list"]:
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps([{"number": 101}]))
        if cmd[:3] == ["gh", "api", "--paginate"]:
            return SimpleNamespace(
                returncode=0,
                stderr="",
                stdout=json.dumps(
                    [
                        [
                            {
                                "id": 1,
                                "created_at": "2026-07-09T00:00:00Z",
                                "body": "<!-- ws-claim -->\nmanual claim",
                            },
                            {
                                "id": 2,
                                "created_at": "2026-07-09T00:00:00Z",
                                "body": "<!-- ws-claim -->\nWorking\nrun: issue-101-old",
                            },
                            {
                                "id": 3,
                                "created_at": "2099-07-09T00:00:00Z",
                                "body": "<!-- ws-claim -->\nWorking\nrun: issue-101-new",
                            },
                        ]
                    ]
                ),
            )
        if cmd[:4] == ["gh", "api", "--method", "DELETE"]:
            deleted.append(int(cmd[4].rsplit("/", 1)[-1]))
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        raise AssertionError(cmd)

    monkeypatch.setattr("src.workspace.codex_runner.time.time", lambda: 1783560000)
    monkeypatch.setattr(git, "_gh_repo_flag", lambda: [])
    monkeypatch.setattr(git, "_resolve_repo", lambda: "owner/repo")
    monkeypatch.setattr(git, "_run", fake_run)

    GitHubCoordinator().prune_stale_runner_claims("company-request", older_than_s=3600)

    assert deleted == [2]


def test_issue_closure_requires_terminal_evidence(monkeypatch) -> None:
    from src.workspace import git

    commands: list[list[str]] = []
    payloads = iter(
        [
            {"state": "CLOSED", "comments": [], "closedByPullRequestsReferences": []},
            {
                "state": "CLOSED",
                "comments": [
                    {
                        "body": "<!-- validation-failed: no-job-board -->\n"
                        "No supported board was found"
                    }
                ],
                "closedByPullRequestsReferences": [],
            },
            {
                "state": "CLOSED",
                "comments": [],
                "closedByPullRequestsReferences": [{"number": 7}],
            },
        ]
    )

    def fake_run(cmd, **kwargs):
        commands.append(cmd)
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(next(payloads)))

    monkeypatch.setattr(git, "_run", fake_run)
    coordinator = GitHubCoordinator()

    assert coordinator.issue_resolution(101).outcome is None
    assert coordinator.issue_resolution(101).outcome == "rejected"
    assert (
        coordinator.issue_resolution(
            101,
            repository="colophon-group/jobseek",
        ).outcome
        == "submitted"
    )
    assert commands[-1][4:6] == ["--repo", "colophon-group/jobseek"]


def test_dry_run_claims_then_releases_without_codex(tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    github = FakeGitHub(issue=101)
    governor = CompanyResolverGovernor(config, github=github)

    result = governor.run_once()

    assert result.state == "skipped"
    assert result.issue == 101
    assert github.deleted == [10]


def test_terminal_trace_export_precedes_worktree_cleanup(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=101))
    calls: list[str] = []
    admission = SimpleNamespace(run_id="issue-101-1-aaaaaaaa", issue=101, claim_comment_id=10)
    worktree = config.worktrees_dir / "run"

    monkeypatch.setattr(governor, "_retry_failed_trace_exports", lambda: None)
    monkeypatch.setattr(
        governor,
        "should_start",
        lambda: SimpleNamespace(should_run=True),
    )
    monkeypatch.setattr(governor, "admit_one", lambda: admission)
    monkeypatch.setattr(
        governor,
        "_execute_admission",
        lambda _admission: RunResult(
            run_id=admission.run_id,
            issue=admission.issue,
            state="submitted",
            worktree_path=worktree,
        ),
    )

    def export(result: RunResult) -> RunResult:
        calls.append("export")
        result.trace_export_status = "cleaned"
        return result

    def cleanup(result: RunResult) -> None:
        assert result.trace_export_status == "cleaned"
        calls.append("cleanup")

    monkeypatch.setattr(governor, "_export_terminal_trace", export)
    monkeypatch.setattr(governor, "_cleanup_terminal_worktree", cleanup)

    result = governor.run_once()

    assert result.state == "submitted"
    assert calls == ["export", "cleanup"]


def test_unknown_usage_uses_conservative_five_hour_budget(tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    ledger = RunnerLedger(config.ledger_path)
    for i in range(5):
        run_id = f"old-{i}"
        assert ledger.acquire(run_id=run_id, issue=i + 1, active_slot=config.active_slot)
        ledger.finish(run_id, "completed")
    governor = CompanyResolverGovernor(config, ledger=ledger, github=FakeGitHub(issue=101))

    decision = governor.should_start()

    assert not decision.should_run
    assert decision.reason == "five-hour run budget exhausted"
    assert decision.recent_limit == 5
    assert decision.recent_runs == 5


def test_weekly_usage_over_fast_threshold_expands_budget(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=101))
    usage = UsageProbeResult(
        ok=True,
        windows=(
            UsageWindow(name="five_hour", remaining_percent=90, reset_in_seconds=3600),
            UsageWindow(name="weekly", remaining_percent=55, reset_in_seconds=None),
        ),
    )
    monkeypatch.setattr(governor, "_probe_usage", lambda: usage)

    decision = governor.should_start()

    assert decision.should_run
    assert decision.recent_limit == 50


def test_weekly_usage_under_fast_threshold_uses_conservative_budget(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(tmp_path, dry_run=True)
    ledger = RunnerLedger(config.ledger_path)
    for i in range(5):
        run_id = f"old-{i}"
        assert ledger.acquire(run_id=run_id, issue=i + 1, active_slot=config.active_slot)
        ledger.finish(run_id, "completed")
    governor = CompanyResolverGovernor(config, ledger=ledger, github=FakeGitHub(issue=101))
    usage = UsageProbeResult(
        ok=True,
        windows=(
            UsageWindow(name="five_hour", remaining_percent=90, reset_in_seconds=3600),
            UsageWindow(name="weekly", remaining_percent=49, reset_in_seconds=3600),
        ),
    )
    monkeypatch.setattr(governor, "_probe_usage", lambda: usage)

    decision = governor.should_start()

    assert not decision.should_run
    assert decision.reason == "five-hour run budget exhausted"
    assert decision.recent_limit == 5
    assert decision.recent_runs == 5


def test_scheduler_records_usage_snapshots(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    ledger = RunnerLedger(config.ledger_path)
    governor = CompanyResolverGovernor(config, ledger=ledger, github=FakeGitHub(issue=101))
    usage = UsageProbeResult(
        ok=True,
        windows=(
            UsageWindow(
                name="five_hour",
                remaining_percent=90,
                used_percent=10,
                reset_in_seconds=3600,
            ),
            UsageWindow(
                name="weekly",
                remaining_percent=55,
                used_percent=45,
                reset_in_seconds=ONE_DAY,
            ),
        ),
    )
    monkeypatch.setattr(governor, "_probe_usage", lambda: usage)

    decision = governor.should_start()
    snapshots = ledger.recent_usage_snapshots(active_slot=config.active_slot, limit=10)

    assert decision.should_run
    assert len(snapshots) == 2
    by_window = {snapshot["window_name"]: snapshot for snapshot in snapshots}
    assert by_window["weekly"]["remaining_percent"] == 55
    assert by_window["weekly"]["used_percent"] == 45
    assert by_window["weekly"]["recent_limit"] == 50
    assert by_window["weekly"]["decision_reason"] == "admitted"
    assert by_window["weekly"]["pacing_interval_s"] == 360
    assert by_window["five_hour"]["reset_in_seconds"] == 3600


def test_fast_mode_paces_starts_between_timer_wakes(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    ledger = RunnerLedger(config.ledger_path)
    monkeypatch.setattr("src.workspace.codex_runner.time.time", lambda: 1000)
    assert ledger.acquire(run_id="old", issue=1, active_slot=config.active_slot)
    ledger.update("old", state="completed", started_at=1000, completed_at=1001)
    governor = CompanyResolverGovernor(config, ledger=ledger, github=FakeGitHub(issue=101))
    usage = UsageProbeResult(
        ok=True,
        windows=(
            UsageWindow(name="five_hour", remaining_percent=90, reset_in_seconds=3600),
            UsageWindow(name="weekly", remaining_percent=80, reset_in_seconds=ONE_DAY),
        ),
    )
    monkeypatch.setattr(governor, "_probe_usage", lambda: usage)
    monkeypatch.setattr("src.workspace.codex_runner.time.time", lambda: 1060)

    decision = governor.should_start()
    snapshots = ledger.recent_usage_snapshots(active_slot=config.active_slot, limit=10)

    assert not decision.should_run
    assert decision.reason == "start pacing interval active"
    assert decision.retry_after_s == 300
    assert decision.pacing_interval_s == 360
    assert decision.last_started_at == 1000
    assert {snapshot["retry_after_s"] for snapshot in snapshots} == {300}


def test_low_usage_window_pauses_until_reset(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=101))
    usage = UsageProbeResult(
        ok=True,
        windows=(
            UsageWindow(name="five_hour", remaining_percent=1, reset_in_seconds=123),
            UsageWindow(name="weekly", remaining_percent=80, reset_in_seconds=ONE_DAY),
        ),
    )
    monkeypatch.setattr(governor, "_probe_usage", lambda: usage)

    decision = governor.should_start()

    assert not decision.should_run
    assert decision.reason == "Codex usage window below threshold"
    assert decision.retry_after_s == 123


def test_weekly_usage_below_twenty_percent_hard_blocks(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=101))
    usage = UsageProbeResult(
        ok=True,
        windows=(
            UsageWindow(name="five_hour", remaining_percent=90, reset_in_seconds=3600),
            UsageWindow(name="weekly", remaining_percent=19, reset_in_seconds=ONE_DAY),
        ),
    )
    monkeypatch.setattr(governor, "_probe_usage", lambda: usage)

    decision = governor.should_start()

    assert not decision.should_run
    assert decision.reason == "Codex usage window below threshold"
    assert decision.retry_after_s == ONE_DAY


def test_low_usage_without_reset_uses_fallback_retry(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=101))
    usage = UsageProbeResult(
        ok=True,
        windows=(UsageWindow(name="five_hour", remaining_percent=1, reset_in_seconds=None),),
    )
    monkeypatch.setattr(governor, "_probe_usage", lambda: usage)

    decision = governor.should_start()

    assert not decision.should_run
    assert decision.retry_after_s == 30 * 60


def test_live_codex_host_health_requires_git_identity(monkeypatch, tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        dry_run=False,
        codex_args=("codex", "exec"),
        min_disk_free_gib=0,
        min_mem_available_gib=0,
        max_load_per_cpu=999,
    ).resolved()

    monkeypatch.setattr("src.workspace.codex_runner._mem_available_gib", lambda: 99)
    monkeypatch.setattr("src.workspace.codex_runner.os.getloadavg", lambda: (0, 0, 0))
    monkeypatch.setattr("src.workspace.codex_runner._missing_git_identity", lambda: ["user.name"])

    health = check_host_health(config)

    assert not health.ok
    assert health.reason == "git identity missing: user.name"


def test_dry_run_host_health_does_not_require_git_identity(monkeypatch, tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path,
        dry_run=True,
        codex_args=("codex", "exec"),
        min_disk_free_gib=0,
        min_mem_available_gib=0,
        max_load_per_cpu=999,
    ).resolved()

    monkeypatch.setattr("src.workspace.codex_runner._mem_available_gib", lambda: 99)
    monkeypatch.setattr("src.workspace.codex_runner.os.getloadavg", lambda: (0, 0, 0))
    monkeypatch.setattr(
        "src.workspace.codex_runner._missing_git_identity",
        lambda: (_ for _ in ()).throw(AssertionError("should not check git identity")),
    )

    health = check_host_health(config)

    assert health.ok


def test_host_health_warns_before_disk_admission_floor(monkeypatch, tmp_path: Path) -> None:
    gib = 1024**3
    config = RunnerConfig(
        root=tmp_path,
        dry_run=True,
        min_disk_free_gib=5,
        disk_alert_margin_gib=2,
        min_mem_available_gib=0,
        max_load_per_cpu=999,
    ).resolved()
    monkeypatch.setattr(
        "src.workspace.codex_runner.shutil.disk_usage",
        lambda path: SimpleNamespace(total=20 * gib, used=14 * gib, free=6 * gib),
    )
    monkeypatch.setattr("src.workspace.codex_runner._mem_available_gib", lambda: 99)
    monkeypatch.setattr("src.workspace.codex_runner.os.getloadavg", lambda: (0, 0, 0))

    health = check_host_health(config)

    assert health.ok
    assert health.warning == "disk free 6.0GiB is within 2.0GiB of the admission floor"
    assert health.disk_free_bytes == 6 * gib


def test_quarantine_limit_blocks_new_admission(tmp_path: Path) -> None:
    config = RunnerConfig(
        root=tmp_path / "runner",
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
        min_disk_free_gib=0,
        min_mem_available_gib=0,
        max_load_per_cpu=999,
        max_quarantine_runs=1,
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=101))
    governor.ledger.record_trace_bundle_attempt(
        "issue-1-1-aaaaaaaa",
        status="quarantined",
        retained_bytes=123,
    )

    decision = governor.should_start()

    assert not decision.should_run
    assert decision.reason == "trace quarantine retention limit reached: 1 runs, 123 bytes"


def test_unsafe_session_entry_blocks_new_admission(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.codex_home is not None
    session_dir = config.codex_home / "sessions" / "2026" / "08" / "24"
    session_dir.mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("outside\n")
    (session_dir / "unsafe.jsonl").symlink_to(outside)
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))

    decision = governor.should_start()

    assert not decision.should_run
    assert decision.reason == "unsafe Codex session entries retained: 1"


def test_terminal_worktree_limit_blocks_new_admission(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=101))
    monkeypatch.setattr(
        governor,
        "reconcile_worktrees",
        lambda *, apply: SimpleNamespace(
            within_bounds=False,
            remaining_terminal_directories=4,
            remaining_terminal_bytes=3 * 1024**3,
        ),
    )

    decision = governor.should_start()

    assert not decision.should_run
    assert decision.reason == (
        "terminal worktree retention limit reached: 4 directories, 3221225472 bytes"
    )


def test_worktree_quarantine_bytes_block_admission_without_leftover_directories(
    tmp_path: Path,
) -> None:
    config = RunnerConfig(
        root=tmp_path / "runner",
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
        min_disk_free_gib=0,
        min_mem_available_gib=0,
        max_load_per_cpu=999,
        max_terminal_worktree_gib=0,
    ).resolved()
    assert config.state_dir is not None
    quarantine = config.state_dir / "worktree-quarantine"
    quarantine.mkdir(parents=True)
    (quarantine / "durable-evidence.tar.gz").write_bytes(b"evidence")
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=101))

    decision = governor.should_start()

    assert not decision.should_run
    assert decision.reason.startswith("terminal worktree retention limit reached: 0 directories, ")
    assert decision.reason != "terminal worktree retention limit reached: 0 directories, 0 bytes"


def test_managed_worktree_context_joins_workspace_to_latest_issue_run(tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    outer = config.worktrees_dir / "company-request-101-run-new"
    workspace = outer / "apps" / "crawler" / ".workspace" / "acme"
    managed = config.managed_worktrees_dir / "acme"
    workspace.mkdir(parents=True)
    managed.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text(
        f"slug: acme\ngit:\n  issue: 101\n  pr: 7\n  branch: add-company/acme\n"
        f"  worktree: {managed}\n"
    )
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    assert governor.ledger.acquire(
        run_id="run-old",
        issue=101,
        active_slot=config.active_slot,
    )
    governor.ledger.update("run-old", worktree_path=str(outer))
    governor.ledger.finish("run-old", "retryable")
    assert governor.ledger.acquire(
        run_id="run-new",
        issue=101,
        active_slot=config.active_slot,
    )
    governor.ledger.update("run-new", worktree_path=str(outer))
    with governor.ledger._connect() as conn:
        conn.execute("UPDATE runs SET updated_at = 1 WHERE run_id = 'run-old'")
        conn.execute("UPDATE runs SET updated_at = 2 WHERE run_id = 'run-new'")

    contexts = governor._managed_worktree_contexts()

    assert contexts[str(managed.resolve())]["run_id"] == "run-new"
    assert contexts[str(managed.resolve())]["issue"] == 101


def test_cleanup_uses_configured_managed_repo_root(tmp_path: Path) -> None:
    custom_managed_repo = tmp_path / "custom-managed" / "repo"
    config = RunnerConfig(
        root=tmp_path / "runner",
        managed_repo_dir=custom_managed_repo,
        managed_worktrees_dir=tmp_path / "custom-managed" / "worktrees",
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    workspace = custom_managed_repo / "apps" / "crawler" / ".workspace" / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n  worktree: ''\n")
    active = workspace.parent / "active.scope-test-run"
    active.write_text("acme\n")
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))

    governor._cleanup_ws_artifacts_for_issue(101)

    assert not workspace.exists()
    assert not active.exists()


def test_cleanup_removes_only_exact_authenticated_run_scope_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    workspace_root.mkdir(parents=True)
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    run_id = "issue-101-1787997096-d0e1bd7d"
    pointer = json.dumps(
        {"version": 1, "slug": "acme", "generation": "a" * 32},
        sort_keys=True,
        separators=(",", ":"),
    )
    workspace = workspace_root / "acme"
    workspace.mkdir()
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n  worktree: ''\n")
    exact = workspace_root / f"active.scope-{run_id}"
    other = workspace_root / "active.scope-issue-102-1787997096-eeeeeeee"
    exact.write_text(pointer)
    other.write_text(pointer)

    governor._cleanup_ws_artifacts_for_issue(101, run_id=run_id)

    assert not exact.exists()
    assert not workspace.exists()
    assert other.read_text() == pointer

    workspace.mkdir()
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n  worktree: ''\n")
    exact.write_text('{"version":1,"slug":"acme","generation":"invalid"}')
    with pytest.raises(RuntimeError, match="marker is unauthenticated"):
        governor._cleanup_ws_artifacts_for_issue(101, run_id=run_id)

    assert exact.exists()
    assert workspace.exists()


def test_cleanup_retains_mismatched_run_scope_marker(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    workspace = workspace_root / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n  worktree: ''\n")
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    run_id = "issue-101-1787997096-d0e1bd7d"
    marker = workspace_root / f"active.scope-{run_id}"
    marker.write_text(json.dumps({"version": 1, "slug": "other", "generation": "a" * 32}))

    with pytest.raises(RuntimeError, match="marker is unauthenticated"):
        governor._cleanup_ws_artifacts_for_issue(101, run_id=run_id)

    assert marker.exists()
    assert workspace.exists()


def test_cleanup_retains_marker_when_workspace_slug_binding_is_invalid(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    workspace = workspace_root / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: other\ngit:\n  issue: 101\n  worktree: ''\n")
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    run_id = "issue-101-1787997096-d0e1bd7d"
    marker = workspace_root / f"active.scope-{run_id}"
    marker.write_text(json.dumps({"version": 1, "slug": "acme", "generation": "a" * 32}))

    with pytest.raises(RuntimeError, match="metadata slug"):
        governor._cleanup_ws_artifacts_for_issue(101, run_id=run_id)

    assert marker.exists()
    assert workspace.exists()


def test_cleanup_does_not_follow_run_scope_marker_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    workspace_root.mkdir(parents=True)
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    run_id = "issue-101-1787997096-d0e1bd7d"
    workspace = workspace_root / "acme"
    workspace.mkdir()
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n  worktree: ''\n")
    external = tmp_path / "external-pointer"
    external.write_text(json.dumps({"version": 1, "slug": "acme", "generation": "a" * 32}))
    marker = workspace_root / f"active.scope-{run_id}"
    marker.symlink_to(external)

    with pytest.raises(RuntimeError, match="marker is unsafe"):
        governor._cleanup_ws_artifacts_for_issue(101, run_id=run_id)

    assert marker.is_symlink()
    assert external.exists()
    assert workspace.exists()


def test_cleanup_retains_hardlinked_run_scope_marker_and_workspace(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    workspace = workspace_root / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n  worktree: ''\n")
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    run_id = "issue-101-1787997096-d0e1bd7d"
    external = tmp_path / "external-pointer"
    external.write_text(json.dumps({"version": 1, "slug": "acme", "generation": "a" * 32}))
    marker = workspace_root / f"active.scope-{run_id}"
    os.link(external, marker)

    with pytest.raises(RuntimeError, match="marker is unauthenticated"):
        governor._cleanup_ws_artifacts_for_issue(101, run_id=run_id)

    assert marker.exists()
    assert external.exists()
    assert workspace.exists()


def test_cleanup_marker_precedes_workspace_removal_and_is_retryable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    workspace = workspace_root / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n  worktree: ''\n")
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    run_id = "issue-101-1787997096-d0e1bd7d"
    marker = workspace_root / f"active.scope-{run_id}"
    marker.write_text(json.dumps({"version": 1, "slug": "acme", "generation": "a" * 32}))
    original_rmtree = codex_runner_module.rmtree_child_at

    def interrupt_removal(*_args, **_kwargs):
        raise RuntimeError("simulated interruption")

    monkeypatch.setattr(codex_runner_module, "rmtree_child_at", interrupt_removal)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        governor._cleanup_ws_artifacts_for_issue(101, run_id=run_id)

    assert not marker.exists()
    assert workspace.exists()
    assert (workspace / "workspace.yaml").exists()

    monkeypatch.setattr(codex_runner_module, "rmtree_child_at", original_rmtree)
    governor._cleanup_ws_artifacts_for_issue(101, run_id=run_id)

    assert not workspace.exists()


def _write_terminal_lifecycle_receipts(
    workspace_root: Path,
    *,
    issue: int,
    slug: str = "acme",
) -> Path:
    terminal = workspace_root / ".terminal-lifecycle"
    terminal.mkdir(parents=True)
    journal_id = "a" * 32
    locator = {
        "version": 1,
        "slug": slug,
        "journal_id": journal_id,
        "issue": issue,
    }
    journal = {
        "version": 1,
        "journal_id": journal_id,
        "slug": slug,
        "branch": f"add-company/{slug}",
        "issue": issue,
        "pr": None,
        "pr_provenance": {},
        "expected_remote_oid": None,
        "worktree": None,
        "worktree_head": None,
        "worktree_dev": None,
        "worktree_ino": None,
        "local_branch_oid": None,
        "data_cleanup_required": False,
        "data_initially_present": False,
        "workspace_was_present": True,
        "active_entries": [],
        "claim_initially_present": False,
        "outcome": None,
        "attempts": {
            "remote_delete": False,
            "pr_close": False,
            "issue_comment": False,
            "issue_labels": False,
            "issue_close": False,
            "worktree_remove": True,
            "local_branch_remove": True,
            "data_remove": False,
            "active_clear": True,
            "workspace_remove": True,
        },
    }
    (terminal / f"{slug}.latest-receipt").write_text(json.dumps(locator))
    (terminal / f"{slug}.{journal_id}.completed.yaml").write_text(json.dumps(journal))
    return terminal


def test_cleanup_clears_completed_terminal_lifecycle_receipts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    terminal = _write_terminal_lifecycle_receipts(workspace_root, issue=101)
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))

    governor._cleanup_ws_artifacts_for_issue(
        101,
        run_id="issue-101-1787997096-d0e1bd7d",
        workspace_root=workspace_root,
        workspace_container=repo,
    )

    assert terminal.is_dir()
    assert list(terminal.iterdir()) == []


def test_cleanup_retains_foreign_terminal_lifecycle_receipts(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    terminal = _write_terminal_lifecycle_receipts(workspace_root, issue=102)
    before = {path.name: path.read_text() for path in terminal.iterdir()}
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))

    with pytest.raises(RuntimeError, match="belongs to another run"):
        governor._cleanup_ws_artifacts_for_issue(
            101,
            run_id="issue-101-1787997096-d0e1bd7d",
            workspace_root=workspace_root,
            workspace_container=repo,
        )

    assert {path.name: path.read_text() for path in terminal.iterdir()} == before


def test_cleanup_retains_pending_terminal_lifecycle_evidence(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    terminal = _write_terminal_lifecycle_receipts(workspace_root, issue=101)
    pending = terminal / "acme.pending.yaml"
    pending.write_text(json.dumps({"issue": 101}))
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))

    with pytest.raises(RuntimeError, match="incomplete or unknown evidence"):
        governor._cleanup_ws_artifacts_for_issue(
            101,
            run_id="issue-101-1787997096-d0e1bd7d",
            workspace_root=workspace_root,
            workspace_container=repo,
        )

    assert pending.exists()
    assert len(list(terminal.iterdir())) == 3


def test_terminal_lifecycle_receipt_cleanup_is_retryable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    terminal = _write_terminal_lifecycle_receipts(workspace_root, issue=101)
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))
    original_unlink = codex_runner_module.unlink_claimed_child_at
    calls = 0

    def interrupt_second_unlink(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated receipt cleanup interruption")
        return original_unlink(*args, **kwargs)

    monkeypatch.setattr(
        codex_runner_module,
        "unlink_claimed_child_at",
        interrupt_second_unlink,
    )
    with pytest.raises(RuntimeError, match="simulated receipt cleanup interruption"):
        governor._cleanup_ws_artifacts_for_issue(
            101,
            run_id="issue-101-1787997096-d0e1bd7d",
            workspace_root=workspace_root,
            workspace_container=repo,
        )

    remaining = list(terminal.iterdir())
    assert len(remaining) == 1
    assert remaining[0].name.startswith(".jobseek-terminal-receipt-v1-")
    monkeypatch.setattr(codex_runner_module, "unlink_claimed_child_at", original_unlink)
    governor._cleanup_ws_artifacts_for_issue(
        101,
        run_id="issue-101-1787997096-d0e1bd7d",
        workspace_root=workspace_root,
        workspace_container=repo,
    )
    assert list(terminal.iterdir()) == []


def test_cleanup_rejects_oversized_terminal_receipt_before_yaml_parse(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    terminal = _write_terminal_lifecycle_receipts(workspace_root, issue=101)
    locator = terminal / "acme.latest-receipt"
    locator.write_bytes(b"x" * (64 * 1024 + 1))
    safe_load_called = False

    def fail_if_parsed(_raw):
        nonlocal safe_load_called
        safe_load_called = True
        raise AssertionError("oversized receipt reached YAML parser")

    monkeypatch.setattr("yaml.safe_load", fail_if_parsed)

    terminal_fd = os.open(terminal, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(RuntimeError, match="unauthenticated"):
            codex_runner_module._read_terminal_receipt_at(
                terminal_fd,
                locator.name,
            )
    finally:
        os.close(terminal_fd)

    assert safe_load_called is False
    assert locator.exists()


def test_cleanup_rejects_terminal_receipt_fifo_without_blocking(tmp_path: Path) -> None:
    workspace_root = tmp_path / "repo" / "apps" / "crawler" / ".workspace"
    terminal = workspace_root / ".terminal-lifecycle"
    terminal.mkdir(parents=True)
    fifo = terminal / "acme.latest-receipt"
    os.mkfifo(fifo)

    terminal_fd = os.open(terminal, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(RuntimeError, match="unauthenticated"):
            codex_runner_module._read_terminal_receipt_at(terminal_fd, fifo.name)
    finally:
        os.close(terminal_fd)

    assert fifo.exists()


def test_cleanup_rejects_noncanonical_terminal_locator_slug(tmp_path: Path) -> None:
    workspace_root = tmp_path / "repo" / "apps" / "crawler" / ".workspace"
    terminal = _write_terminal_lifecycle_receipts(
        workspace_root,
        issue=101,
        slug="acme",
    )
    locator = terminal / "acme.latest-receipt"
    data = json.loads(locator.read_text())
    data["slug"] = "!!!"
    malformed = terminal / "!!!.latest-receipt"
    malformed.write_text(json.dumps(data))
    locator.unlink()

    with pytest.raises(RuntimeError, match="locator is invalid"):
        codex_runner_module._cleanup_terminal_lifecycle_receipts(
            workspace_root,
            issue=101,
        )

    assert malformed.exists()


def test_cleanup_clears_completed_issue_only_terminal_receipt(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    workspace_root = repo / "apps" / "crawler" / ".workspace"
    issues = workspace_root / ".terminal-lifecycle" / "issues"
    issues.mkdir(parents=True)
    receipt = {
        "version": 1,
        "namespace": "issue",
        "issue": 101,
        "outcome": {
            "marker": "<!-- terminal -->",
            "body": "Rejected",
            "labels": ["rejected"],
            "close_issue": True,
        },
        "claim_initially_present": True,
        "attempts": {
            "issue_comment": True,
            "issue_labels": True,
            "issue_close": True,
        },
    }
    (issues / "101.completed.yaml").write_text(json.dumps(receipt))
    config = RunnerConfig(
        root=tmp_path / "runner",
        repo_dir=repo,
        dry_run=True,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=None))

    governor._cleanup_ws_artifacts_for_issue(
        101,
        run_id="issue-101-1787997096-d0e1bd7d",
        workspace_root=workspace_root,
        workspace_container=repo,
    )

    assert issues.is_dir()
    assert list(issues.iterdir()) == []


def test_reconcile_pre_remove_does_not_follow_workspace_symlink(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, dry_run=True)
    assert config.repo_dir is not None
    assert config.worktrees_dir is not None
    repo = config.repo_dir
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "runner@example.test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )
    main_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(
        GitHubRemoteVerifier,
        "verify_main",
        lambda self: RemoteProof(
            ok=True,
            kind="authoritative_main",
            detail={"headRefOid": main_oid},
        ),
    )
    config.worktrees_dir.mkdir(parents=True)
    worktree = config.worktrees_dir / "terminal"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    external_root = tmp_path / "external-workspace"
    victim = external_root / "victim"
    victim.mkdir(parents=True)
    (victim / "workspace.yaml").write_text("slug: victim\ngit:\n  issue: 101\n  worktree: ''\n")
    secret = victim / "credential.txt"
    secret.write_text("external-secret\n")
    workspace_root = worktree / "apps" / "crawler" / ".workspace"
    workspace_root.parent.mkdir(parents=True)
    workspace_root.symlink_to(external_root, target_is_directory=True)
    github = FakeGitHub(issue=101, issue_closed=True, issue_outcome="rejected")
    governor = CompanyResolverGovernor(config, github=github)
    assert governor.ledger.acquire(
        run_id="issue-101-1-aaaaaaaa",
        issue=101,
        active_slot=config.active_slot,
    )
    governor.ledger.update("issue-101-1-aaaaaaaa", worktree_path=str(worktree))
    governor.ledger.finish("issue-101-1-aaaaaaaa", "rejected")

    report = governor.reconcile_worktrees(apply=True)

    assert report.removed == 1
    assert not worktree.exists()
    assert victim.exists()
    assert secret.read_text() == "external-secret\n"


def test_reconcile_cleanup_stays_anchored_when_workspace_root_is_swapped(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.workspace import safe_cleanup

    config = _config(tmp_path, dry_run=True)
    assert config.repo_dir is not None
    assert config.worktrees_dir is not None
    repo = config.repo_dir
    repo.mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "runner@example.test"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True)
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", "HEAD"],
        cwd=repo,
        check=True,
    )
    main_oid = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    monkeypatch.setattr(
        GitHubRemoteVerifier,
        "verify_main",
        lambda self: RemoteProof(
            ok=True,
            kind="authoritative_main",
            detail={"headRefOid": main_oid},
        ),
    )
    config.worktrees_dir.mkdir(parents=True)
    worktree = config.worktrees_dir / "terminal-swap"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(worktree), "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    workspace_root = worktree / "apps" / "crawler" / ".workspace"
    workspace = workspace_root / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text("slug: acme\ngit:\n  issue: 101\n  worktree: ''\n")
    (workspace_root / "active").write_text("acme\n")
    original_workspace = workspace_root / "acme-original"
    original_open_root = codex_runner_module.open_absolute_directory_no_follow
    original_open_child = codex_runner_module.open_child_directory_no_follow
    original_rename = os.rename
    opened_fds: list[tuple[int, int, int]] = []
    swapped = False

    def track_root_fd(path: Path) -> int:
        descriptor = original_open_root(path)
        opened = os.fstat(descriptor)
        opened_fds.append((descriptor, opened.st_dev, opened.st_ino))
        return descriptor

    def track_child_fd(parent_fd: int, name: str):
        result = original_open_child(parent_fd, name)
        opened_fds.append((result[0], result[1].st_dev, result[1].st_ino))
        return result

    def swap_child_before_claim(src, dst, *args, **kwargs):
        nonlocal swapped
        if src == "acme" and not swapped:
            original_rename(workspace, original_workspace)
            workspace.mkdir()
            (workspace / "replacement.txt").write_text("preserve replacement\n")
            swapped = True
        return original_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(
        codex_runner_module,
        "open_absolute_directory_no_follow",
        track_root_fd,
    )
    monkeypatch.setattr(
        codex_runner_module,
        "open_child_directory_no_follow",
        track_child_fd,
    )
    monkeypatch.setattr(safe_cleanup.os, "rename", swap_child_before_claim)
    github = FakeGitHub(issue=101, issue_closed=True, issue_outcome="rejected")
    governor = CompanyResolverGovernor(config, github=github)
    assert governor.ledger.acquire(
        run_id="issue-101-1-aaaaaaaa",
        issue=101,
        active_slot=config.active_slot,
    )
    governor.ledger.update("issue-101-1-aaaaaaaa", worktree_path=str(worktree))
    governor.ledger.finish("issue-101-1-aaaaaaaa", "rejected")

    report = governor.reconcile_worktrees(apply=True)

    assert swapped
    assert report.removed == 0
    assert report.removal_failures == 1
    assert report.remaining_terminal_directories == 1
    assert report.reclaimed_bytes == 0
    assert worktree.exists()
    assert (workspace / "replacement.txt").read_text() == "preserve replacement\n"
    assert (original_workspace / "workspace.yaml").exists()
    assert (workspace_root / "active").read_text() == "acme\n"
    assert opened_fds
    for descriptor, expected_dev, expected_ino in opened_fds:
        try:
            current = os.fstat(descriptor)
        except OSError:
            continue
        assert (current.st_dev, current.st_ino) != (expected_dev, expected_ino), (
            f"workspace cleanup leaked descriptor {descriptor}"
        )


def test_active_marker_replacement_is_preserved_at_claim(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from src.workspace import safe_cleanup

    workspace_root = tmp_path / ".workspace"
    workspace_root.mkdir()
    marker = workspace_root / "active"
    marker.write_text("acme\n")
    original_marker = workspace_root / "active-original"
    original_rename = os.rename
    swapped = False

    def swap_marker_before_claim(src, dst, *args, **kwargs):
        nonlocal swapped
        if src == "active" and not swapped:
            original_rename(marker, original_marker)
            marker.write_text("replacement-marker\n")
            swapped = True
        return original_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(safe_cleanup.os, "rename", swap_marker_before_claim)
    root_fd = codex_runner_module.open_absolute_directory_no_follow(workspace_root)
    try:
        codex_runner_module._cleanup_active_markers_at(root_fd, "acme")
    finally:
        os.close(root_fd)

    assert swapped
    assert marker.read_text() == "replacement-marker\n"
    assert original_marker.read_text() == "acme\n"


def test_safe_env_excludes_unneeded_secrets() -> None:
    env = _safe_env(
        {
            "PATH": "/usr/bin",
            "GH_TOKEN": "github",
            "HF_TOKEN": "hf",
            "HUGGINGFACE_HUB_TOKEN": "hf2",
            "CRAWLER_DATABASE_URL": "postgres://secret",
            "WS_REPO": "owner/repo",
        }
    )

    assert env == {"PATH": "/usr/bin", "GH_TOKEN": "github", "WS_REPO": "owner/repo"}


def test_usage_probe_transport_and_schema_failures_are_nonfatal(tmp_path: Path) -> None:
    script = tmp_path / "probe.py"
    script.write_text("print('not json')\n")

    result = run_usage_probe(script, python="python3")

    assert not result.ok
    assert "invalid JSON" in (result.error or "")


def test_usage_probe_normalizes_success(monkeypatch, tmp_path: Path) -> None:
    payload = {
        "ok": True,
        "windows": [
            {
                "name": "weekly",
                "remaining_percent": 42.5,
                "used_percent": 57.5,
                "reset_in_seconds": 3600,
            }
        ],
    }

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    with patch("src.workspace.codex_runner.subprocess.run", side_effect=fake_run):
        result = run_usage_probe(tmp_path / "probe.py")

    assert result.ok
    assert len(result.windows) == 1
    assert result.windows[0].name == "weekly"
    assert result.windows[0].remaining_percent == 42.5


def test_non_ok_usage_probe_reset_pauses_scheduler(monkeypatch, tmp_path: Path) -> None:
    payload = {"ok": False, "status": 429, "resets_in_seconds": 321}

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout=json.dumps(payload))

    with patch("src.workspace.codex_runner.subprocess.run", side_effect=fake_run):
        usage = run_usage_probe(tmp_path / "probe.py")

    assert not usage.ok
    assert usage.windows[0].reset_in_seconds == 321

    config = _config(tmp_path, dry_run=True)
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=101))
    monkeypatch.setattr(governor, "_probe_usage", lambda: usage)

    decision = governor.should_start()

    assert not decision.should_run
    assert decision.reason == "Codex usage window below threshold"
    assert decision.retry_after_s == 321


def test_parse_codex_usage_jsonl_and_deduplicate_ingestion(tmp_path: Path) -> None:
    trace = tmp_path / "run.jsonl"
    trace.write_text(
        "\n".join(
            [
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10}}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "usage": {"input_tokens": 1, "output_tokens": 1},
                        "item": {
                            "usage": {
                                "prompt_tokens": 3,
                                "completion_tokens": 4,
                                "cached_prompt_tokens": 2,
                            }
                        },
                    }
                ),
                "{bad json",
            ]
        )
    )
    summary = parse_codex_usage_jsonl(trace)

    assert summary == UsageSummary(
        input_tokens=11,
        output_tokens=1,
        cached_input_tokens=0,
        events_with_usage=2,
    )

    ledger = RunnerLedger(tmp_path / "ledger.sqlite")
    assert ledger.ingest_trace_once("run-1", trace, summary)
    assert not ledger.ingest_trace_once("run-1", trace, summary)


def test_timeout_is_interrupted_with_backoff_and_retains_trace(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    repo = tmp_path / "repo"
    crawler = repo / "apps" / "crawler"
    crawler.mkdir(parents=True)
    config = RunnerConfig(
        root=config.root,
        repo_dir=repo,
        max_runtime_s=1,
        kill_grace_s=1,
        dry_run=False,
        codex_args=("python3", "-c", "import time; time.sleep(30)"),
    ).resolved()
    github = FakeGitHub(issue=101)
    governor = CompanyResolverGovernor(config, github=github)

    monkeypatch.setattr(
        governor,
        "_prepare_worktree",
        lambda admission: repo,
    )

    result = governor.run_once()

    assert result.state == "interrupted"
    assert result.trace_path is not None
    assert result.trace_path.exists()
    run = governor.ledger.get_run(result.run_id)
    assert run is not None
    assert run["state"] == "interrupted"
    assert run["retry_after_at"] is not None
    assert run["outcome_reason"] == "codex runtime exceeded"


def test_failed_run_is_retryable_and_releases_own_claim(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    repo = tmp_path / "repo"
    (repo / "apps" / "crawler").mkdir(parents=True)
    config = RunnerConfig(
        root=config.root,
        repo_dir=repo,
        max_runtime_s=5,
        dry_run=False,
        codex_args=("python3", "-c", "import sys; sys.exit(2)"),
    ).resolved()
    github = FakeGitHub(issue=101)
    governor = CompanyResolverGovernor(config, github=github)
    monkeypatch.setattr(governor, "_prepare_worktree", lambda admission: repo)

    result = governor.run_once()

    assert result.state == "retryable"
    assert result.exit_code == 2
    assert github.deleted == [10]


def test_codex_process_runs_under_shared_worktree_execution_lease(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, dry_run=False)
    repo = tmp_path / "repo"
    (repo / "apps" / "crawler").mkdir(parents=True)
    lock_glob = config.ledger_path.parent / "worktree-execution-leases" / "*.lock"
    script = (
        "import fcntl, glob; "
        f"path = glob.glob({str(lock_glob)!r})[0]; "
        "handle = open(path, 'rb'); "
        "\ntry:\n"
        " fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "except BlockingIOError:\n print('shared-lease-held')\n"
        "else:\n print('exclusive-lease-acquired')\n"
    )
    config = RunnerConfig(
        root=config.root,
        repo_dir=repo,
        max_runtime_s=5,
        dry_run=False,
        codex_args=("python3", "-c", script),
    ).resolved()
    governor = CompanyResolverGovernor(config, github=FakeGitHub(issue=101))
    monkeypatch.setattr(governor, "_prepare_worktree", lambda admission: repo)

    result = governor.run_once()

    assert result.trace_path is not None
    assert "shared-lease-held" in result.trace_path.read_text()
    assert "exclusive-lease-acquired" not in result.trace_path.read_text()


def test_zero_exit_without_terminal_outcome_is_retryable(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    repo = tmp_path / "repo"
    (repo / "apps" / "crawler").mkdir(parents=True)
    config = RunnerConfig(
        root=config.root,
        repo_dir=repo,
        max_runtime_s=5,
        dry_run=False,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    github = FakeGitHub(issue=101)
    governor = CompanyResolverGovernor(config, github=github)
    monkeypatch.setattr(governor, "_prepare_worktree", lambda admission: repo)

    result = governor.run_once()

    assert result.state == "retryable"
    assert result.exit_code == 0
    assert github.deleted == [10]
    run = governor.ledger.get_run(result.run_id)
    assert run is not None
    assert run["error"] == "codex exited 0 without a terminal ws outcome"
    assert run["retry_after_at"] is not None


def test_zero_exit_with_pr_but_no_ws_completion_is_retryable(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    repo = tmp_path / "repo"
    (repo / "apps" / "crawler").mkdir(parents=True)
    config = RunnerConfig(
        root=config.root,
        repo_dir=repo,
        max_runtime_s=5,
        dry_run=False,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    github = FakeGitHub(
        issue=101,
    )
    governor = CompanyResolverGovernor(config, github=github)
    monkeypatch.setattr(governor, "_prepare_worktree", lambda admission: repo)
    calls = 0

    def check_existing_prs(issue: int) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls < 3:
            return []
        return [
            {
                "number": 7,
                "url": "https://example/pr/7",
                "headRefName": "add-company/x",
                "isDraft": True,
            }
        ]

    monkeypatch.setattr(github, "check_existing_prs", check_existing_prs)

    result = governor.run_once()

    assert result.state == "retryable"
    assert result.exit_code == 0
    assert github.deleted == [10]
    run = governor.ledger.get_run(result.run_id)
    assert run is not None
    assert run["pr_number"] == 7


def test_zero_exit_with_ready_pr_and_ws_completion_is_submitted(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(tmp_path, dry_run=False)
    repo = tmp_path / "repo"
    workspace = repo / "apps" / "crawler" / ".workspace" / "acme"
    workspace.mkdir(parents=True)
    (workspace / "workspace.yaml").write_text(
        "slug: acme\ngit:\n  issue: 101\n  pr: 7\n  worktree: ''\n"
    )
    (workspace / "workflow.state.yaml").write_text("current_step: done\n")
    config = RunnerConfig(
        root=config.root,
        repo_dir=repo,
        max_runtime_s=5,
        dry_run=False,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    github = FakeGitHub(issue=101)
    governor = CompanyResolverGovernor(config, github=github)
    monkeypatch.setattr(governor, "_prepare_worktree", lambda admission: repo)
    calls = 0

    def check_existing_prs(issue: int) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls < 3:
            return []
        return [
            {
                "number": 7,
                "url": "https://example/pr/7",
                "headRefName": "add-company/x",
                "isDraft": False,
            }
        ]

    monkeypatch.setattr(github, "check_existing_prs", check_existing_prs)

    result = governor.run_once()

    assert result.state == "submitted"
    assert result.exit_code == 0
    assert github.deleted == []
    run = governor.ledger.get_run(result.run_id)
    assert run is not None
    assert run["pr_number"] == 7


def test_coding_mode_fix_pr_is_submitted_without_ws_completion(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    repo = tmp_path / "repo"
    (repo / "apps" / "crawler").mkdir(parents=True)
    config = RunnerConfig(
        root=config.root,
        repo_dir=repo,
        max_runtime_s=5,
        dry_run=False,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    github = FakeGitHub(issue=101)
    governor = CompanyResolverGovernor(config, github=github)
    monkeypatch.setattr(governor, "_prepare_worktree", lambda admission: repo)
    calls = 0

    def check_existing_prs(issue: int) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        if calls < 3:
            return []
        return [
            {
                "number": 8,
                "url": "https://example/pr/8",
                "headRefName": "fix-crawler/authenticated-board",
                "isDraft": True,
            }
        ]

    github.check_existing_prs = check_existing_prs  # type: ignore[method-assign]

    result = governor.run_once()

    assert result.state == "submitted"
    assert github.deleted == []


def test_closed_issue_without_terminal_marker_is_retryable(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    repo = tmp_path / "repo"
    (repo / "apps" / "crawler").mkdir(parents=True)
    config = RunnerConfig(
        root=config.root,
        repo_dir=repo,
        max_runtime_s=5,
        dry_run=False,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    github = FakeGitHub(issue=101, issue_closed=True)
    governor = CompanyResolverGovernor(config, github=github)
    monkeypatch.setattr(governor, "_prepare_worktree", lambda admission: repo)

    result = governor.run_once()

    assert result.state == "retryable"
    assert github.deleted == [10]


def test_closed_issue_with_rejection_marker_is_rejected(monkeypatch, tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=False)
    repo = tmp_path / "repo"
    (repo / "apps" / "crawler").mkdir(parents=True)
    config = RunnerConfig(
        root=config.root,
        repo_dir=repo,
        max_runtime_s=5,
        dry_run=False,
        codex_args=("python3", "-c", "print('{}')"),
    ).resolved()
    github = FakeGitHub(issue=101, issue_closed=True, issue_outcome="rejected")
    governor = CompanyResolverGovernor(config, github=github)
    monkeypatch.setattr(governor, "_prepare_worktree", lambda admission: repo)

    result = governor.run_once()

    assert result.state == "rejected"
    assert github.deleted == []


def test_stale_lease_with_reused_pid_is_interrupted_and_released(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(tmp_path, dry_run=True)
    ledger = RunnerLedger(config.ledger_path)
    assert ledger.acquire(
        run_id="stale",
        issue=101,
        active_slot=config.active_slot,
        lease_expires_at=1,
    )
    ledger.update("stale", pid=123, claim_comment_id=10)
    github = FakeGitHub(issue=None)
    governor = CompanyResolverGovernor(config, ledger=ledger, github=github)
    monkeypatch.setattr("src.workspace.codex_runner._pid_matches_run", lambda pid, run_id: False)

    governor.reconcile_stale_runs()

    run = ledger.get_run("stale")
    assert run is not None
    assert run["state"] == "interrupted"
    assert github.deleted == [10]


def test_exception_after_claim_is_interrupted_and_releases_when_unresolved(
    monkeypatch, tmp_path: Path
) -> None:
    config = _config(tmp_path, dry_run=False)
    github = FakeGitHub(issue=101)
    governor = CompanyResolverGovernor(config, github=github)
    monkeypatch.setattr(
        governor,
        "_prepare_worktree",
        lambda admission: (_ for _ in ()).throw(RuntimeError("worktree failed")),
    )

    result = governor.run_once()

    assert result.state == "interrupted"
    assert result.error == "worktree failed"
    assert github.deleted == [10]
    run = governor.ledger.get_run(result.run_id)
    assert run is not None
    assert run["state"] == "interrupted"


ONE_DAY = 24 * 60 * 60
