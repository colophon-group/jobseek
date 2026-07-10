from __future__ import annotations

import json
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.workspace.codex_runner import (
    ClaimComment,
    CompanyResolverGovernor,
    GitHubCoordinator,
    GitHubStateError,
    RunnerConfig,
    RunnerLedger,
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


class FakeGitHub:
    def __init__(
        self,
        *,
        issue: int | None = 101,
        claims_after_post: list[ClaimComment] | None = None,
        existing_prs: list[dict[str, str]] | None = None,
        issue_closed: bool = False,
        fail_pr_lookup: bool = False,
        fail_claim_lookup: bool = False,
    ):
        self.issue = issue
        self.claims_after_post = claims_after_post
        self.existing_prs = existing_prs or []
        self.issue_closed = issue_closed
        self.fail_pr_lookup = fail_pr_lookup
        self.fail_claim_lookup = fail_claim_lookup
        self.deleted: list[int] = []
        self.claimed: list[tuple[int, str, int]] = []
        self.pruned: list[tuple[str, int]] = []
        self._next_claim_id = 10

    def check_auth(self) -> bool:
        return True

    def fetch_oldest_open_issue(self, label: str) -> int | None:
        assert label == "company-request"
        return self.issue

    def claim_issue(self, issue: int, run_id: str) -> int:
        claim_id = self._next_claim_id
        self._next_claim_id += 1
        self.claimed.append((issue, run_id, claim_id))
        return claim_id

    def check_existing_prs(self, issue: int) -> list[dict[str, str]]:
        if self.fail_pr_lookup:
            raise GitHubStateError("PR lookup failed")
        return self.existing_prs

    def issue_is_closed(self, issue: int) -> bool:
        return self.issue_closed

    def list_claims(self, issue: int) -> list[ClaimComment]:
        if self.fail_claim_lookup:
            raise GitHubStateError("claim lookup failed")
        if self.claims_after_post is not None:
            return self.claims_after_post
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
    github = FakeGitHub(issue=101, existing_prs=[{"number": "7", "url": "https://example/pr/7"}])
    governor = CompanyResolverGovernor(config, github=github)

    assert governor.admit_one() is None
    assert github.deleted == [10]

    run = governor.ledger.get_run(github.claimed[0][1])
    assert run is not None
    assert run["state"] == "skipped"
    assert run["error"] == "open PR appeared before launch"


def test_unknown_github_state_after_claim_fails_closed_and_releases_claim(tmp_path: Path) -> None:
    config = _config(tmp_path)
    github = FakeGitHub(issue=101, fail_claim_lookup=True)
    governor = CompanyResolverGovernor(config, github=github)

    assert governor.admit_one() is None

    assert github.deleted == [10]
    run = governor.ledger.get_run(github.claimed[0][1])
    assert run is not None
    assert run["state"] == "skipped"
    assert "claim lookup failed" in run["error"]


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


def test_dry_run_claims_then_releases_without_codex(tmp_path: Path) -> None:
    config = _config(tmp_path, dry_run=True)
    github = FakeGitHub(issue=101)
    governor = CompanyResolverGovernor(config, github=github)

    result = governor.run_once()

    assert result.state == "skipped"
    assert result.issue == 101
    assert github.deleted == [10]


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


def test_timeout_marks_run_and_retains_trace(monkeypatch, tmp_path: Path) -> None:
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

    assert result.state == "timeout"
    assert result.trace_path is not None
    assert result.trace_path.exists()
    run = governor.ledger.get_run(result.run_id)
    assert run is not None
    assert run["state"] == "timeout"


def test_failed_run_without_pr_releases_own_claim(monkeypatch, tmp_path: Path) -> None:
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

    assert result.state == "failed"
    assert result.exit_code == 2
    assert github.deleted == [10]


def test_zero_exit_without_ws_completion_or_closed_issue_is_failed(
    monkeypatch, tmp_path: Path
) -> None:
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

    assert result.state == "failed"
    assert result.exit_code == 0
    assert github.deleted == [10]
    run = governor.ledger.get_run(result.run_id)
    assert run is not None
    assert run["error"] == (
        "codex exited 0 but no ws completion, PR completion, or closed issue was confirmed"
    )


def test_zero_exit_with_pr_but_no_ws_completion_is_failed(monkeypatch, tmp_path: Path) -> None:
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
        if calls < 2:
            return []
        return [{"number": 7, "url": "https://example/pr/7", "headRefName": "add-company/x"}]

    monkeypatch.setattr(github, "check_existing_prs", check_existing_prs)

    result = governor.run_once()

    assert result.state == "failed"
    assert result.exit_code == 0
    assert github.deleted == [10]
    run = governor.ledger.get_run(result.run_id)
    assert run is not None
    assert run["pr_number"] == 7


def test_zero_exit_with_pr_and_ws_completion_is_completed(monkeypatch, tmp_path: Path) -> None:
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
        if calls < 2:
            return []
        return [{"number": 7, "url": "https://example/pr/7", "headRefName": "add-company/x"}]

    monkeypatch.setattr(github, "check_existing_prs", check_existing_prs)

    result = governor.run_once()

    assert result.state == "completed"
    assert result.exit_code == 0
    assert github.deleted == []
    run = governor.ledger.get_run(result.run_id)
    assert run is not None
    assert run["pr_number"] == 7


def test_zero_exit_with_closed_issue_is_completed(monkeypatch, tmp_path: Path) -> None:
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

    assert result.state == "completed"
    assert github.deleted == []


def test_stale_lease_with_reused_pid_is_failed_and_released(monkeypatch, tmp_path: Path) -> None:
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
    assert run["state"] == "failed"
    assert github.deleted == [10]


def test_exception_after_claim_marks_failed_and_releases_when_unresolved(
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

    assert result.state == "failed"
    assert result.error == "worktree failed"
    assert github.deleted == [10]
    run = governor.ledger.get_run(result.run_id)
    assert run is not None
    assert run["state"] == "failed"


ONE_DAY = 24 * 60 * 60
