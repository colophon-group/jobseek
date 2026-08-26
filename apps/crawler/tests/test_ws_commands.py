"""Tests for workspace CLI commands.

Uses Click's CliRunner for command invocation testing.
Mocks git/gh operations since these tests run without a real repo.
"""

from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.workspace.cli import ws
from src.workspace.errors import WorkspaceError, WorkspaceStateError
from src.workspace.state import (
    Board,
    Workspace,
    board_yaml_path,
    get_active_slug,
    list_boards,
    load_board,
    load_workspace,
    save_board,
    save_workspace,
    set_active_slug,
    workspace_exists,
    ws_yaml_path,
)

COMPANIES_HEADER = "slug,name,website,logo_url,icon_url,logo_type\n"
BOARDS_HEADER = (
    "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
)
TEST_HEAD_OID = "a" * 40


def _test_pr_details(
    number: int,
    *,
    slug: str = "test",
    issue: int | None = 1,
    branch: str | None = None,
    author: str = "resolver",
) -> dict:
    closing = []
    if issue is not None:
        closing = [
            {
                "number": issue,
                "repository": {
                    "name": "jobseek",
                    "owner": {"login": "colophon-group"},
                },
            }
        ]
    return {
        "number": number,
        "state": "OPEN",
        "isDraft": True,
        "headRefName": branch or f"add-company/{slug}",
        "headRefOid": TEST_HEAD_OID,
        "headRepository": {"name": "jobseek"},
        "headRepositoryOwner": {"login": "colophon-group"},
        "baseRefName": "main",
        "author": {"login": author},
        "closingIssuesReferences": closing,
        "isCrossRepository": False,
    }


def _test_pr_provenance(number: int, *, slug: str = "test", issue: int | None = 1) -> dict:
    from src.workspace.git import pr_provenance

    return pr_provenance(_test_pr_details(number, slug=slug, issue=issue), issue=issue, slug=slug)


def _pr_safety_race_details(kind: str, *, issue: int | None = 42) -> dict:
    details = _test_pr_details(10, slug="test", issue=issue)
    if kind == "review":
        details["reviews"] = [
            {
                "state": "APPROVED",
                "author": {"login": "reviewer"},
                "commit": {"oid": TEST_HEAD_OID},
                "submittedAt": "2026-08-26T12:00:00Z",
            }
        ]
    elif kind == "comment":
        details["comments"] = [
            {
                "author": {"login": "reviewer"},
                "createdAt": "2026-08-26T12:00:00Z",
                "body": "Do not merge until the independent audit is complete.",
            }
        ]
    elif kind == "hold":
        details["labels"] = [{"name": "merge-hold"}]
    elif kind == "ready":
        details["isDraft"] = False
    else:
        raise AssertionError(f"unknown PR race kind: {kind}")
    return details


def _setup_csvs(tmp_path, companies="", boards=""):
    (tmp_path / "companies.csv").write_text(COMPANIES_HEADER + companies)
    (tmp_path / "boards.csv").write_text(BOARDS_HEADER + boards)


def _inventory_issue_body() -> str:
    from src.ats_inventory.candidates import Candidate, CandidatePlan, render_candidate_issue
    from src.ats_inventory.models import CompanyImpact

    candidate = Candidate.from_impact(
        CompanyImpact(
            ats="greenhouse",
            name="Acme",
            slug="acme",
            url="https://boards.greenhouse.io/acme",
            impact_unknown=False,
            active_jobs=12,
            remote_jobs=2,
            location_count=3,
            country_codes=("US",),
            latest_posted_at="2026-08-01T00:00:00Z",
        )
    )
    return render_candidate_issue(CandidatePlan(candidate, (), ()))[1]


def _patch_all(monkeypatch, tmp_path, *, strict_worktree: bool = False):
    """Patch path getters for testing."""
    ws_dir = tmp_path / ".ws"
    _data = lambda: tmp_path  # noqa: E731
    _ws = lambda: ws_dir  # noqa: E731
    monkeypatch.setattr("src.shared.constants.get_data_dir", _data)
    monkeypatch.setattr("src.shared.constants.get_workspace_dir", _ws)
    monkeypatch.setattr("src.csvtool.get_data_dir", _data)
    monkeypatch.setattr("src.inspect.get_data_dir", _data)
    monkeypatch.setattr("src.workspace.commands.lifecycle.get_data_dir", _data)
    monkeypatch.setattr("src.workspace.commands.taxonomy.get_data_dir", _data)
    monkeypatch.setattr("src.workspace.state.get_workspace_dir", _ws)
    monkeypatch.setattr("src.workspace.filelock._LIFECYCLE_LOCKS_DIR", tmp_path / ".locks")
    monkeypatch.setattr(
        "src.workspace.git.current_head_oid_strict",
        lambda **_kwargs: TEST_HEAD_OID,
    )
    if not strict_worktree:
        # Most command tests exercise behavior after the worktree ownership
        # gate. Dedicated hostile-path tests below keep the real gate enabled.
        monkeypatch.setattr(
            "src.workspace.preflight.pivot_to_workspace_worktree", lambda _workspace: None
        )

    # Keep CLI tests deterministic/offline: board link analysis is exercised
    # in dedicated tests via targeted monkeypatching.
    monkeypatch.setattr(
        "src.workspace.commands.config._inspect_board_job_links",
        lambda url, provided_pattern: SimpleNamespace(
            board_url=url,
            final_url=url,
            fetch_mode="http",
            outgoing_links_total=0,
            job_links_total=0,
            matched_outgoing_links=0,
            matched_job_links=0,
            pattern=provided_pattern,
            pattern_source="provided" if provided_pattern else None,
            warnings=[],
        ),
    )


@contextmanager
def _mock_terminal_issue(*, claimed: bool = False):
    state = {"comment": False, "closed": False, "claimed": claimed, "labels": set()}
    events: list[str] = []

    def comment(*_args):
        state["comment"] = True
        events.append("comment")

    def close(*_args):
        state["closed"] = True
        events.append("close-issue")

    def unclaim(*_args):
        state["claimed"] = False
        events.append("unclaim")

    def add_label(_issue, label):
        state["labels"].add(label)
        events.append(f"label:{label}")

    with (
        patch("src.workspace.git.comment_on_issue_once", side_effect=comment) as comment_mock,
        patch("src.workspace.git.close_issue_if_open", side_effect=close) as close_mock,
        patch("src.workspace.git.unclaim_issue_strict", side_effect=unclaim) as unclaim_mock,
        patch(
            "src.workspace.git.issue_has_comment_marker_strict",
            side_effect=lambda *_args: state["comment"],
        ),
        patch(
            "src.workspace.git.issue_state_and_labels_strict",
            side_effect=lambda *_args: (
                "CLOSED" if state["closed"] else "OPEN",
                set(state["labels"]),
            ),
        ),
        patch(
            "src.workspace.git.is_issue_claimed_strict",
            side_effect=lambda *_args: state["claimed"],
        ),
        patch("src.workspace.git.add_label_to_issue", side_effect=add_label),
    ):
        yield SimpleNamespace(
            comment=comment_mock,
            close=close_mock,
            unclaim=unclaim_mock,
            events=events,
            state=state,
        )


class TestValidate:
    def test_valid_csvs(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(
            tmp_path,
            companies="test,Test,https://test.com,,\n",
            boards="test,test-careers,https://test.com/jobs,greenhouse,,,\n",
        )
        runner = CliRunner()
        result = runner.invoke(ws, ["validate"])
        assert result.exit_code == 0
        assert "passed" in result.output

    def test_invalid_csvs(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="INVALID,,,,\n")
        runner = CliRunner()
        result = runner.invoke(ws, ["validate"])
        assert result.exit_code != 0


class TestUse:
    def test_use_sets_active(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        runner = CliRunner()
        result = runner.invoke(ws, ["use", "test"])
        assert result.exit_code == 0
        assert "Active workspace: test" in result.output
        assert get_active_slug() == "test"

    def test_use_nonexistent(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(ws, ["use", "nonexistent"])
        assert result.exit_code != 0


class TestStatus:
    def test_empty(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(ws, ["status"])
        assert "No active workspace" in result.output


class TestHelp:
    def test_help_board_topic(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(ws, ["help", "board"])
        assert result.exit_code == 0
        assert "Board Command Reference" in result.output
        assert "ws add board" in result.output
        assert "ws del board" in result.output

    def test_help_monitor_cards_match_known_monitor_types(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        from src.workspace._compat import all_monitor_types
        from src.workspace.commands.help import MONITOR_CARDS

        assert set(MONITOR_CARDS) == set(all_monitor_types())

    def test_help_scraper_cards_match_registered_scrapers(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        from src.core.scrapers import _REGISTRY
        from src.workspace.commands.help import SCRAPER_CARDS

        assert set(SCRAPER_CARDS) == set(_REGISTRY)

    def test_help_monitor_join_and_rss_topics(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        runner = CliRunner()

        join_result = runner.invoke(ws, ["help", "monitor", "join"])
        assert join_result.exit_code == 0
        assert "JOIN" in join_result.output

        rss_result = runner.invoke(ws, ["help", "monitor", "rss"])
        assert rss_result.exit_code == 0
        assert "RSS 2.0 Feed Monitor" in rss_result.output

        legacy_result = runner.invoke(ws, ["help", "monitor", "successfactors"])
        assert legacy_result.exit_code != 0
        assert "Unknown monitor type" in legacy_result.output

    def test_dom_help_documents_antibot_browser_options(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        runner = CliRunner()

        for topic in ("monitor", "scraper"):
            result = runner.invoke(ws, ["help", topic, "dom"])
            assert result.exit_code == 0
            assert "proxy" in result.output
            assert "persistent_context" in result.output
            assert 'channel: "chrome"' in result.output
            assert "warmup_url" in result.output

    def test_help_actions_documents_paginated_page_collection(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        runner = CliRunner()

        result = runner.invoke(ws, ["help", "actions"])

        assert result.exit_code == 0
        assert '"action": "paginate_collect"' in result.output
        assert "next_selector" in result.output
        assert "page_size_selector" in result.output
        assert "max_pages" in result.output

    def test_help_industries_uses_repo_data_and_supports_legacy_schema(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        (tmp_path / "industries.csv").write_text(
            'id,name,keywords\n1,Technology,"software,AI"\n', encoding="utf-8"
        )

        runner = CliRunner()
        result = runner.invoke(ws, ["help", "industries"])

        assert result.exit_code == 0
        assert "Industry Taxonomy" in result.output
        assert "Technology" in result.output
        assert "No industries found" not in result.output

    def test_with_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(slug="test", issue=42, pr=10, name="Test")
        save_workspace(ws_obj)
        runner = CliRunner()
        result = runner.invoke(ws, ["status", "test"])
        assert "test" in result.output
        assert "#42" in result.output

    def test_status_uses_active(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(slug="test", issue=42, pr=10, name="Test")
        save_workspace(ws_obj)
        set_active_slug("test")
        runner = CliRunner()
        result = runner.invoke(ws, ["status"])
        # Should show detail view for active workspace, not list view
        assert "#42" in result.output

    def test_no_active_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="alpha"))
        save_workspace(Workspace(slug="beta"))
        runner = CliRunner()
        # No active workspace — error, not a listing
        result = runner.invoke(ws, ["status"])
        assert "No active workspace" in result.output


class TestSet:
    def test_set_name(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        runner = CliRunner()

        with patch("src.workspace.commands.config.httpx", create=True):
            result = runner.invoke(ws, ["set", "test", "--name", "Test Corp"])
        assert result.exit_code == 0
        loaded = load_workspace("test")
        assert loaded.name == "Test Corp"

    def test_set_without_slug_uses_active(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        set_active_slug("test")
        runner = CliRunner()

        with patch("src.workspace.commands.config.httpx", create=True):
            result = runner.invoke(ws, ["set", "--name", "Test Corp"])
        assert result.exit_code == 0
        loaded = load_workspace("test")
        assert loaded.name == "Test Corp"

    def test_set_logo_type(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        runner = CliRunner()
        result = runner.invoke(ws, ["set", "test", "--logo-type", "wordmark+icon"])
        assert result.exit_code == 0
        loaded = load_workspace("test")
        assert loaded.logo_type == "wordmark+icon"

    def test_set_logo_type_invalid(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        runner = CliRunner()
        result = runner.invoke(ws, ["set", "test", "--logo-type", "lockup"])
        assert result.exit_code != 0
        assert "Invalid value for '--logo-type'" in result.output

    def test_set_logo_type_does_not_trigger_discovery(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", website="https://test.com"))
        monkeypatch.setattr(
            "src.workspace.commands.config._discover_and_show_all",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected discovery")),
        )
        runner = CliRunner()
        result = runner.invoke(ws, ["set", "test", "--logo-type", "wordmark"])
        assert result.exit_code == 0

    def test_set_no_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(ws, ["set", "nonexistent", "--name", "X"])
        assert result.exit_code != 0

    def test_set_nothing(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        runner = CliRunner()
        result = runner.invoke(ws, ["set", "test"])
        assert result.exit_code != 0

    def test_set_board_job_link_pattern(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", active_board="careers"))
        save_board("test", Board(alias="careers", slug="test-careers", url="https://test.com/jobs"))

        monkeypatch.setattr(
            "src.workspace.commands.config._inspect_board_job_links",
            lambda url, provided_pattern: SimpleNamespace(
                board_url=url,
                final_url=url,
                fetch_mode="http",
                outgoing_links_total=12,
                job_links_total=6,
                matched_outgoing_links=6,
                matched_job_links=6,
                pattern=provided_pattern,
                pattern_source="provided",
                warnings=[],
            ),
        )

        runner = CliRunner()
        result = runner.invoke(
            ws,
            [
                "set",
                "test",
                "--board",
                "careers",
                "--job-link-pattern",
                r"^https?://test\.com/jobs/",
            ],
        )
        assert result.exit_code == 0
        board = load_board("test", "careers")
        assert board.job_link_pattern == r"^https?://test\.com/jobs/"

    def test_set_logo_candidate_prefers_png_artifact(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))

        candidates_dir = tmp_path / ".ws" / "test" / "artifacts" / "company" / "logo-candidates"
        candidates_dir.mkdir(parents=True, exist_ok=True)

        original_path = candidates_dir / "candidate-1.svg"
        png_path = candidates_dir / "candidate-1.png"
        original_path.write_text("<svg></svg>")
        png_path.write_bytes(b"png-preview")
        (candidates_dir / "candidates.json").write_text(
            json.dumps(
                [
                    {
                        "index": 1,
                        "url": "https://cdn.example.com/logo.svg",
                        "artifact_path": str(original_path),
                        "original_artifact_path": str(original_path),
                        "png_artifact_path": str(png_path),
                        "embedded": True,
                    }
                ]
            )
        )

        monkeypatch.setattr("src.workspace.commands.config._check_image", lambda *_args: None)

        runner = CliRunner()
        result = runner.invoke(ws, ["set", "test", "--logo-candidate", "1"])
        assert result.exit_code == 0
        assert "Manual visual inspection required" in result.output

        workspace = load_workspace("test")
        assert workspace.logo_url == str(png_path)

    def test_set_icon_candidate_falls_back_to_original_artifact(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))

        candidates_dir = tmp_path / ".ws" / "test" / "artifacts" / "company" / "logo-candidates"
        candidates_dir.mkdir(parents=True, exist_ok=True)

        original_path = candidates_dir / "candidate-1.svg"
        missing_png_path = candidates_dir / "candidate-1.png"
        original_path.write_text("<svg></svg>")
        (candidates_dir / "candidates.json").write_text(
            json.dumps(
                [
                    {
                        "index": 1,
                        "url": "https://cdn.example.com/icon.svg",
                        "artifact_path": str(original_path),
                        "original_artifact_path": str(original_path),
                        "png_artifact_path": str(missing_png_path),
                        "embedded": True,
                    }
                ]
            )
        )

        monkeypatch.setattr("src.workspace.commands.config._check_image", lambda *_args: None)

        runner = CliRunner()
        result = runner.invoke(ws, ["set", "test", "--icon-candidate", "1"])
        assert result.exit_code == 0

        workspace = load_workspace("test")
        assert workspace.icon_url == str(original_path)

    def test_set_logo_candidate_uses_url_when_no_local_artifact(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))

        candidates_dir = tmp_path / ".ws" / "test" / "artifacts" / "company" / "logo-candidates"
        candidates_dir.mkdir(parents=True, exist_ok=True)
        (candidates_dir / "candidates.json").write_text(
            json.dumps(
                [
                    {
                        "index": 1,
                        "url": "https://cdn.example.com/logo.png",
                        "artifact_path": "",
                        "original_artifact_path": "",
                        "png_artifact_path": "",
                    }
                ]
            )
        )

        monkeypatch.setattr("src.workspace.commands.config._check_image", lambda *_args: None)

        runner = CliRunner()
        result = runner.invoke(ws, ["set", "test", "--logo-candidate", "1"])
        assert result.exit_code == 0

        workspace = load_workspace("test")
        assert workspace.logo_url == "https://cdn.example.com/logo.png"

    def test_set_website_no_discover_skips_side_effects(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))

        monkeypatch.setattr(
            "src.workspace.commands.config._discover_and_show_all",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("discovery should not run")),
        )
        monkeypatch.setattr(
            "src.workspace.commands.config._auto_enrich",
            lambda *a, **kw: (_ for _ in ()).throw(AssertionError("enrichment should not run")),
        )

        runner = CliRunner()
        result = runner.invoke(ws, ["set", "test", "--website", "https://new.com", "--no-discover"])
        assert result.exit_code == 0
        loaded = load_workspace("test")
        assert loaded.website == "https://new.com"

    def test_set_website_without_no_discover_triggers_discovery(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))

        # Mock shutil.which to return a fake ws binary path
        monkeypatch.setattr(
            "shutil.which", lambda name: "/usr/local/bin/ws" if name == "ws" else None
        )

        popen_calls = []
        mock_proc = MagicMock()
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda *args, **kwargs: popen_calls.append((args, kwargs)) or mock_proc,
        )

        runner = CliRunner()
        result = runner.invoke(ws, ["set", "test", "--website", "https://new.com"])
        assert result.exit_code == 0
        assert len(popen_calls) == 1
        assert "discover-bg" in popen_calls[0][0][0]
        assert "Background discovery launched" in result.output


class TestAddBoard:
    def test_add_board(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        runner = CliRunner()
        result = runner.invoke(
            ws, ["add", "board", "test", "careers", "--url", "https://test.com/jobs"]
        )
        assert result.exit_code == 0
        assert "test-careers" in result.output

        ws_obj = load_workspace("test")
        assert ws_obj.active_board == "careers"

    def test_add_board_without_slug(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        set_active_slug("test")
        runner = CliRunner()
        result = runner.invoke(ws, ["add", "board", "careers", "--url", "https://test.com/jobs"])
        assert result.exit_code == 0
        assert "test-careers" in result.output

    def test_double_prefix_warning(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        runner = CliRunner()
        result = runner.invoke(
            ws, ["add", "board", "test", "test-careers", "--url", "https://test.com/jobs"]
        )
        assert result.exit_code == 0
        assert "already prefixed" in result.output

    def test_add_board_stores_inferred_job_link_pattern(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        monkeypatch.setattr(
            "src.workspace.commands.config._inspect_board_job_links",
            lambda url, provided_pattern: SimpleNamespace(
                board_url=url,
                final_url=url,
                fetch_mode="http",
                outgoing_links_total=20,
                job_links_total=8,
                matched_outgoing_links=8,
                matched_job_links=8,
                pattern=r"^https?://test\.com/jobs/",
                pattern_source="inferred",
                warnings=[],
            ),
        )

        runner = CliRunner()
        result = runner.invoke(
            ws, ["add", "board", "test", "careers", "--url", "https://test.com/jobs"]
        )
        assert result.exit_code == 0
        board = load_board("test", "careers")
        assert board.job_link_pattern == r"^https?://test\.com/jobs/"


class TestTaxonomy:
    def test_taxonomy_search_uses_repo_data(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        (tmp_path / "industries.csv").write_text(
            'id,name,keywords\n1,Technology,"software,AI"\n', encoding="utf-8"
        )

        runner = CliRunner()
        result = runner.invoke(ws, ["taxonomy", "search", "industries", "AI"])

        assert result.exit_code == 0
        assert "Technology" in result.output


class TestDelBoard:
    def test_del_board_accepts_full_board_slug(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", active_board="careers"))
        set_active_slug("test")
        save_board("test", Board(alias="careers", slug="test-careers", url="https://test.com/jobs"))

        runner = CliRunner()
        result = runner.invoke(ws, ["del", "board", "test-careers"])
        assert result.exit_code == 0
        assert "Resolved 'test-careers' to alias 'careers'" in result.output
        assert "Removed board 'careers'" in result.output

    def test_del_board_repairs_workflow_pointer(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(slug="test", active_board="careers")
        save_workspace(ws_obj)
        save_board("test", Board(alias="careers", slug="test-careers", url="https://test.com/jobs"))
        save_board(
            "test",
            Board(
                alias="careers-lever", slug="test-careers-lever", url="https://jobs.lever.co/test"
            ),
        )

        from src.workspace.workflow import WorkflowState, _load_wf_from_disk, _save_wf_to_disk

        _save_wf_to_disk(
            "test",
            WorkflowState(
                current_step="select_monitor",
                current_board="careers",
                completed_boards=["careers"],
            ),
        )

        runner = CliRunner()
        result = runner.invoke(ws, ["del", "test", "board", "test", "careers"])
        assert result.exit_code == 0
        assert "Removed board 'careers'" in result.output

        updated_ws = load_workspace("test")
        assert updated_ws.active_board == "careers-lever"

        wf = _load_wf_from_disk("test")
        assert wf.current_board == "careers-lever"
        assert "careers" not in wf.completed_boards


class TestUseBoard:
    def test_use_slug_and_board(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="a", slug="test-a", url="https://a.com"))
        save_board("test", Board(alias="b", slug="test-b", url="https://b.com"))

        runner = CliRunner()
        result = runner.invoke(ws, ["use", "test", "b"])
        assert result.exit_code == 0
        assert "Active workspace: test" in result.output
        assert "Active board: test-b" in result.output

        assert get_active_slug() == "test"
        ws_obj = load_workspace("test")
        assert ws_obj.active_board == "b"

    def test_use_board_flag(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="a", slug="test-a", url="https://a.com"))
        save_board("test", Board(alias="b", slug="test-b", url="https://b.com"))
        set_active_slug("test")

        runner = CliRunner()
        result = runner.invoke(ws, ["use", "--board", "b"])
        assert result.exit_code == 0
        assert "Active board: test-b" in result.output

        ws_obj = load_workspace("test")
        assert ws_obj.active_board == "b"

    def test_use_board_flag_accepts_board_slug(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="a", slug="test-a", url="https://a.com"))
        save_board("test", Board(alias="b", slug="test-b", url="https://b.com"))
        set_active_slug("test")

        runner = CliRunner()
        result = runner.invoke(ws, ["use", "--board", "test-b"])
        assert result.exit_code == 0
        assert "Resolved 'test-b' to alias 'b'" in result.output
        assert "Active board: test-b" in result.output

        ws_obj = load_workspace("test")
        assert ws_obj.active_board == "b"

    def test_use_company_flag(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))

        runner = CliRunner()
        result = runner.invoke(ws, ["use", "--company", "test"])
        assert result.exit_code == 0
        assert "Active workspace: test" in result.output
        assert get_active_slug() == "test"

    def test_use_both_flags(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="a", slug="test-a", url="https://a.com"))

        runner = CliRunner()
        result = runner.invoke(ws, ["use", "--company", "test", "--board", "a"])
        assert result.exit_code == 0
        assert "Active workspace: test" in result.output
        assert "Active board: test-a" in result.output

    def test_use_no_args(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        runner = CliRunner()
        result = runner.invoke(ws, ["use"])
        assert result.exit_code != 0

    def test_use_board_nonexistent(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        set_active_slug("test")
        runner = CliRunner()
        result = runner.invoke(ws, ["use", "--board", "nope"])
        assert result.exit_code != 0


class TestReject:
    def test_reject_with_issue(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        runner = CliRunner()

        with (
            patch("src.workspace.git.check_existing_prs_strict", return_value=[]),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            _mock_terminal_issue() as issue_state,
        ):
            result = runner.invoke(
                ws,
                [
                    "reject",
                    "--issue",
                    "42",
                    "--reason",
                    "no-job-board",
                    "--message",
                    "No careers page found",
                ],
            )
            assert result.exit_code == 0
            issue_state.comment.assert_called_once()
            issue_state.close.assert_called_once_with(42)

    def test_reject_from_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", issue=42))
        runner = CliRunner()

        with (
            patch("src.workspace.git.check_existing_prs_strict", return_value=[]),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            _mock_terminal_issue() as issue_state,
        ):
            result = runner.invoke(
                ws,
                [
                    "reject",
                    "test",
                    "--reason",
                    "no-open-positions",
                    "--message",
                    "Zero listings visible",
                ],
            )
            assert result.exit_code == 0
            issue_state.close.assert_called_once_with(42)

    def test_reject_from_active_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", issue=42))
        set_active_slug("test")
        runner = CliRunner()

        with (
            patch("src.workspace.git.check_existing_prs_strict", return_value=[]),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            _mock_terminal_issue() as issue_state,
        ):
            result = runner.invoke(
                ws,
                [
                    "reject",
                    "--reason",
                    "no-open-positions",
                    "--message",
                    "Zero listings visible",
                ],
            )
            assert result.exit_code == 0
            issue_state.close.assert_called_once_with(42)

    def test_reject_explicit_issue_not_overridden_by_active_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="active-ws", issue=38))
        set_active_slug("active-ws")
        runner = CliRunner()

        with (
            patch("src.workspace.git.check_existing_prs_strict", return_value=[]),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            _mock_terminal_issue() as issue_state,
        ):
            result = runner.invoke(
                ws,
                [
                    "reject",
                    "--issue",
                    "39",
                    "--reason",
                    "no-open-positions",
                    "--message",
                    "No listings found",
                ],
            )
            assert result.exit_code == 0
            issue_state.close.assert_called_once_with(39)

    def test_reject_slug_issue_mismatch_fails_fast(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", issue=38))
        runner = CliRunner()

        with (
            patch("src.workspace.git.check_existing_prs_strict", return_value=[]),
            patch("src.workspace.git.comment_on_issue_once") as mock_comment,
            patch("src.workspace.git.unclaim_issue_strict"),
            patch("src.workspace.git.close_issue_if_open") as mock_close,
        ):
            result = runner.invoke(
                ws,
                [
                    "reject",
                    "test",
                    "--issue",
                    "39",
                    "--reason",
                    "no-open-positions",
                    "--message",
                    "No listings found",
                ],
            )
            assert result.exit_code != 0
            assert "does not match workspace" in result.output
            mock_comment.assert_not_called()
            mock_close.assert_not_called()

    def test_reject_by_issue_cleans_resumable_artifacts_before_closing(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="acme,,,,,\n")
        worktree = tmp_path / "worktrees" / "acme"
        save_workspace(
            Workspace(
                slug="acme",
                issue=42,
                pr=7,
                branch="add-company/acme",
                pr_provenance=_test_pr_provenance(7, slug="acme", issue=42),
                worktree=str(worktree),
                worktree_identity={
                    "version": 1,
                    "path": str(worktree),
                    "slug": "acme",
                    "branch": "add-company/acme",
                    "head": TEST_HEAD_OID,
                    "dev": 1,
                    "ino": 2,
                    "issue": 42,
                    "pr": 7,
                    "pr_provenance": _test_pr_provenance(7, slug="acme", issue=42),
                },
            )
        )
        events: list[str] = []
        refs = {"remote": TEST_HEAD_OID, "local": TEST_HEAD_OID}
        pr_details = _test_pr_details(7, slug="acme", issue=42)
        worktree_present = {"value": True}

        def delete_remote(*_args, **_kwargs):
            events.append("delete-remote")
            refs["remote"] = None

        def close_pr(_number):
            events.append("close-pr")
            pr_details["state"] = "CLOSED"

        def remove_worktree(*_args, **_kwargs):
            events.append("remove-worktree")
            worktree_present["value"] = False

        def delete_local(*_args, **_kwargs):
            events.append("delete-local")
            refs["local"] = None

        with (
            patch(
                "src.workspace.git.check_existing_prs_strict",
                return_value=[
                    {
                        "number": 7,
                        "headRefName": "add-company/acme",
                        "isDraft": True,
                    }
                ],
            ),
            patch("src.workspace.git.worktrees_dir", return_value=tmp_path / "worktrees"),
            patch(
                "src.workspace.git.managed_worktree_identity_strict",
                side_effect=lambda *_args: (
                    {"head": TEST_HEAD_OID, "dev": 1, "ino": 2}
                    if worktree_present["value"]
                    else None
                ),
            ),
            patch("src.workspace.git.authenticate_managed_worktree"),
            patch("src.workspace.git.close_pr_if_open", side_effect=close_pr),
            patch("src.workspace.git.verify_recorded_pr"),
            patch(
                "src.workspace.git.verify_recorded_pr_object",
                side_effect=lambda *_args, **_kwargs: dict(pr_details),
            ),
            patch(
                "src.workspace.git.remove_authenticated_worktree",
                side_effect=remove_worktree,
            ),
            patch(
                "src.workspace.git.delete_remote_branch_at_expected_oid",
                side_effect=delete_remote,
            ),
            patch(
                "src.workspace.git.remote_branch_oid_strict",
                side_effect=lambda *_args: refs["remote"],
            ),
            patch(
                "src.workspace.git.local_branch_oid_strict",
                side_effect=lambda *_args: refs["local"],
            ),
            patch(
                "src.workspace.git.delete_local_branch_at_expected_oid",
                side_effect=delete_local,
            ),
            _mock_terminal_issue(claimed=True) as issue_state,
        ):
            result = CliRunner().invoke(
                ws,
                [
                    "reject",
                    "--issue",
                    "42",
                    "--reason",
                    "no-job-board",
                    "--message",
                    "No supported board",
                ],
            )

        assert result.exit_code == 0, result.output
        assert events == [
            "delete-remote",
            "close-pr",
            "remove-worktree",
            "delete-local",
        ]
        assert issue_state.events == [
            "comment",
            "close-issue",
            "unclaim",
        ]
        assert not workspace_exists("acme")

    def test_reject_cleanup_failure_leaves_issue_open_and_workspace_retryable(
        self, tmp_path, monkeypatch
    ):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="acme", issue=42, pr=7, branch="add-company/acme"))

        with (
            patch(
                "src.workspace.git.check_existing_prs_strict",
                return_value=[
                    {
                        "number": 7,
                        "headRefName": "add-company/acme",
                        "isDraft": True,
                    }
                ],
            ),
            patch("src.workspace.git.close_pr_if_open", side_effect=RuntimeError("offline")),
            patch("src.workspace.git.comment_on_issue_once") as comment,
            patch("src.workspace.git.close_issue_if_open") as close_issue,
        ):
            result = CliRunner().invoke(
                ws,
                [
                    "reject",
                    "--issue",
                    "42",
                    "--reason",
                    "no-job-board",
                    "--message",
                    "No supported board",
                ],
            )

        assert result.exit_code != 0
        assert workspace_exists("acme")
        comment.assert_not_called()
        close_issue.assert_not_called()


class TestTaskIssueBinding:
    def test_task_preverify_surfaces_validated_inventory_seed(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        with (
            patch("src.workspace.git.check_gh_auth", return_value=True),
            patch(
                "src.workspace.git.fetch_issue",
                return_value={
                    "title": "Add company: Acme",
                    "body": _inventory_issue_body(),
                    "labels": [{"name": "source:ats-inventory"}],
                },
            ),
        ):
            result = CliRunner().invoke(ws, ["task", "--issue", "39"])

        assert result.exit_code == 0, result.output
        assert "Validated inventory seed" in result.output
        assert "will preselect Jobseek's `greenhouse` monitor" in result.output
        assert 'ws search "<company name>"' in result.output

    def test_task_preverify_human_request_has_no_inventory_guidance(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        with (
            patch("src.workspace.git.check_gh_auth", return_value=True),
            patch(
                "src.workspace.git.fetch_issue",
                return_value={
                    "title": "Please add Acme",
                    "body": "Website: https://acme.example",
                },
            ),
        ):
            result = CliRunner().invoke(ws, ["task", "--issue", "39"])

        assert result.exit_code == 0, result.output
        assert "Website: https://acme.example" in result.output
        assert "Validated inventory seed" not in result.output
        assert "Inventory seed validation" not in result.output
        assert 'ws search "<company name>"' in result.output

    def test_task_preverify_ignores_spoofed_marker_without_inventory_label(
        self, tmp_path, monkeypatch
    ):
        _patch_all(monkeypatch, tmp_path)
        with (
            patch("src.workspace.git.check_gh_auth", return_value=True),
            patch(
                "src.workspace.git.fetch_issue",
                return_value={
                    "title": "Add company: Acme",
                    "body": _inventory_issue_body(),
                    "labels": [{"name": "company-request"}],
                },
            ),
        ):
            result = CliRunner().invoke(ws, ["task", "--issue", "39"])

        assert result.exit_code == 0, result.output
        assert "Validated inventory seed" not in result.output
        assert "will preselect" not in result.output
        assert 'ws search "<company name>"' in result.output

    def test_task_issue_binds_to_matching_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="swissquote-bank", issue=38, branch="add-company/swissquote"))
        save_workspace(Workspace(slug="playnvoice", issue=39, branch="add-company/playnvoice"))
        set_active_slug("swissquote-bank")

        runner = CliRunner()
        result = runner.invoke(ws, ["task", "--issue", "39"])

        assert result.exit_code == 0
        assert "Using existing workspace 'playnvoice' for issue #39" in result.output
        assert get_active_slug() == "playnvoice"
        assert "Parallel Pipeline" in result.output

    def test_task_issue_fails_on_ambiguous_workspace_matches(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="alpha", issue=39, branch="add-company/alpha"))
        save_workspace(Workspace(slug="beta", issue=39, branch="add-company/beta"))
        save_workspace(Workspace(slug="other", issue=38, branch="add-company/other"))
        set_active_slug("other")

        runner = CliRunner()
        result = runner.invoke(ws, ["task", "--issue", "39"])

        assert result.exit_code != 0
        assert "Multiple workspaces match issue #39" in result.output


class TestTaskNext:
    def test_task_next_reports_skipped_scraper_for_rich_monitor(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(
            Workspace(slug="test", branch="add-company/test", name="Test", website="https://x")
        )
        set_active_slug("test")

        board = Board(alias="careers", slug="test-careers", url="https://boards.greenhouse.io/test")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "status": "tested",
            "scraper_type": "skip",
            "run": {"jobs": 10, "has_rich_data": True},
        }
        board.active_config = "greenhouse"
        save_board("test", board)

        from src.workspace.workflow import WorkflowState, _save_wf_to_disk

        _save_wf_to_disk(
            "test",
            WorkflowState(current_step="select_monitor", current_board="careers"),
        )

        runner = CliRunner()
        result = runner.invoke(ws, ["task", "next", "--notes", "none"])

        assert result.exit_code == 0
        assert "Skipped step: Select and test scraper" in result.output
        assert "Step 5/7: Verify quality and record feedback" in result.output


class TestTaskOutcomes:
    def test_task_fail_enters_coding_mode_instead_of_terminal_exit(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="acme", issue=42, branch="add-company/acme"))
        set_active_slug("acme")
        from src.workspace.workflow import WorkflowState, _load_wf_from_disk, _save_wf_to_disk

        _save_wf_to_disk("acme", WorkflowState(current_step="add_boards"))
        monkeypatch.setattr("src.workspace.trace.export_trace", lambda *args: None)

        result = CliRunner().invoke(
            ws,
            ["task", "fail", "--reason", "Unsupported board protocol"],
        )

        assert result.exit_code == 0
        assert "# Coding Mode" in result.output
        assert "Identify the root cause" in result.output
        assert "ws task escalate" in result.output
        workflow = _load_wf_from_disk("acme")
        assert workflow.failed is True
        assert workflow.fail_reason == "Unsupported board protocol"

    def test_task_escalate_cleans_and_records_terminal_follow_up(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="acme", issue=42))
        with (
            patch("src.workspace.git.check_existing_prs_strict", return_value=[]),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            _mock_terminal_issue() as issue_state,
        ):
            result = CliRunner().invoke(
                ws,
                [
                    "task",
                    "escalate",
                    "--issue",
                    "42",
                    "--reason",
                    "Needs authenticated browser support",
                    "--follow-up",
                    "Add a credentialed monitor before retrying",
                ],
            )

        assert result.exit_code == 0, result.output
        assert not workspace_exists("acme")
        marker, body = issue_state.comment.call_args.args[1:]
        assert marker == "<!-- resolver-outcome: escalated -->"
        assert "Needs authenticated browser support" in body
        assert "Add a credentialed monitor before retrying" in body
        issue_state.close.assert_called_once_with(42)


class TestTaskComplete:
    def test_task_complete_recovers_push_before_provenance_save(self, tmp_path, monkeypatch):
        from src.workspace.commands import lifecycle
        from src.workspace.commands.task import _finalize_workflow
        from src.workspace.workflow import WorkflowState, _load_wf_from_disk, _save_wf_to_disk

        _patch_all(monkeypatch, tmp_path)
        published_oid = "b" * 40
        ws_obj = Workspace(
            slug="test",
            issue=42,
            pr=10,
            branch="add-company/test",
            pr_provenance=_test_pr_provenance(10, issue=42),
        )
        ws_obj.submit_state = {"pushed": True}
        save_workspace(ws_obj)
        _save_wf_to_disk("test", WorkflowState(current_step="reflect"))
        state = {
            "local": TEST_HEAD_OID,
            "remote": TEST_HEAD_OID,
            "dirty": True,
            "draft": True,
            "claimed": True,
        }

        def details(_number):
            value = _test_pr_details(10, slug="test", issue=42)
            value["headRefOid"] = state["remote"]
            value["isDraft"] = state["draft"]
            return value

        def commit(_message):
            state["local"] = published_oid
            state["dirty"] = False

        def push(*_args):
            state["remote"] = published_oid

        def unclaim(_issue):
            state["claimed"] = False

        real_record = lifecycle._record_current_pr_provenance
        record_calls = {"count": 0}

        def record(*args, **kwargs):
            record_calls["count"] += 1
            if record_calls["count"] == 1:
                raise RuntimeError("crash-after-kb-push")
            return real_record(*args, **kwargs)

        with (
            patch("src.workspace.commands.lifecycle.is_local_mode", return_value=False),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            patch("src.workspace.commands.lifecycle._verify_workspace_pr_before_mutation"),
            patch(
                "src.workspace.commands.lifecycle._record_current_pr_provenance", side_effect=record
            ),
            patch(
                "src.workspace.git.changed_paths_strict",
                side_effect=lambda: (
                    {"apps/crawler/src/workspace/kb/new.md"} if state["dirty"] else set()
                ),
            ),
            patch(
                "src.workspace.git.current_head_oid_strict",
                side_effect=lambda **_kwargs: state["local"],
            ),
            patch("src.workspace.git.commit", side_effect=commit),
            patch("src.workspace.git.add_files"),
            patch("src.workspace.git.verify_single_commit_strict"),
            patch(
                "src.workspace.git.remote_branch_oid_strict",
                side_effect=lambda *_args: state["remote"],
            ),
            patch("src.workspace.git.push_branch_at_expected_oid", side_effect=push) as push_mock,
            patch("src.workspace.git.get_pr_details_strict", side_effect=details),
            patch("src.workspace.git.get_main_branch", return_value="main"),
            patch("src.workspace.git.mark_pr_ready") as ready,
            patch(
                "src.workspace.git.is_issue_claimed_strict", side_effect=lambda *_: state["claimed"]
            ),
            patch("src.workspace.git.unclaim_issue_strict", side_effect=unclaim),
            patch("src.workspace.trace.upload_trace_to_hf", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="crash-after-kb-push"):
                _finalize_workflow("test")
            journal = load_workspace("test").ready_state
            assert journal["kb_publish_oid"] == published_oid
            assert journal["attempts"]["kb_push"] is True
            assert _load_wf_from_disk("test").current_step == "reflect"

            _finalize_workflow("test")

        push_mock.assert_called_once_with("add-company/test", published_oid, TEST_HEAD_OID)
        assert _load_wf_from_disk("test").current_step == "done"
        assert state["draft"] is True
        assert state["claimed"] is False
        ready.assert_not_called()

    @pytest.mark.parametrize("phase", ["before_commit", "before_push"])
    @pytest.mark.parametrize("kind", ["review", "comment", "hold", "ready"])
    def test_kb_publication_rechecks_exact_pr_lease_before_mutation(
        self, tmp_path, monkeypatch, phase, kind
    ):
        from src.workspace.commands.task import _finalize_workflow
        from src.workspace.workflow import WorkflowState, _save_wf_to_disk

        _patch_all(monkeypatch, tmp_path)
        published_oid = "b" * 40
        save_workspace(
            Workspace(
                slug="test",
                issue=42,
                pr=10,
                branch="add-company/test",
                pr_provenance=_test_pr_provenance(10, issue=42),
                submit_state={"pushed": True},
            )
        )
        _save_wf_to_disk("test", WorkflowState(current_step="reflect"))
        state = {"local": TEST_HEAD_OID, "remote": TEST_HEAD_OID, "dirty": True}
        pristine = _test_pr_details(10, slug="test", issue=42)
        raced = _pr_safety_race_details(kind)
        detail_calls = {"count": 0}

        def details(_number):
            detail_calls["count"] += 1
            if phase == "before_commit" or detail_calls["count"] >= 2:
                return copy.deepcopy(raced)
            return copy.deepcopy(pristine)

        def commit(_message):
            state["local"] = published_oid
            state["dirty"] = False

        with (
            patch("src.workspace.commands.lifecycle.is_local_mode", return_value=False),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            patch("src.workspace.commands.lifecycle._verify_workspace_pr_before_mutation"),
            patch("src.workspace.git.is_issue_claimed_strict", return_value=False),
            patch(
                "src.workspace.git.changed_paths_strict",
                side_effect=lambda: (
                    {"apps/crawler/src/workspace/kb/new.md"} if state["dirty"] else set()
                ),
            ),
            patch(
                "src.workspace.git.current_head_oid_strict",
                side_effect=lambda **_kwargs: state["local"],
            ),
            patch("src.workspace.git.add_files"),
            patch("src.workspace.git.commit", side_effect=commit) as commit_mock,
            patch("src.workspace.git.verify_single_commit_strict"),
            patch(
                "src.workspace.git.remote_branch_oid_strict",
                side_effect=lambda *_args: state["remote"],
            ),
            patch("src.workspace.git.get_pr_details_strict", side_effect=details),
            patch("src.workspace.git.push_branch_at_expected_oid") as push,
            patch("src.workspace.git.mark_pr_draft") as mark_draft,
            pytest.raises(WorkspaceError, match="provenance or head changed"),
        ):
            _finalize_workflow("test")

        assert commit_mock.call_count == (1 if phase == "before_push" else 0)
        assert state["remote"] == TEST_HEAD_OID
        push.assert_not_called()
        mark_draft.assert_not_called()


class TestDel:
    def test_del_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="test,Test,,,\n")
        worktree = tmp_path / "worktrees" / "test"
        save_workspace(
            Workspace(
                slug="test",
                branch="add-company/test",
                pr=10,
                pr_provenance=_test_pr_provenance(10, issue=None),
                worktree=str(worktree),
            )
        )
        runner = CliRunner()

        with patch("src.workspace.commands.lifecycle.is_local_mode", return_value=True):
            result = runner.invoke(ws, ["del", "test"])
            assert result.exit_code == 0

        assert not workspace_exists("test")

    def test_del_clears_active(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="test,Test,,,\n")
        save_workspace(
            Workspace(
                slug="test",
                branch="add-company/test",
                pr=10,
                pr_provenance=_test_pr_provenance(10, issue=None),
                worktree=str(tmp_path / "worktrees" / "test"),
            )
        )
        set_active_slug("test")
        runner = CliRunner()

        with patch("src.workspace.commands.lifecycle.is_local_mode", return_value=True):
            result = runner.invoke(ws, ["del", "test"])
            assert result.exit_code == 0

        assert get_active_slug() is None


class TestTerminalCleanupRecovery:
    @staticmethod
    def _workspace(tmp_path) -> Workspace:
        path = tmp_path / "worktrees" / "acme"
        provenance = _test_pr_provenance(7, slug="acme", issue=42)
        return Workspace(
            slug="acme",
            issue=42,
            pr=7,
            branch="add-company/acme",
            pr_provenance=provenance,
            worktree=str(path),
            worktree_identity={
                "version": 1,
                "path": str(path),
                "slug": "acme",
                "branch": "add-company/acme",
                "head": TEST_HEAD_OID,
                "dev": 1,
                "ino": 2,
                "issue": 42,
                "pr": 7,
                "pr_provenance": copy.deepcopy(provenance),
            },
        )

    @staticmethod
    def _materialize_identity(workspace, monkeypatch):
        path = Path(workspace.worktree)
        path.mkdir(parents=True)
        item = path.stat()
        workspace.worktree_identity["dev"] = int(item.st_dev)
        workspace.worktree_identity["ino"] = int(item.st_ino)
        monkeypatch.setattr("src.workspace.git._WORKTREES_DIR", path.parent)
        monkeypatch.setattr(
            "src.workspace.git._registered_worktrees_strict",
            lambda: {
                path: {
                    "head": TEST_HEAD_OID,
                    "branch": f"refs/heads/{workspace.branch}",
                    "locked": False,
                }
            },
        )

    def _root_aware_workspace(self, tmp_path, monkeypatch, *, issue=None):
        from src import csvtool
        from src.shared import constants

        managed = tmp_path / "managed-repo"
        worktree = tmp_path / "managed-worktrees" / "acme"
        managed_data = managed / "apps" / "crawler" / "data"
        worktree_data = worktree / "apps" / "crawler" / "data"
        managed_data.mkdir(parents=True)
        worktree_data.mkdir(parents=True)
        _setup_csvs(managed_data)
        _setup_csvs(
            worktree_data,
            companies="acme,Acme,,,,\n",
            boards="acme,acme-careers,https://acme.test/jobs,greenhouse,,,\n",
        )

        monkeypatch.setattr(constants, "_repo_root", managed)
        monkeypatch.setattr(constants, "_workspace_root", managed)
        monkeypatch.setattr(csvtool, "get_data_dir", constants.get_data_dir)
        monkeypatch.setattr("src.workspace.git._MANAGED_REPO", managed)
        monkeypatch.setattr("src.workspace.git._WORKTREES_DIR", worktree.parent)
        monkeypatch.setattr("src.workspace.filelock._LIFECYCLE_LOCKS_DIR", tmp_path / ".locks")

        item = worktree.stat()
        workspace = Workspace(
            slug="acme",
            issue=issue,
            branch="add-company/acme",
            worktree=str(worktree),
            worktree_identity={
                "version": 1,
                "path": str(worktree),
                "slug": "acme",
                "branch": "add-company/acme",
                "head": TEST_HEAD_OID,
                "dev": int(item.st_dev),
                "ino": int(item.st_ino),
                "issue": issue,
                "pr": None,
                "pr_provenance": {},
            },
        )
        save_workspace(workspace)
        set_active_slug("acme")
        return SimpleNamespace(
            managed=managed,
            managed_data=managed_data,
            worktree=worktree,
            worktree_data=worktree_data,
            workspace=workspace,
            registration={"present": True},
            local={"oid": TEST_HEAD_OID},
        )

    @staticmethod
    def _patch_root_aware_git(monkeypatch, setup):
        monkeypatch.setattr(
            "src.workspace.git._registered_worktrees_strict",
            lambda: (
                {
                    setup.worktree: {
                        "head": TEST_HEAD_OID,
                        "branch": "refs/heads/add-company/acme",
                        "locked": False,
                    }
                }
                if setup.registration["present"]
                else {}
            ),
        )
        monkeypatch.setattr("src.workspace.git.remote_branch_oid_strict", lambda *_: None)
        monkeypatch.setattr("src.workspace.git.check_existing_prs_strict", lambda *_: [])
        monkeypatch.setattr(
            "src.workspace.git.local_branch_oid_strict", lambda *_: setup.local["oid"]
        )
        monkeypatch.setattr(
            "src.workspace.git.delete_local_branch_at_expected_oid",
            lambda *_args, **_kwargs: setup.local.__setitem__("oid", None),
        )
        monkeypatch.setattr(
            "src.workspace.git._worktree_admin_identity_strict",
            lambda *_args, **_kwargs: (
                (setup.managed / ".git" / "worktrees", "acme", 1, 2)
                if setup.registration["present"]
                else None
            ),
        )

    def test_add_company_data_is_removed_from_worktree_before_root_pivot(
        self, tmp_path, monkeypatch
    ):
        import shutil

        from src.shared.constants import get_repo_root, set_repo_root
        from src.workspace.commands import lifecycle

        setup = self._root_aware_workspace(tmp_path, monkeypatch)
        self._patch_root_aware_git(monkeypatch, setup)
        set_repo_root(setup.worktree)
        from src import csvtool

        assert get_repo_root() == setup.worktree
        assert lifecycle.get_data_dir() == setup.worktree_data
        assert csvtool.get_data_dir() == setup.worktree_data
        removal_observation = {}

        def remove(path, *_args, **_kwargs):
            removal_observation["repo_root"] = get_repo_root()
            removal_observation["company_present"] = (
                "acme,Acme" in (setup.worktree_data / "companies.csv").read_text()
            )
            journal, _ = lifecycle._load_terminal_journal("acme")
            removal_observation["data_attempt"] = journal["attempts"]["data_remove"]
            setup.registration["present"] = False
            shutil.rmtree(path)

        monkeypatch.setattr("src.workspace.git.remove_authenticated_worktree", remove)

        lifecycle._run_terminal_cleanup(setup.workspace, local=False)

        assert removal_observation == {
            "repo_root": setup.worktree,
            "company_present": False,
            "data_attempt": True,
        }
        assert "acme" not in (setup.managed_data / "companies.csv").read_text()
        receipt, completed = lifecycle._load_terminal_journal("acme")
        assert receipt is not None and completed is True
        assert receipt["data_initially_present"] is True
        assert receipt["attempts"]["data_remove"] is True
        assert receipt["attempts"]["worktree_remove"] is True

    @pytest.mark.parametrize(
        "boundary",
        ["before-rename", "rename", "quarantine-delete", "admin-prune"],
    )
    @pytest.mark.parametrize(
        ("command", "args"),
        [
            ("del", ["del", "acme"]),
            (
                "reject",
                [
                    "reject",
                    "acme",
                    "--reason",
                    "no-job-board",
                    "--message",
                    "No supported board found",
                ],
            ),
            (
                "escalate",
                [
                    "task",
                    "escalate",
                    "--reason",
                    "Ownership is ambiguous",
                    "--follow-up",
                    "Inspect the exact board manually",
                ],
            ),
        ],
    )
    def test_real_main_recovers_terminal_remover_intermediate_state(
        self, tmp_path, monkeypatch, boundary, command, args
    ):
        import shutil

        from src.workspace import cli
        from src.workspace.commands import lifecycle
        from src.workspace.git import terminal_worktree_quarantine_path

        setup = self._root_aware_workspace(
            tmp_path,
            monkeypatch,
            issue=42 if command != "del" else None,
        )
        self._patch_root_aware_git(monkeypatch, setup)
        monkeypatch.setattr(cli, "_detect_repo_root", lambda: setup.managed)
        removal_calls = {"count": 0}
        quarantine = terminal_worktree_quarantine_path(
            setup.worktree,
            setup.workspace.branch,
            TEST_HEAD_OID,
        )

        def remove(path, *_args, absent_is_success=False, **_kwargs):
            removal_calls["count"] += 1
            if removal_calls["count"] == 1:
                assert absent_is_success is False
                if boundary == "before-rename":
                    raise RuntimeError("crash-before-worktree-rename")
                path.rename(quarantine)
                if boundary == "rename":
                    raise RuntimeError("crash-after-worktree-rename")
                shutil.rmtree(quarantine)
                if boundary == "quarantine-delete":
                    raise RuntimeError("crash-after-quarantine-delete")
                setup.registration["present"] = False
                raise RuntimeError("crash-after-admin-prune")
            assert absent_is_success is True
            if path.exists():
                shutil.rmtree(path)
            if quarantine.exists():
                shutil.rmtree(quarantine)
            setup.registration["present"] = False

        monkeypatch.setattr("src.workspace.git.remove_authenticated_worktree", remove)
        monkeypatch.setattr("sys.argv", ["ws", *args])

        with _mock_terminal_issue(claimed=False) as issue_state:
            with pytest.raises(RuntimeError, match="crash-(?:before|after)"):
                cli.main()

            journal, completed = lifecycle._load_terminal_journal("acme")
            assert journal is not None and completed is False
            assert journal["attempts"]["data_remove"] is True
            assert journal["attempts"]["worktree_remove"] is True
            assert setup.worktree.exists() is (boundary == "before-rename")
            assert quarantine.exists() is (boundary == "rename")
            assert setup.registration["present"] is (boundary != "admin-prune")
            assert workspace_exists("acme")
            assert get_active_slug() == "acme"

            if boundary != "before-rename":
                monkeypatch.setattr("sys.argv", ["ws", "status"])
                with pytest.raises(
                    WorkspaceError,
                    match="missing but remains registered|worktree disappeared",
                ):
                    cli.main()
                assert workspace_exists("acme")

            if command in {"reject", "escalate"}:
                mismatched = list(args)
                mismatched[-1] = "A different terminal outcome"
                monkeypatch.setattr("sys.argv", ["ws", *mismatched])
                with pytest.raises(WorkspaceError, match="outcome contradicts"):
                    cli.main()
                assert workspace_exists("acme")
                assert issue_state.comment.call_count == 0

            monkeypatch.setattr("sys.argv", ["ws", *args])
            cli.main()

        assert removal_calls["count"] == 2
        assert not workspace_exists("acme")
        assert get_active_slug() is None
        receipt, completed = lifecycle._load_terminal_journal("acme")
        assert receipt is not None and completed is True
        if command == "del":
            assert issue_state.comment.call_count == 0
            assert issue_state.close.call_count == 0
        else:
            assert issue_state.comment.call_count == 1
            assert issue_state.close.call_count == 1

    def test_terminal_startup_preserves_replaced_quarantine(self, tmp_path, monkeypatch):
        from src.csvtool import company_del
        from src.shared.constants import set_repo_root
        from src.workspace import cli
        from src.workspace.commands import lifecycle
        from src.workspace.git import terminal_worktree_quarantine_path

        setup = self._root_aware_workspace(tmp_path, monkeypatch)
        self._patch_root_aware_git(monkeypatch, setup)
        set_repo_root(setup.worktree)
        journal = lifecycle._initialize_terminal_journal(
            setup.workspace,
            local=False,
            outcome=None,
        )
        lifecycle._set_terminal_attempt(journal, "data_remove")
        company_del("acme")
        lifecycle._set_terminal_attempt(journal, "worktree_remove")

        quarantine = terminal_worktree_quarantine_path(
            setup.worktree,
            setup.workspace.branch,
            TEST_HEAD_OID,
        )
        held = quarantine.with_name(f"{quarantine.name}.held")
        setup.worktree.rename(quarantine)
        quarantine.rename(held)
        quarantine.mkdir()
        pivot = MagicMock()
        mutate = MagicMock()
        monkeypatch.setattr("src.shared.constants.set_repo_root", pivot)
        monkeypatch.setattr("src.workspace.git._run", mutate)

        with pytest.raises(WorkspaceError, match="replacement filesystem entry"):
            cli._pivot_to_worktree(["del", "acme"])

        pivot.assert_not_called()
        mutate.assert_not_called()
        assert quarantine.is_dir()
        assert held.is_dir()
        assert workspace_exists("acme")

    @pytest.mark.parametrize(
        "failing",
        ["remote", "pr", "issue_close", "local", "data", "workspace", "claim"],
    )
    def test_crash_at_every_transition_resumes_without_losing_journal(
        self, tmp_path, monkeypatch, failing
    ):
        from src.csvtool import company_del as real_company_del
        from src.workspace.commands import lifecycle
        from src.workspace.commands.lifecycle import _run_terminal_cleanup

        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="acme,Acme,,,,\n")
        workspace = self._workspace(tmp_path)
        save_workspace(workspace)
        outcome = {
            "marker": "<!-- terminal-test -->",
            "body": "<!-- terminal-test -->\nclosed",
            "labels": [],
            "close_issue": True,
        }
        state = {
            "remote": TEST_HEAD_OID,
            "local": TEST_HEAD_OID,
            "pr": "OPEN",
            "comment": False,
            "issue": "OPEN",
            "claim": True,
            "worktree": True,
        }
        crashed: set[str] = set()
        events: list[str] = []

        def mutate(name, action):
            events.append(name)
            action()
            if failing == name and name not in crashed:
                crashed.add(name)
                raise RuntimeError(f"crash-{name}")

        def pr_details(*_args, **_kwargs):
            details = _test_pr_details(7, slug="acme", issue=42)
            details["state"] = state["pr"]
            return details

        real_delete_workspace = lifecycle.delete_workspace

        with (
            patch("src.workspace.git.verify_recorded_pr"),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            patch(
                "src.workspace.git.managed_worktree_identity_strict",
                side_effect=lambda *_: (
                    {"head": TEST_HEAD_OID, "dev": 1, "ino": 2} if state["worktree"] else None
                ),
            ),
            patch("src.workspace.git.authenticate_managed_worktree", return_value=True),
            patch(
                "src.workspace.git.remove_authenticated_worktree",
                side_effect=lambda *_args, **_kwargs: state.__setitem__("worktree", False),
            ),
            patch(
                "src.workspace.git.remote_branch_oid_strict",
                side_effect=lambda *_: state["remote"],
            ),
            patch(
                "src.workspace.git.delete_remote_branch_at_expected_oid",
                side_effect=lambda *_args, **_kwargs: mutate(
                    "remote", lambda: state.__setitem__("remote", None)
                ),
            ),
            patch(
                "src.workspace.git.verify_recorded_pr_object",
                side_effect=pr_details,
            ),
            patch(
                "src.workspace.git.close_pr_if_open",
                side_effect=lambda *_: mutate("pr", lambda: state.__setitem__("pr", "CLOSED")),
            ),
            patch(
                "src.workspace.git.local_branch_oid_strict",
                side_effect=lambda *_: state["local"],
            ),
            patch(
                "src.workspace.git.delete_local_branch_at_expected_oid",
                side_effect=lambda *_args, **_kwargs: mutate(
                    "local", lambda: state.__setitem__("local", None)
                ),
            ),
            patch(
                "src.workspace.git.issue_has_comment_marker_strict",
                side_effect=lambda *_: state["comment"],
            ),
            patch(
                "src.workspace.git.comment_on_issue_once",
                side_effect=lambda *_: mutate(
                    "comment", lambda: state.__setitem__("comment", True)
                ),
            ),
            patch(
                "src.workspace.git.issue_state_and_labels_strict",
                side_effect=lambda *_: (state["issue"], set()),
            ),
            patch(
                "src.workspace.git.close_issue_if_open",
                side_effect=lambda *_: mutate(
                    "issue_close", lambda: state.__setitem__("issue", "CLOSED")
                ),
            ),
            patch(
                "src.workspace.git.is_issue_claimed_strict",
                side_effect=lambda *_: state["claim"],
            ),
            patch(
                "src.workspace.git.unclaim_issue_strict",
                side_effect=lambda *_: mutate("claim", lambda: state.__setitem__("claim", False)),
            ),
            patch(
                "src.csvtool.company_del",
                side_effect=lambda slug: mutate("data", lambda: real_company_del(slug)),
            ),
            patch(
                "src.workspace.commands.lifecycle.delete_workspace",
                side_effect=lambda slug: mutate("workspace", lambda: real_delete_workspace(slug)),
            ),
        ):
            with pytest.raises(RuntimeError, match=f"crash-{failing}"):
                _run_terminal_cleanup(load_workspace("acme"), local=False, outcome=outcome)
            journal, completed = lifecycle._load_terminal_journal("acme", issue=42)
            assert journal is not None
            if failing == "claim":
                assert completed is True

            retry_ws = (
                load_workspace("acme")
                if workspace_exists("acme")
                else Workspace(slug="acme", branch="add-company/acme", issue=42)
            )
            _run_terminal_cleanup(retry_ws, local=False, outcome=outcome)

        assert not workspace_exists("acme")
        assert state == {
            "remote": None,
            "local": None,
            "pr": "CLOSED",
            "comment": True,
            "issue": "CLOSED",
            "claim": False,
            "worktree": False,
        }
        assert events[-1] == "claim"

    def test_changed_local_ref_blocks_exact_deletion(self, tmp_path, monkeypatch):
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        workspace = self._workspace(tmp_path)
        workspace.pr = None
        workspace.pr_provenance = {}
        workspace.issue = None
        workspace.worktree_identity["pr"] = None
        workspace.worktree_identity["pr_provenance"] = {}
        workspace.worktree_identity["issue"] = None
        self._materialize_identity(workspace, monkeypatch)
        save_workspace(workspace)
        local = {"oid": TEST_HEAD_OID}
        with (
            patch("src.workspace.git.remote_branch_oid_strict", return_value=None),
            patch("src.workspace.git.local_branch_oid_strict", side_effect=lambda *_: local["oid"]),
        ):
            lifecycle._initialize_terminal_journal(workspace, local=False, outcome=None)
            local["oid"] = "b" * 40
            with pytest.raises(WorkspaceError, match="Local branch changed"):
                lifecycle._run_terminal_cleanup(workspace, local=False)

    def test_repointed_local_ref_blocks_remote_mutation(self, tmp_path, monkeypatch):
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        workspace = self._workspace(tmp_path)
        self._materialize_identity(workspace, monkeypatch)
        save_workspace(workspace)
        local = {"oid": TEST_HEAD_OID}
        with (
            patch("src.workspace.git.verify_recorded_pr"),
            patch("src.workspace.git.remote_branch_oid_strict", return_value=TEST_HEAD_OID),
            patch("src.workspace.git.local_branch_oid_strict", side_effect=lambda *_: local["oid"]),
            patch("src.workspace.git.is_issue_claimed_strict", return_value=True),
        ):
            lifecycle._initialize_terminal_journal(workspace, local=False, outcome=None)

        local["oid"] = "b" * 40
        with (
            patch(
                "src.workspace.git.verify_recorded_pr_object",
                return_value=_test_pr_details(7, slug="acme", issue=42),
            ),
            patch("src.workspace.git.local_branch_oid_strict", side_effect=lambda *_: local["oid"]),
            patch("src.workspace.git.delete_remote_branch_at_expected_oid") as delete_remote,
            pytest.raises(WorkspaceError, match="Local branch changed"),
        ):
            lifecycle._run_terminal_cleanup(workspace, local=False)
        delete_remote.assert_not_called()

    def test_tampered_terminal_schema_is_rejected_before_mutation(self, tmp_path, monkeypatch):
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        ws_obj = self._workspace(tmp_path)
        ws_obj.pr = None
        ws_obj.pr_provenance = {}
        ws_obj.issue = None
        save_workspace(ws_obj)
        with (
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            patch("src.workspace.git.remote_branch_oid_strict", return_value=None),
            patch("src.workspace.git.local_branch_oid_strict", return_value=TEST_HEAD_OID),
        ):
            lifecycle._initialize_terminal_journal(ws_obj, local=False, outcome=None)
        pending = lifecycle._terminal_pending_path("acme")
        data = pending.read_text() + "attacker_completion: true\n"
        pending.write_text(data)
        with pytest.raises(WorkspaceError, match="invalid exact schema"):
            lifecycle._load_terminal_journal("acme")

    def test_completed_receipt_does_not_block_later_issue_for_same_slug(
        self, tmp_path, monkeypatch
    ):
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        first = Workspace(slug="acme", issue=41)
        save_workspace(first)
        lifecycle._run_terminal_cleanup(first, local=True)
        old, completed = lifecycle._load_terminal_journal("acme", issue=41)
        assert old is not None and completed is True

        second = Workspace(slug="acme", issue=42)
        save_workspace(second)
        new = lifecycle._initialize_terminal_journal(second, local=True, outcome=None)
        assert new["journal_id"] != old["journal_id"]
        pending, completed = lifecycle._load_terminal_journal("acme", issue=42)
        assert pending == new
        assert completed is False

    def test_del_retries_from_completed_slug_receipt_without_workspace(self, tmp_path, monkeypatch):
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(slug="acme", issue=42)
        save_workspace(ws_obj)
        lifecycle._run_terminal_cleanup(ws_obj, local=True)
        assert not workspace_exists("acme")

        with patch("src.workspace.commands.lifecycle.is_local_mode", return_value=True):
            result = CliRunner().invoke(ws, ["del", "acme"])

        assert result.exit_code == 0, result.output
        receipt, completed = lifecycle._load_terminal_journal("acme")
        assert receipt is not None and receipt["issue"] == 42 and completed is True

    def test_real_del_cli_recovers_claim_release_crash_from_slug_receipt(
        self, tmp_path, monkeypatch
    ):
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        ws_obj = self._workspace(tmp_path)
        ws_obj.pr = None
        ws_obj.pr_provenance = {}
        save_workspace(ws_obj)
        state = {
            "claimed": True,
            "crashed": False,
            "worktree": True,
            "local": TEST_HEAD_OID,
        }

        def unclaim(_issue):
            state["claimed"] = False
            if not state["crashed"]:
                state["crashed"] = True
                raise RuntimeError("crash-after-claim-release")

        with (
            patch("src.workspace.commands.lifecycle.is_local_mode", return_value=False),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            patch("src.workspace.git.remote_branch_oid_strict", return_value=None),
            patch("src.workspace.git.authenticate_managed_worktree", return_value=True),
            patch(
                "src.workspace.git.managed_worktree_identity_strict",
                side_effect=lambda *_: (
                    {"head": TEST_HEAD_OID, "dev": 1, "ino": 2} if state["worktree"] else None
                ),
            ),
            patch(
                "src.workspace.git.remove_authenticated_worktree",
                side_effect=lambda *_args, **_kwargs: state.__setitem__("worktree", False),
            ),
            patch(
                "src.workspace.git.local_branch_oid_strict",
                side_effect=lambda *_: state["local"],
            ),
            patch(
                "src.workspace.git.delete_local_branch_at_expected_oid",
                side_effect=lambda *_args, **_kwargs: state.__setitem__("local", None),
            ),
            patch(
                "src.workspace.git.is_issue_claimed_strict",
                side_effect=lambda *_: state["claimed"],
            ),
            patch("src.workspace.git.unclaim_issue_strict", side_effect=unclaim),
        ):
            first = CliRunner().invoke(ws, ["del", "acme"])
            assert first.exit_code != 0
            assert "crash-after-claim-release" in str(first.exception)
            assert not workspace_exists("acme")
            receipt, completed = lifecycle._load_terminal_journal("acme")
            assert receipt is not None and completed is True

            retry = CliRunner().invoke(ws, ["del", "acme"])

        assert retry.exit_code == 0, retry.output
        assert state["claimed"] is False

    def test_issue_only_namespace_never_loads_same_named_company(self, tmp_path, monkeypatch):
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        collision = Workspace(slug="issue-42", issue=99, branch="add-company/issue-42")
        save_workspace(collision)
        outcome = {
            "marker": "<!-- issue-only-test -->",
            "body": "<!-- issue-only-test -->\nclosed",
            "labels": [],
            "close_issue": True,
        }

        lifecycle._cleanup_resolver_artifacts(
            issue=42,
            slug=None,
            ws=None,
            local=True,
            outcome=outcome,
        )

        assert load_workspace("issue-42").issue == 99
        receipt, completed = lifecycle._load_issue_terminal_journal(42)
        assert receipt is not None and receipt["namespace"] == "issue" and completed is True

    def test_replaced_active_pointer_is_preserved(self, tmp_path, monkeypatch):
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(slug="acme")
        save_workspace(ws_obj)
        set_active_slug("acme")
        lifecycle._initialize_terminal_journal(ws_obj, local=True, outcome=None)
        active = next((tmp_path / ".ws").glob("active*"))
        active.unlink()
        active.write_text("acme")

        with pytest.raises(WorkspaceError, match="active pointer was replaced"):
            lifecycle._run_terminal_cleanup(ws_obj, local=True)
        assert active.read_text() == "acme"
        assert workspace_exists("acme")

    def test_recreated_active_pointer_token_is_preserved_even_if_inode_is_reused(
        self, tmp_path, monkeypatch
    ):
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(slug="acme")
        save_workspace(ws_obj)
        set_active_slug("acme")
        lifecycle._initialize_terminal_journal(ws_obj, local=True, outcome=None)
        active = next((tmp_path / ".ws").glob("active*"))
        old_record = active.read_text()
        active.unlink()
        set_active_slug("acme")
        replacement = active.read_text()
        assert replacement != old_record

        with pytest.raises(WorkspaceError, match="active pointer was replaced"):
            lifecycle._run_terminal_cleanup(ws_obj, local=True)
        assert active.read_text() == replacement
        assert workspace_exists("acme")

    def test_active_pointer_unlink_crash_resumes(self, tmp_path, monkeypatch):
        from src.workspace.commands import lifecycle
        from src.workspace.safe_cleanup import unlink_child_at as real_unlink

        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(slug="acme")
        save_workspace(ws_obj)
        set_active_slug("acme")
        crashed = {"value": False}

        def unlink(*args, **kwargs):
            real_unlink(*args, **kwargs)
            if not crashed["value"]:
                crashed["value"] = True
                raise RuntimeError("crash-active-unlink")

        with (
            patch("src.workspace.safe_cleanup.unlink_child_at", side_effect=unlink),
            pytest.raises(WorkspaceError, match="safely remove active pointer"),
        ):
            lifecycle._run_terminal_cleanup(ws_obj, local=True)

        lifecycle._run_terminal_cleanup(load_workspace("acme"), local=True)
        assert not workspace_exists("acme")
        assert get_active_slug() is None


class TestReadyRecovery:
    def test_completion_leaves_pr_draft_and_releases_claim(self, tmp_path, monkeypatch):
        from src.workspace.commands.task import _finalize_workflow
        from src.workspace.workflow import WorkflowState, _load_wf_from_disk, _save_wf_to_disk

        _patch_all(monkeypatch, tmp_path)
        save_workspace(
            Workspace(
                slug="test",
                issue=42,
                pr=10,
                branch="add-company/test",
                pr_provenance=_test_pr_provenance(10, issue=42),
                submit_state={"pushed": True},
            )
        )
        _save_wf_to_disk("test", WorkflowState(current_step="reflect"))
        remote = {"oid": TEST_HEAD_OID}
        draft = {"value": True}
        claimed = {"value": True}

        def details(_number):
            value = _test_pr_details(10, slug="test", issue=42)
            value["isDraft"] = draft["value"]
            return value

        with (
            patch("src.workspace.commands.lifecycle.is_local_mode", return_value=False),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            patch("src.workspace.commands.lifecycle._verify_workspace_pr_before_mutation"),
            patch("src.workspace.git.changed_paths_strict", return_value=set()),
            patch(
                "src.workspace.git.remote_branch_oid_strict", side_effect=lambda *_: remote["oid"]
            ),
            patch("src.workspace.git.get_pr_details_strict", side_effect=details),
            patch("src.workspace.git.mark_pr_ready") as ready,
            patch(
                "src.workspace.git.is_issue_claimed_strict", side_effect=lambda *_: claimed["value"]
            ),
            patch(
                "src.workspace.git.unclaim_issue_strict",
                side_effect=lambda *_: claimed.__setitem__("value", False),
            ) as unclaim,
            patch("src.workspace.trace.upload_trace_to_hf", return_value=None),
        ):
            _finalize_workflow("test")

        assert draft["value"] is True
        ready.assert_not_called()
        assert _load_wf_from_disk("test").current_step == "done"
        unclaim.assert_called_once_with(42)

    def test_readiness_race_is_returned_to_draft_and_ambiguous_response_reconciles(
        self, tmp_path, monkeypatch
    ):
        from src.workspace.commands.task import _finalize_workflow
        from src.workspace.workflow import WorkflowState, _load_wf_from_disk, _save_wf_to_disk

        _patch_all(monkeypatch, tmp_path)
        save_workspace(
            Workspace(
                slug="test",
                pr=10,
                branch="add-company/test",
                pr_provenance=_test_pr_provenance(10, issue=None),
                submit_state={"pushed": True},
            )
        )
        _save_wf_to_disk("test", WorkflowState(current_step="reflect"))
        draft = {"value": False}

        def details(_number):
            value = _test_pr_details(10, slug="test", issue=None)
            value["isDraft"] = draft["value"]
            return value

        def draft_side_effect(_number):
            draft["value"] = True
            raise RuntimeError("lost response")

        with (
            patch("src.workspace.commands.lifecycle.is_local_mode", return_value=False),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            patch("src.workspace.commands.lifecycle._verify_workspace_pr_before_mutation"),
            patch("src.workspace.git.changed_paths_strict", return_value=set()),
            patch("src.workspace.git.remote_branch_oid_strict", return_value=TEST_HEAD_OID),
            patch("src.workspace.git.get_pr_details_strict", side_effect=details),
            patch("src.workspace.git.mark_pr_draft", side_effect=draft_side_effect) as mark_draft,
            patch("src.workspace.git.mark_pr_ready") as mark_ready,
            patch("src.workspace.trace.upload_trace_to_hf", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="lost response"):
                _finalize_workflow("test")
            _finalize_workflow("test")

        mark_draft.assert_called_once_with(10)
        mark_ready.assert_not_called()
        assert draft["value"] is True
        assert _load_wf_from_disk("test").current_step == "done"

    def test_readiness_recovery_rechecks_exact_ready_pr_before_draft_mutation(
        self, tmp_path, monkeypatch
    ):
        from src.workspace.commands.task import _finalize_workflow
        from src.workspace.workflow import WorkflowState, _save_wf_to_disk

        _patch_all(monkeypatch, tmp_path)
        save_workspace(
            Workspace(
                slug="test",
                pr=10,
                branch="add-company/test",
                pr_provenance=_test_pr_provenance(10, issue=None),
                submit_state={"pushed": True},
            )
        )
        _save_wf_to_disk("test", WorkflowState(current_step="reflect"))
        ready = _test_pr_details(10, slug="test", issue=None)
        ready["isDraft"] = False
        raced = copy.deepcopy(ready)
        raced["headRefOid"] = "b" * 40
        raced["comments"] = [
            {
                "author": {"login": "reviewer"},
                "createdAt": "2026-08-26T12:00:00Z",
                "body": "Approved at the replacement head.",
            }
        ]

        with (
            patch("src.workspace.commands.lifecycle.is_local_mode", return_value=False),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            patch("src.workspace.commands.lifecycle._verify_workspace_pr_before_mutation"),
            patch("src.workspace.git.changed_paths_strict", return_value=set()),
            patch("src.workspace.git.remote_branch_oid_strict", return_value=TEST_HEAD_OID),
            patch(
                "src.workspace.git.get_pr_details_strict",
                side_effect=[ready, raced],
            ),
            patch("src.workspace.git.mark_pr_draft") as mark_draft,
            patch("src.workspace.git.mark_pr_ready") as mark_ready,
            pytest.raises(WorkspaceError, match="changed while transitioning to ready"),
        ):
            _finalize_workflow("test")

        mark_draft.assert_not_called()
        mark_ready.assert_not_called()

    def test_readiness_race_recovery_posts_issue_audit(self, tmp_path, monkeypatch):
        from src.workspace.commands.task import _finalize_workflow
        from src.workspace.workflow import WorkflowState, _save_wf_to_disk

        _patch_all(monkeypatch, tmp_path)
        save_workspace(
            Workspace(
                slug="test",
                issue=42,
                pr=10,
                branch="add-company/test",
                pr_provenance=_test_pr_provenance(10, issue=42),
                submit_state={"pushed": True},
            )
        )
        _save_wf_to_disk("test", WorkflowState(current_step="reflect"))
        draft = {"value": False}

        def details(_number):
            value = _test_pr_details(10, slug="test", issue=42)
            value["isDraft"] = draft["value"]
            return value

        with (
            patch("src.workspace.commands.lifecycle.is_local_mode", return_value=False),
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            patch("src.workspace.commands.lifecycle._verify_workspace_pr_before_mutation"),
            patch("src.workspace.git.changed_paths_strict", return_value=set()),
            patch("src.workspace.git.remote_branch_oid_strict", return_value=TEST_HEAD_OID),
            patch("src.workspace.git.get_pr_details_strict", side_effect=details),
            patch(
                "src.workspace.git.mark_pr_draft",
                side_effect=lambda _number: draft.__setitem__("value", True),
            ),
            patch("src.workspace.git.comment_on_issue_once") as comment,
            patch("src.workspace.git.is_issue_claimed_strict", return_value=False),
            patch("src.workspace.trace.upload_trace_to_hf", return_value=None),
        ):
            _finalize_workflow("test")

        marker, body = comment.call_args.args[1:]
        assert marker == f"<!-- resolver-ready-race:10:{TEST_HEAD_OID} -->"
        assert "returned to draft" in body

    def test_tampered_ready_schema_blocks_all_mutations(self, tmp_path, monkeypatch):
        from src.workspace.commands.task import _finalize_workflow
        from src.workspace.workflow import WorkflowState, _save_wf_to_disk

        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(
            slug="test",
            pr=10,
            branch="add-company/test",
            pr_provenance=_test_pr_provenance(10),
        )
        ws_obj.ready_state = {
            "version": 3,
            "slug": "test",
            "issue": None,
            "pr": 10,
            "branch": "add-company/test",
            "initial_provenance": _test_pr_provenance(10),
            "initial_head_oid": TEST_HEAD_OID,
            "kb_required": False,
            "kb_publish_oid": None,
            "claim_initially_present": False,
            "attempts": {
                "kb_push": False,
                "draft_recovery": False,
                "workflow_done": False,
                "claim_release": False,
            },
            "ready_confirmed": True,
        }
        save_workspace(ws_obj)
        _save_wf_to_disk("test", WorkflowState(current_step="reflect"))
        with (
            patch("src.workspace.commands.lifecycle.is_local_mode", return_value=False),
            patch("src.workspace.git.mark_pr_ready") as ready,
            pytest.raises(WorkspaceError, match="invalid exact schema"),
        ):
            _finalize_workflow("test")
        ready.assert_not_called()

    def test_del_rejects_unrelated_recorded_pr_before_mutation(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="test,Test,,,\n")
        provenance = _test_pr_provenance(99, issue=None)
        save_workspace(
            Workspace(
                slug="test",
                branch="add-company/test",
                pr=10,
                pr_provenance=provenance,
                worktree=str(tmp_path / "worktrees" / "test"),
                worktree_identity={
                    "version": 1,
                    "path": str(tmp_path / "worktrees" / "test"),
                    "slug": "test",
                    "branch": "add-company/test",
                    "head": TEST_HEAD_OID,
                    "dev": 1,
                    "ino": 2,
                    "issue": None,
                    "pr": 10,
                    "pr_provenance": copy.deepcopy(provenance),
                },
            )
        )

        with (
            patch("src.workspace.commands.lifecycle._authenticate_workspace_worktree"),
            patch("src.workspace.git.worktrees_dir", return_value=tmp_path / "worktrees"),
            patch("src.workspace.git.close_pr_if_open") as close_pr,
            patch("src.workspace.git.delete_branch_at_expected_oid") as delete_branch,
        ):
            result = CliRunner().invoke(ws, ["del", "test"])

        assert result.exit_code != 0
        assert "number/branch does not match" in str(result.exception)
        close_pr.assert_not_called()
        delete_branch.assert_not_called()
        assert workspace_exists("test")


class TestSelectMonitorValidation:
    def test_invalid_monitor_type(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="careers", slug="test-careers", url="https://test.com/jobs"))
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(ws, ["select", "monitor", "test", "nonexistent"])
        assert result.exit_code != 0
        stderr = (result.stderr_bytes or b"").decode()
        assert "Unknown monitor type" in result.output or "Unknown monitor type" in stderr

    def test_valid_monitor_type(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="careers", slug="test-careers", url="https://test.com/jobs"))
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(ws, ["select", "monitor", "test", "greenhouse"])
        assert result.exit_code == 0
        assert "Selected monitor: greenhouse" in result.output

    def test_config_hint_shown_when_no_config(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="careers", slug="test-careers", url="https://test.com/jobs"))
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(ws, ["select", "monitor", "test", "dom"])
        assert result.exit_code == 0
        assert "render" in result.output  # DOM config hint mentions render

    def test_prospective_probe_requires_explicit_application_identity(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(
            alias="careers",
            slug="test-careers",
            url="https://jobs.example.com/?lang=en",
        )
        board.detections["prospective"] = {
            "medium_id": "1000613",
            "page_size": 10,
            "urls": 2,
        }
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "prospective"])

        assert result.exit_code != 0
        assert "requires an explicit application_identity contract" in result.output
        assert load_board("test", "careers").configs == {}

    def test_prospective_selection_accepts_explicit_identity_contract(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board(
            "test",
            Board(
                alias="careers",
                slug="test-careers",
                url="https://jobs.example.com/?lang=en",
            ),
        )
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)
        config = {
            "medium_id": "1000613",
            "application_identity": {
                "link_texts": ["Apply"],
                "source_url_allowlist": r"^https://apply\.example/jobs/[1-9]\d*$",
                "canonical_url_allowlist": r"^https://apply\.example/jobs/[1-9]\d*$",
                "locale_priority": ["en"],
            },
        }

        result = CliRunner().invoke(
            ws,
            ["select", "monitor", "test", "prospective", "--config", json.dumps(config)],
        )

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["prospective"]
        assert selected["monitor_config"] == config
        assert selected["scraper_type"] == "skip"


class TestSelectScraperValidation:
    def test_invalid_scraper_type(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="careers", slug="test-careers", url="https://test.com/jobs"))
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(ws, ["select", "scraper", "test", "nonexistent"])
        assert result.exit_code != 0
        stderr = (result.stderr_bytes or b"").decode()
        assert "Unknown scraper type" in result.output or "Unknown scraper type" in stderr

    def test_valid_scraper_type(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="careers", slug="test-careers", url="https://test.com/jobs"))
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(ws, ["select", "scraper", "test", "json-ld"])
        assert result.exit_code == 0
        assert "Selected scraper: json-ld" in result.output

    def test_scraper_config_hint(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="careers", slug="test-careers", url="https://test.com/jobs"))
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(ws, ["select", "scraper", "test", "json-ld"])
        assert result.exit_code == 0
        assert "Optional: render" in result.output


def _as_run_monitor_result(fake_result, elapsed, http_log, *, monitor_type="sitemap"):
    """Adapter: legacy ``(FakeMonitorResult, elapsed, http_log)`` → ``RunMonitorResult``.

    The CLI handler now consumes ``RunMonitorResult`` from the lib instead
    of the bare tuple it built itself. Test helpers translate so existing
    test bodies (which build ``FakeResult`` dataclasses) keep working.
    """
    from src.workspace.lib.run import RunMonitorResult, _build_monitor_quality

    urls_list = sorted(fake_result.urls)
    has_rich = fake_result.jobs_by_url is not None
    quality = _build_monitor_quality(fake_result.jobs_by_url) if has_rich else None
    desc_samples: list[dict] = []
    if fake_result.jobs_by_url:
        import re as _re

        for job in list(fake_result.jobs_by_url.values())[:5]:
            desc = getattr(job, "description", None)
            if desc:
                plain_desc = _re.sub(r"<[^>]+>", "", desc).strip()
                desc_samples.append({"length": len(plain_desc), "snippet": plain_desc[:200]})
    return RunMonitorResult(
        board_url="https://test.com/jobs",
        monitor_type=monitor_type,
        urls=urls_list,
        jobs_by_url=fake_result.jobs_by_url,
        filtered_count=getattr(fake_result, "filtered_count", 0),
        elapsed_seconds=elapsed,
        has_rich_data=has_rich,
        truncated=bool(getattr(fake_result, "truncated", False)),
        sample_urls=list(urls_list)[:10],
        description_samples=desc_samples,
        quality=quality,
        http_log=list(http_log),
        log_events=[],
    )


def _as_run_scraper_result(items, http_log, skipped, *, scraper_type="json-ld"):
    """Adapter: legacy ``(items, http_log, skipped)`` → ``RunScraperResult``."""
    from src.workspace.lib.run import RunScraperResult, ScrapedJob

    scraped = [ScrapedJob(url=u, content=c, elapsed_seconds=e) for u, c, e in items]
    avg = sum(it.elapsed_seconds for it in scraped) / len(scraped) if scraped else 0.0
    desc_samples: list[dict] = []
    import re as _re

    for it in scraped:
        desc = getattr(it.content, "description", None)
        if desc and len(desc_samples) < 5:
            plain_desc = _re.sub(r"<[^>]+>", "", desc).strip()
            desc_samples.append({"length": len(plain_desc), "snippet": plain_desc[:200]})
    return RunScraperResult(
        scraper_type=scraper_type,
        items=scraped,
        skipped=list(skipped),
        description_samples=desc_samples,
        avg_elapsed_seconds=avg,
        http_log=list(http_log),
        log_events=[],
    )


def _as_probe_scraper_result(entries, spa_suspect, *, sample_urls=None):
    """Adapter: legacy ``(entries, spa_suspect)`` → ``ProbeScraperResult``."""
    from src.workspace.lib.probe import ProbeEntry, ProbeScraperResult

    return ProbeScraperResult(
        sample_urls=list(sample_urls or []),
        entries=[ProbeEntry(name=n, metadata=m, comment=c) for n, m, c in entries],
        spa_suspect=spa_suspect,
    )


def _as_probe_monitor_result(entries, *, board_url="https://test.com/jobs", current_jobs=200):
    """Adapter: legacy ``[(name, metadata, comment), ...]`` → ``ProbeMonitorResult``."""
    from src.workspace.lib.probe import (
        ProbeEntry,
        ProbeMonitorResult,
        score_probe_entries,
    )

    probe_entries = [ProbeEntry(name=n, metadata=m, comment=c) for n, m, c in entries]
    return ProbeMonitorResult(
        board_url=board_url,
        current_jobs=current_jobs,
        entries=probe_entries,
        scored=score_probe_entries(probe_entries, current_jobs),
    )


def _enter_asyncio_run_patch(stack: ExitStack) -> MagicMock:
    """Patch only ``asyncio.run`` and always close the bypassed coroutine."""
    delegate = MagicMock()

    def _run(coro):
        try:
            return delegate(coro)
        finally:
            coro.close()

    stack.enter_context(patch("src.workspace.commands.crawl.asyncio.run", side_effect=_run))
    return delegate


def _enter_monitor_patches(tmp_path) -> tuple[ExitStack, MagicMock]:
    """Enter common patches for run monitor tests. Returns (stack, mock_asyncio).

    The mocked ``asyncio.run`` must return a :class:`RunMonitorResult` since
    the CLI now delegates to ``src.workspace.lib.run.run_monitor``.
    Existing tests pass tuples; use :func:`_as_run_monitor_result` to wrap.
    """
    stack = ExitStack()
    mock_asyncio_run = _enter_asyncio_run_patch(stack)
    stack.enter_context(
        patch(
            "src.workspace.artifacts.monitor_run_dir",
            return_value=tmp_path / "artifacts",
        )
    )
    stack.enter_context(patch("src.workspace.artifacts.save_jobs"))
    stack.enter_context(patch("src.workspace.artifacts.save_quality"))
    stack.enter_context(patch("src.workspace.artifacts.save_http_log"))
    stack.enter_context(patch("src.workspace.artifacts.save_events"))
    stack.enter_context(patch("src.workspace.artifacts.capture_structlog", return_value=[]))
    return stack, MagicMock(run=mock_asyncio_run)


def _enter_scraper_patches(tmp_path) -> tuple[ExitStack, MagicMock]:
    """Enter common patches for run scraper tests. Returns (stack, mock_asyncio).

    The mocked ``asyncio.run`` must return a :class:`RunScraperResult` since
    the CLI now delegates to ``src.workspace.lib.run.run_scraper``.
    Existing tests pass tuples; use :func:`_as_run_scraper_result` to wrap.
    """
    stack = ExitStack()
    mock_asyncio_run = _enter_asyncio_run_patch(stack)
    stack.enter_context(
        patch(
            "src.workspace.artifacts.scraper_run_dir",
            return_value=tmp_path / "artifacts",
        )
    )
    stack.enter_context(patch("src.workspace.artifacts.save_results"))
    stack.enter_context(patch("src.workspace.artifacts.save_quality"))
    stack.enter_context(patch("src.workspace.artifacts.save_http_log"))
    stack.enter_context(patch("src.workspace.artifacts.save_events"))
    stack.enter_context(patch("src.workspace.artifacts.capture_structlog", return_value=[]))
    stack.enter_context(
        patch("random.sample", return_value=["https://test.com/jobs/1", "https://test.com/jobs/2"])
    )
    return stack, MagicMock(run=mock_asyncio_run)


class TestRunMonitorOutput:
    def _setup_monitor_board(self, tmp_path, monkeypatch, monitor_type="sitemap"):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.monitor_type = monitor_type
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

    def _setup_inventory_board(self, tmp_path, monkeypatch):
        from src.workspace.ats_seed import apply_inventory_seed, parse_inventory_seed

        _patch_all(monkeypatch, tmp_path)
        workspace = Workspace(slug="acme", issue=1)
        seed = parse_inventory_seed(_inventory_issue_body())
        assert seed is not None
        board = apply_inventory_seed(workspace, seed)
        save_workspace(workspace)
        save_board("acme", board)

    def test_zero_jobs_warning(self, tmp_path, monkeypatch):
        """0 jobs should produce a warning, not a regular info line."""
        self._setup_monitor_board(tmp_path, monkeypatch)

        @dataclass
        class FakeResult:
            urls: set[str]
            jobs_by_url: dict | None
            filtered_count: int = 0

        fake_result = FakeResult(urls=set(), jobs_by_url=None)

        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_monitor_result(fake_result, 1.5, [])
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor", "test"])

        assert "\u26a0" in result.output  # Warning symbol
        assert "0 jobs" in result.output

    def test_nonzero_jobs_info(self, tmp_path, monkeypatch):
        """Non-zero jobs should produce a regular info line."""
        self._setup_monitor_board(tmp_path, monkeypatch)

        @dataclass
        class FakeResult:
            urls: set[str]
            jobs_by_url: dict | None
            filtered_count: int = 0

        fake_result = FakeResult(
            urls={"https://test.com/jobs/1", "https://test.com/jobs/2"},
            jobs_by_url=None,
        )

        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_monitor_result(fake_result, 2.0, [])
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor", "test"])

        assert "\u2713" in result.output  # Checkmark symbol
        assert "2 jobs" in result.output

    def test_truncated_run_is_persisted_and_warned(self, tmp_path, monkeypatch):
        self._setup_monitor_board(tmp_path, monkeypatch)

        @dataclass
        class FakeResult:
            urls: set[str]
            jobs_by_url: dict | None
            filtered_count: int = 0
            truncated: bool = True

        fake_result = FakeResult(
            urls={"https://test.com/jobs/1"},
            jobs_by_url=None,
        )
        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_monitor_result(fake_result, 2.0, [])
            result = CliRunner().invoke(ws, ["run", "monitor", "test"])

        assert result.exit_code == 0, result.output
        assert "incomplete/truncated" in result.output
        board = load_board("test", "careers")
        assert board.monitor_run["truncated"] is True

    def test_inventory_seed_success_is_verified(self, tmp_path, monkeypatch):
        self._setup_inventory_board(tmp_path, monkeypatch)

        @dataclass
        class FakeResult:
            urls: set[str]
            jobs_by_url: dict | None
            filtered_count: int = 0

        fake_result = FakeResult(
            urls={"https://boards.greenhouse.io/acme/jobs/1"},
            jobs_by_url=None,
        )
        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:
            expected = _as_run_monitor_result(fake_result, 1.0, [])

            def _succeed(coro):
                from src.workspace.state import update_workspace

                coro.close()
                # Parallel enrichment can finish while the monitor is in
                # flight; the seed-status write must not restore stale state.
                with update_workspace("acme") as concurrent_workspace:
                    concurrent_workspace.name = "Acme Concurrent"
                return expected

            mock_asyncio.run.side_effect = _succeed
            result = CliRunner().invoke(
                ws,
                ["run", "monitor", "acme", "--board", "careers", "--config", "inventory-seed"],
            )

        assert result.exit_code == 0, result.output
        assert load_workspace("acme").ats_inventory["status"] == "verified"
        assert load_workspace("acme").ats_inventory["jobs"] == 1
        assert load_workspace("acme").name == "Acme Concurrent"
        assert load_board("acme", "careers").configs["inventory-seed"]["status"] == "tested"

    def test_inventory_seed_zero_jobs_forces_normal_fallback(self, tmp_path, monkeypatch):
        self._setup_inventory_board(tmp_path, monkeypatch)

        @dataclass
        class FakeResult:
            urls: set[str]
            jobs_by_url: dict | None
            filtered_count: int = 0

        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:
            expected = _as_run_monitor_result(
                FakeResult(urls=set(), jobs_by_url=None),
                1.0,
                [],
            )

            def _succeed(coro):
                coro.close()
                return expected

            mock_asyncio.run.side_effect = _succeed
            result = CliRunner().invoke(
                ws,
                ["run", "monitor", "acme", "--board", "careers", "--config", "inventory-seed"],
            )

        assert result.exit_code == 0, result.output
        assert "Fast path rejected" in result.output
        assert load_workspace("acme").ats_inventory["status"] == "fallback"
        board = load_board("acme", "careers")
        assert board.configs["inventory-seed"]["status"] == "selected"
        assert board.ready is False

    def test_inventory_seed_monitor_failure_records_fallback(self, tmp_path, monkeypatch):
        self._setup_inventory_board(tmp_path, monkeypatch)
        from src.workspace.lib import WsMonitorRunFailed

        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:

            def _fail(coro):
                coro.close()
                raise WsMonitorRunFailed("seed endpoint is stale")

            mock_asyncio.run.side_effect = _fail
            result = CliRunner().invoke(
                ws,
                ["run", "monitor", "acme", "--board", "careers", "--config", "inventory-seed"],
            )

        assert result.exit_code != 0
        assert "use normal probe/discovery" in result.output
        state = load_workspace("acme").ats_inventory
        assert state["status"] == "fallback"
        assert state["reason"] == "seed endpoint is stale"

    def test_monitor_failure_shows_recovery_guidance(self, tmp_path, monkeypatch):
        self._setup_monitor_board(tmp_path, monkeypatch, monitor_type="greenhouse")

        from src.workspace.lib import WsMonitorRunFailed

        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:

            def _fail(coro):
                coro.close()
                # The lib normally wraps the underlying ValueError in
                # WsMonitorRunFailed; we simulate that here since the
                # mocked asyncio.run bypasses the lib body.
                inner = ValueError(
                    "Cannot derive Greenhouse token from board URL "
                    "'https://test.com/jobs' and no token in metadata"
                )
                wrapped = WsMonitorRunFailed(str(inner))
                wrapped.__cause__ = inner
                raise wrapped

            mock_asyncio.run.side_effect = _fail
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor", "test"])

        assert result.exit_code != 0
        assert "Run failed:" in result.output
        assert "ws probe monitor -n <current-job-count>" in result.output
        assert "ws help monitor greenhouse" in result.output
        assert "ws select monitor greenhouse --config" in result.output
        assert "Traceback" not in result.output

    def test_rich_data_quality_with_optional_fields(self, tmp_path, monkeypatch):
        """Rich data should show quality including optional fields."""
        self._setup_monitor_board(tmp_path, monkeypatch, monitor_type="greenhouse")

        from src.core.monitors import DiscoveredJob

        jobs = {
            "https://test.com/jobs/1": DiscoveredJob(
                url="https://test.com/jobs/1",
                title="Engineer",
                description="<p>Build</p>",
                locations=["NYC"],
                employment_type="FULL_TIME",
                date_posted="2026-01-01",
            ),
            "https://test.com/jobs/2": DiscoveredJob(
                url="https://test.com/jobs/2",
                title="Designer",
                description="<p>Design</p>",
                locations=["SF"],
            ),
        }

        @dataclass
        class FakeResult:
            urls: set[str]
            jobs_by_url: dict | None
            filtered_count: int = 0

        fake_result = FakeResult(urls=set(jobs.keys()), jobs_by_url=jobs)

        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_monitor_result(fake_result, 1.0, [])
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor", "test"])

        assert "Quality:" in result.output
        assert "2/2 title" in result.output
        assert "Optional:" in result.output
        assert "employment_type" in result.output


class TestRunScraperOutput:
    def _setup_board_with_monitor(self, tmp_path, monkeypatch, scraper_type="json-ld"):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.monitor_type = "sitemap"
        board.scraper_type = scraper_type
        board.monitor_run = {"sample_urls": ["https://test.com/jobs/1", "https://test.com/jobs/2"]}
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

    def test_desc_column_in_table(self, tmp_path, monkeypatch):
        """Results table should include a Desc column."""
        self._setup_board_with_monitor(tmp_path, monkeypatch)

        from src.core.scrapers import JobContent

        contents = [
            JobContent(title="Engineer", description="<p>Build things</p>", locations=["NYC"]),
            JobContent(title="Designer", description=None, locations=["SF"]),
        ]

        stack, mock_asyncio = _enter_scraper_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_scraper_result(
                [
                    ("https://test.com/jobs/1", contents[0], 0.5),
                    ("https://test.com/jobs/2", contents[1], 0.3),
                ],
                [],
                [],
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "scraper", "test"])

        assert "Desc" in result.output
        assert "descriptions" in result.output

    def test_zero_titles_warns(self, tmp_path, monkeypatch):
        """0 titles extracted should warn and suggest different scraper."""
        self._setup_board_with_monitor(tmp_path, monkeypatch)

        from src.core.scrapers import JobContent

        contents = [
            JobContent(title=None, description=None, locations=None),
            JobContent(title=None, description=None, locations=None),
        ]

        stack, mock_asyncio = _enter_scraper_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_scraper_result(
                [
                    ("https://test.com/jobs/1", contents[0], 0.5),
                    ("https://test.com/jobs/2", contents[1], 0.3),
                ],
                [],
                [],
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "scraper", "test"])

        assert "No titles extracted" in result.output
        assert "ws select scraper dom" in result.output

    def test_optional_fields_shown(self, tmp_path, monkeypatch):
        """Optional fields with data should be shown in output."""
        self._setup_board_with_monitor(tmp_path, monkeypatch)

        from src.core.scrapers import JobContent

        contents = [
            JobContent(
                title="Engineer",
                description="<p>Hi</p>",
                locations=["NYC"],
                employment_type="FULL_TIME",
                date_posted="2026-01-01",
                extras={"skills": ["Python", "SQL"]},
            ),
            JobContent(title="Designer", description="<p>Hi</p>", locations=["SF"]),
        ]

        stack, mock_asyncio = _enter_scraper_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_scraper_result(
                [
                    ("https://test.com/jobs/1", contents[0], 0.5),
                    ("https://test.com/jobs/2", contents[1], 0.3),
                ],
                [],
                [],
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "scraper", "test"])

        assert "Optional:" in result.output
        assert "employment_type" in result.output
        assert "skills" in result.output

    def test_content_samples_shown(self, tmp_path, monkeypatch):
        """ws run scraper should show extracted content grouped by field."""
        self._setup_board_with_monitor(tmp_path, monkeypatch)

        from src.core.scrapers import JobContent

        contents = [
            JobContent(title="Engineer", description="<p>Build things</p>", locations=["NYC"]),
            JobContent(title="Designer", description="<p>Design things</p>", locations=["SF"]),
        ]

        stack, mock_asyncio = _enter_scraper_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_scraper_result(
                [
                    ("https://test.com/jobs/1", contents[0], 0.5),
                    ("https://test.com/jobs/2", contents[1], 0.3),
                ],
                [],
                [],
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "scraper", "test"])

        assert "Extracted content:" in result.output
        assert "title:" in result.output
        assert "[0] Engineer" in result.output
        assert "[1] Designer" in result.output
        assert "locations:" in result.output
        assert "NYC" in result.output

    def test_content_samples_truncates_long_strings(self, tmp_path, monkeypatch):
        """Long strings (like descriptions) should be truncated in content samples."""
        self._setup_board_with_monitor(tmp_path, monkeypatch)

        from src.core.scrapers import JobContent

        long_desc = "<p>" + "x" * 200 + "</p>"
        contents = [
            JobContent(title="Engineer", description=long_desc, locations=["NYC"]),
        ]

        stack, mock_asyncio = _enter_scraper_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_scraper_result(
                [("https://test.com/jobs/1", contents[0], 0.5)],
                [],
                [],
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "scraper", "test"])

        assert "Extracted content:" in result.output
        # Should be truncated with ellipsis
        assert "\u2026" in result.output


class TestRunMonitorVerifyPrompt:
    def _setup_monitor_board(self, tmp_path, monkeypatch, monitor_type="sitemap"):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.monitor_type = monitor_type
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

    def test_verify_prompt_shown(self, tmp_path, monkeypatch):
        """Non-zero job count should show verification prompt."""
        self._setup_monitor_board(tmp_path, monkeypatch)

        @dataclass
        class FakeResult:
            urls: set[str]
            jobs_by_url: dict | None
            filtered_count: int = 0

        fake_result = FakeResult(
            urls={"https://test.com/jobs/1", "https://test.com/jobs/2"},
            jobs_by_url=None,
        )

        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_monitor_result(fake_result, 2.0, [])
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor", "test"])

        assert "Verify: compare this count" in result.output

    def test_no_verify_prompt_on_zero_jobs(self, tmp_path, monkeypatch):
        """0 jobs should not show verification prompt."""
        self._setup_monitor_board(tmp_path, monkeypatch)

        @dataclass
        class FakeResult:
            urls: set[str]
            jobs_by_url: dict | None
            filtered_count: int = 0

        fake_result = FakeResult(urls=set(), jobs_by_url=None)

        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_monitor_result(fake_result, 1.0, [])
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor", "test"])

        assert "Verify: compare this count" not in result.output


class TestRunMonitorNamedConfig:
    """Tests for ws run monitor --config <name>."""

    def test_named_config_writes_to_correct_entry(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(
            alias="careers",
            slug="test-careers",
            url="https://test.com/jobs",
            active_config="sitemap",
            configs={
                "sitemap": {
                    "monitor_type": "sitemap",
                    "monitor_config": {},
                    "status": "selected",
                },
                "greenhouse": {
                    "monitor_type": "greenhouse",
                    "monitor_config": {"token": "abc"},
                    "status": "selected",
                },
            },
        )
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        @dataclass
        class FakeResult:
            urls: set[str]
            jobs_by_url: dict | None
            filtered_count: int = 0

        fake_result = FakeResult(
            urls={"https://test.com/jobs/1", "https://test.com/jobs/2"},
            jobs_by_url=None,
        )

        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_monitor_result(fake_result, 1.0, [])
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor", "test", "--config", "greenhouse"])

        assert result.exit_code == 0
        board = load_board("test", "careers")
        assert board.configs["greenhouse"]["status"] == "tested"
        assert board.configs["greenhouse"]["run"]["jobs"] == 2
        # Active config should be unchanged
        assert board.active_config == "sitemap"
        assert board.configs["sitemap"]["status"] == "selected"

    def test_nonexistent_config_errors(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(
            alias="careers",
            slug="test-careers",
            url="https://test.com/jobs",
            active_config="sitemap",
            configs={"sitemap": {"monitor_type": "sitemap", "status": "selected"}},
        )
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(ws, ["run", "monitor", "test", "--config", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output


class TestRunScraperNamedConfig:
    """Tests for ws run scraper --config <name>."""

    def test_named_config_writes_to_correct_entry(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(
            alias="careers",
            slug="test-careers",
            url="https://test.com/jobs",
            active_config="sitemap",
            configs={
                "sitemap": {
                    "monitor_type": "sitemap",
                    "scraper_type": "json-ld",
                    "scraper_config": {},
                    "status": "tested",
                    "run": {
                        "jobs": 5,
                        "sample_urls": [
                            "https://test.com/jobs/1",
                            "https://test.com/jobs/2",
                        ],
                    },
                },
                "alt": {
                    "monitor_type": "dom",
                    "scraper_type": "dom",
                    "scraper_config": {},
                    "status": "tested",
                    "run": {
                        "jobs": 3,
                        "sample_urls": [
                            "https://test.com/jobs/3",
                            "https://test.com/jobs/4",
                        ],
                    },
                },
            },
        )
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        from src.core.scrapers import JobContent

        fake_content = JobContent(
            title="Engineer",
            description="<p>Build things</p>",
            locations=["NYC"],
        )

        stack, mock_asyncio = _enter_scraper_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_run_scraper_result(
                [("https://test.com/jobs/3", fake_content, 0.5)],
                [],
                [],
                scraper_type="dom",
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "scraper", "test", "--config", "alt"])

        assert result.exit_code == 0
        board = load_board("test", "careers")
        assert board.configs["alt"]["status"] == "tested"
        assert board.configs["alt"]["scraper_run"]["count"] == 1
        # Active config unchanged
        assert board.active_config == "sitemap"

    def test_nonexistent_config_errors(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(
            alias="careers",
            slug="test-careers",
            url="https://test.com/jobs",
            active_config="sitemap",
            configs={
                "sitemap": {
                    "monitor_type": "sitemap",
                    "scraper_type": "json-ld",
                    "status": "tested",
                }
            },
        )
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(ws, ["run", "scraper", "test", "--config", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output


def _enter_probe_scraper_patches(tmp_path) -> tuple[ExitStack, MagicMock]:
    """Enter common patches for probe scraper tests. Returns (stack, mock_asyncio)."""
    stack = ExitStack()
    mock_asyncio_run = _enter_asyncio_run_patch(stack)
    stack.enter_context(
        patch(
            "src.workspace.artifacts.scraper_probe_run_dir",
            return_value=tmp_path / "artifacts",
        )
    )
    stack.enter_context(patch("src.workspace.artifacts.save_probe"))
    return stack, MagicMock(run=mock_asyncio_run)


class TestProbeScraperQualityGate:
    def _setup_board_with_monitor(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.monitor_type = "sitemap"
        board.monitor_run = {"sample_urls": ["https://test.com/jobs/1"]}
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

    def test_spa_warning_shown(self, tmp_path, monkeypatch):
        """SPA suspect should show warning in probe output."""
        self._setup_board_with_monitor(tmp_path, monkeypatch)

        fake_results = [
            ("json-ld", None, "Not detected"),
            ("nextdata", None, "Not detected"),
            ("dom", None, "Not detected"),
            ("api_sniffer", None, "Not detected (0/1 pages had XHR job data)"),
        ]

        stack, mock_asyncio = _enter_probe_scraper_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = _as_probe_scraper_result(
                fake_results, True, sample_urls=["https://test.com/jobs/1"]
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["probe", "scraper", "test"])

        assert "JS-rendered" in result.output or "SPA" in result.output


# ── Phase 4: Named Configs, Feedback, Quality Gates ──────────────────


class TestSelectMonitorNaming:
    """Test --as naming and auto-naming for ws select monitor."""

    def _setup(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

    def test_explicit_name(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(ws, ["select", "monitor", "test", "greenhouse", "--as", "gh-api"])
        assert result.exit_code == 0
        assert "gh-api" in result.output

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        assert board.active_config == "gh-api"
        assert board.configs["gh-api"]["monitor_type"] == "greenhouse"
        assert board.configs["gh-api"]["status"] == "selected"

    def test_auto_name_first(self, tmp_path, monkeypatch):
        """First select without --as uses the type as name."""
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(ws, ["select", "monitor", "test", "greenhouse"])
        assert result.exit_code == 0

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        assert board.active_config == "greenhouse"

    def test_auto_name_increment(self, tmp_path, monkeypatch):
        """Second select with same type gets -2 suffix."""
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        runner.invoke(ws, ["select", "monitor", "test", "greenhouse"])
        runner.invoke(ws, ["select", "monitor", "test", "greenhouse"])

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        assert board.active_config == "greenhouse-2"
        assert "greenhouse" in board.configs
        assert "greenhouse-2" in board.configs
        assert board.configs["greenhouse"]["status"] == "untested"
        assert board.configs["greenhouse-2"]["status"] == "selected"

    def test_auto_scraper_config_is_persisted(self, tmp_path, monkeypatch):
        """Partial-rich monitors retain their required enrichment config."""
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()

        result = runner.invoke(ws, ["select", "monitor", "test", "paylocity"])

        assert result.exit_code == 0
        board = load_board("test", "careers")
        selected = board.configs[board.active_config]
        assert selected["scraper_type"] == "paylocity"
        assert selected["scraper_config"] == {
            "enrich": ["description", "employment_type", "job_location_type"]
        }

        # Mutating workspace state must not mutate the reusable compatibility
        # default returned for subsequent monitor selections.
        selected["scraper_config"]["enrich"].append("title")
        from src.workspace._compat import auto_scraper_type

        assert auto_scraper_type("paylocity") == (
            "paylocity",
            {"enrich": ["description", "employment_type", "job_location_type"]},
        )

    def test_mokahr_detail_enrichment_is_persisted(self, tmp_path, monkeypatch):
        """Mokahr listing metadata must schedule the native detail scraper."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "mokahr"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["mokahr"]
        assert selected["scraper_type"] == "mokahr"
        assert selected["scraper_config"] == {"enrich": ["description"]}

    def test_legacy_successfactors_static_enrichment_is_persisted(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        config = json.dumps(
            {
                "preset": "successfactors",
                "variant": "legacy",
                "host": "career5.successfactors.eu",
                "company": "Acme",
            }
        )

        result = CliRunner().invoke(
            ws,
            ["select", "monitor", "test", "rss", "--config", config],
        )

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["rss"]
        assert selected["scraper_type"] == "dom"
        assert selected["scraper_config"]["scope"] == ".joqReqDescription"
        assert selected["scraper_config"]["enrich"] == ["description"]

    def test_bamboohr_api_scraper_preset_is_persisted(self, tmp_path, monkeypatch):
        """BambooHR selection carries its complete generic detail API preset."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "bamboohr"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["bamboohr"]
        from src.workspace._compat import auto_scraper_type

        expected = auto_scraper_type("bamboohr")
        assert expected is not None
        assert selected["scraper_type"] == expected[0] == "api_sniffer"
        assert selected["scraper_config"] == expected[1]

    def test_paycom_scraper_preset_is_persisted(self, tmp_path, monkeypatch):
        """Paycom selection carries its native detail API preset."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "paycom"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["paycom"]
        from src.workspace._compat import auto_scraper_type

        expected = auto_scraper_type("paycom")
        assert expected is not None
        assert selected["scraper_type"] == expected[0] == "paycom"
        assert selected["scraper_config"] == expected[1]

    def test_adp_scraper_preset_is_persisted(self, tmp_path, monkeypatch):
        """ADP selection carries its native detail enrichment preset."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "adp"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["adp"]
        from src.workspace._compat import auto_scraper_type

        expected = auto_scraper_type("adp")
        assert expected is not None
        assert selected["scraper_type"] == expected[0] == "adp"
        assert selected["scraper_config"] == expected[1]

    def test_avature_dom_scraper_preset_is_persisted(self, tmp_path, monkeypatch):
        """Avature selection activates the shared DOM detail preset."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "avature"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["avature"]
        from src.workspace._compat import auto_scraper_type

        expected = auto_scraper_type("avature")
        assert expected is not None
        assert selected["scraper_type"] == expected[0] == "dom"
        assert selected["scraper_config"] == expected[1]

    def test_jazzhr_scraper_preset_is_persisted(self, tmp_path, monkeypatch):
        """JazzHR selection carries its composed static detail scraper."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "jazzhr"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["jazzhr"]
        assert selected["scraper_type"] == "jazzhr"
        assert selected.get("scraper_config") is None

    def test_recruiterbox_scraper_preset_is_persisted(self, tmp_path, monkeypatch):
        """Recruiterbox selection activates the shared JSON-LD detail scraper."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "recruiterbox"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["recruiterbox"]
        assert selected["scraper_type"] == "json-ld"
        assert selected.get("scraper_config") is None

    def test_icims_scraper_preset_is_persisted(self, tmp_path, monkeypatch):
        """iCIMS selection carries the existing JSON-LD detail scraper."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "icims"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["icims"]
        assert selected["scraper_type"] == "json-ld"
        assert selected.get("scraper_config") is None

    def test_herp_scraper_preset_is_persisted(self, tmp_path, monkeypatch):
        """HERP selection carries the existing JSON-LD detail scraper."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "herp"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["herp"]
        assert selected["scraper_type"] == "json-ld"
        assert selected.get("scraper_config") is None

    def test_gupy_scraper_preset_is_persisted(self, tmp_path, monkeypatch):
        """Gupy selection carries the existing JSON-LD detail scraper."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "gupy"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["gupy"]
        assert selected["scraper_type"] == "json-ld"
        assert selected.get("scraper_config") is None

    def test_cornerstone_rich_monitor_skip_is_persisted(self, tmp_path, monkeypatch):
        """Cornerstone selection skips redundant per-job scraping."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "cornerstone"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["cornerstone"]
        assert selected["scraper_type"] == "skip"
        assert selected.get("scraper_config") is None

    def test_dayforce_rich_monitor_skip_is_persisted(self, tmp_path, monkeypatch):
        """Dayforce selection skips redundant per-job scraping."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "dayforce"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["dayforce"]
        assert selected["scraper_type"] == "skip"
        assert selected.get("scraper_config") is None

    def test_darwinbox_rich_monitor_skip_is_persisted(self, tmp_path, monkeypatch):
        """Darwinbox selection skips redundant per-job scraping."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "darwinbox"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["darwinbox"]
        assert selected["scraper_type"] == "skip"
        assert selected.get("scraper_config") is None

    def test_hrmos_scraper_preset_is_persisted(self, tmp_path, monkeypatch):
        """HRMOS selection carries the existing JSON-LD detail scraper."""
        self._setup(tmp_path, monkeypatch)

        result = CliRunner().invoke(ws, ["select", "monitor", "test", "hrmos"])

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["hrmos"]
        assert selected["scraper_type"] == "json-ld"
        assert selected.get("scraper_config") is None

    def test_reselect_monitor_preserves_explicit_empty_scraper_config(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        board = load_board("test", "careers")
        board.configs["custom"] = {
            "scraper_type": "paylocity",
            "scraper_config": {},
        }
        save_board("test", board)

        result = CliRunner().invoke(
            ws,
            ["select", "monitor", "test", "paylocity", "--as", "custom"],
        )

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["custom"]
        assert selected["scraper_type"] == "paylocity"
        assert selected["scraper_config"] == {}

    def test_reselect_monitor_repairs_v1_null_scraper_placeholders(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        legacy = Board.from_dict(
            {
                "alias": "careers",
                "slug": "test-careers",
                "url": "https://test.com/jobs",
                "monitor": {"type": "paylocity", "config": {}},
                "scraper": {},
            }
        )
        save_board("test", legacy)

        result = CliRunner().invoke(
            ws,
            ["select", "monitor", "test", "paylocity", "--as", "paylocity"],
        )

        assert result.exit_code == 0
        selected = load_board("test", "careers").configs["paylocity"]
        assert selected["scraper_type"] == "paylocity"
        assert selected["scraper_config"] == {
            "enrich": ["description", "employment_type", "job_location_type"]
        }

    def test_auto_fill_from_detections(self, tmp_path, monkeypatch):
        """Config auto-fills from board.detections when no --config given."""
        self._setup(tmp_path, monkeypatch)

        from src.workspace.state import load_board, save_board

        board = load_board("test", "careers")
        board.detections["greenhouse"] = {"token": "stripe"}
        save_board("test", board)

        runner = CliRunner()
        result = runner.invoke(ws, ["select", "monitor", "test", "greenhouse"])
        assert result.exit_code == 0
        assert "Auto-filled" in result.output

        board = load_board("test", "careers")
        assert board.configs["greenhouse"]["monitor_config"]["token"] == "stripe"


class TestSelectConfig:
    """Test ws select config <name>."""

    def _setup(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh-api"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
        }
        board.configs["sitemap-v1"] = {
            "monitor_type": "sitemap",
            "monitor_config": {},
            "status": "tested",
        }
        board.active_config = "gh-api"
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

    def test_reactivate(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(ws, ["select", "config", "sitemap-v1", "test"])
        assert result.exit_code == 0
        assert "sitemap-v1" in result.output

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        assert board.active_config == "sitemap-v1"

    def test_reactivate_demotes_stale_selected_sibling(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        board = load_board("test", "careers")
        board.configs["gh-api"]["status"] = "selected"
        board.configs["gh-api"]["run"] = {"jobs": 10}
        save_board("test", board)

        result = CliRunner().invoke(ws, ["select", "config", "sitemap-v1", "test"])

        assert result.exit_code == 0
        board = load_board("test", "careers")
        assert board.configs["gh-api"]["status"] == "tested"

    def test_nonexistent_config(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(ws, ["select", "config", "nonexistent", "test"])
        assert result.exit_code != 0


class TestRejectConfig:
    """Test ws reject-config <name> --reason '...'."""

    def _setup(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh-api"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
        }
        board.active_config = "gh-api"
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

    def test_reject(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            ws, ["reject-config", "gh-api", "test", "--reason", "Too many false positives"]
        )
        assert result.exit_code == 0
        assert "Rejected" in result.output

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        assert board.configs["gh-api"]["status"] == "rejected"
        assert "false positives" in board.configs["gh-api"]["rejection_reason"]
        # Active config should be cleared
        assert board.active_config is None

    def test_reject_nonexistent(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(ws, ["reject-config", "nonexistent", "test", "--reason", "Bad"])
        assert result.exit_code != 0


class TestFeedback:
    """Test ws feedback command."""

    def _setup(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh-api"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 10, "quality": {"title": 10, "description": 10, "locations": 8}},
        }
        board.active_config = "gh-api"
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

    def test_good_feedback(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            ws,
            [
                "feedback",
                "gh-api",
                "test",
                "--title",
                "clean",
                "--description",
                "clean",
                "--locations",
                "clean",
                "--verdict",
                "good",
                "--verdict-notes",
                "All fields clean, 10 jobs",
            ],
        )
        assert result.exit_code == 0
        assert "good" in result.output

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        fb = board.configs["gh-api"]["feedback"]
        assert fb["verdict"] == "good"
        assert fb["verdict_notes"] == "All fields clean, 10 jobs"
        assert fb["fields"]["title"]["quality"] == "clean"
        assert fb["fields"]["description"]["quality"] == "clean"
        assert fb["fields"]["locations"]["quality"] == "clean"

    def test_feedback_no_per_field_data_omits_coverage(self, tmp_path, monkeypatch):
        """When run.quality is empty (URL-only monitor + no scraper sample),
        feedback should NOT synthesize misleading "0/N" coverage strings.
        Quality stored without a fraction; tier summary still computes.
        """
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="urlonly"))
        board = Board(alias="careers", slug="urlonly-careers", url="https://x.com/jobs")
        # URL-only monitor: jobs counted, but no per-field quality dict.
        board.configs["wd"] = {
            "monitor_type": "workday",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 617},  # no "quality" key — agent's ws run scraper failed
        }
        board.active_config = "wd"
        save_board("urlonly", board)
        ws_obj = load_workspace("urlonly")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(
            ws,
            [
                "feedback",
                "wd",
                "urlonly",
                "--title",
                "clean",
                "--description",
                "clean",
                "--locations",
                "clean",
                "--verdict",
                "good",
                "--verdict-notes",
                "Verified manually via direct API",
            ],
        )
        assert result.exit_code == 0, result.output

        from src.workspace.state import load_board

        board = load_board("urlonly", "careers")
        fb = board.configs["wd"]["feedback"]
        # No misleading "0/617" — agent verified manually.
        assert fb["fields"]["title"]["quality"] == "clean"
        assert "coverage" not in fb["fields"]["title"], fb["fields"]["title"]
        assert "coverage" not in fb["fields"]["description"]
        # Tier summary computes without crashing on missing coverage.
        assert "required" in fb
        assert fb["required"]["coverage"] == "0/0"

    def test_feedback_replaces_seeded_null_feedback(self, tmp_path, monkeypatch):
        """Inventory-seeded configs persist feedback after starting at null."""
        self._setup(tmp_path, monkeypatch)
        board = load_board("test", "careers")
        board.configs["gh-api"]["feedback"] = None
        save_board("test", board)

        result = CliRunner().invoke(
            ws,
            [
                "feedback",
                "gh-api",
                "test",
                "--title",
                "clean",
                "--description",
                "clean",
                "--locations",
                "clean",
                "--verdict",
                "good",
                "--verdict-notes",
                "Validated inventory seed",
            ],
        )

        assert result.exit_code == 0, result.output
        feedback = load_board("test", "careers").configs["gh-api"]["feedback"]
        assert feedback["verdict"] == "good"
        assert "Required:" in result.output

    def test_feedback_default_to_active_config(self, tmp_path, monkeypatch):
        """When name is omitted, uses active config."""
        self._setup(tmp_path, monkeypatch)
        set_active_slug("test")
        runner = CliRunner()
        result = runner.invoke(
            ws,
            [
                "feedback",
                "--title",
                "clean",
                "--description",
                "clean",
                "--locations",
                "clean",
                "--verdict",
                "good",
                "--verdict-notes",
                "Default config test",
            ],
        )
        assert result.exit_code == 0

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        assert "feedback" in board.configs["gh-api"]

    def test_verified_empty_board_feedback(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="empty"))
        board = Board(alias="careers", slug="empty-careers", url="https://x.com/jobs")
        board.configs["rss"] = {
            "monitor_type": "rss",
            "monitor_config": {"preset": "teamtailor"},
            "status": "tested",
            "run": {"jobs": 0},
        }
        board.active_config = "rss"
        save_board("empty", board)
        ws_obj = load_workspace("empty")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(
            ws,
            [
                "feedback",
                "rss",
                "empty",
                "--verified-empty-board",
                "--verdict",
                "acceptable",
                "--verdict-notes",
                "Official board has no openings; valid Teamtailor RSS is empty",
            ],
        )
        assert result.exit_code == 0, result.output

        board = load_board("empty", "careers")
        feedback = board.configs["rss"]["feedback"]
        assert feedback["verified_empty_board"] is True
        assert feedback["fields"] == {}
        assert feedback["required"]["quality"] == "unverified"

    def test_feedback_auto_populates_absent(self, tmp_path, monkeypatch):
        """Fields with 0/N coverage auto-populate as absent (even important ones)."""
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            ws,
            [
                "feedback",
                "gh-api",
                "test",
                "--title",
                "clean",
                "--description",
                "clean",
                "--locations",
                "clean",
                "--verdict",
                "good",
                "--verdict-notes",
                "Auto-absent test",
            ],
        )
        assert result.exit_code == 0

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        fb = board.configs["gh-api"]["feedback"]
        # employment_type has 0/10 → auto-absent (even though it's important)
        assert fb["fields"]["employment_type"]["quality"] == "absent"
        # job_location_type has 0/10 → auto-absent
        assert fb["fields"]["job_location_type"]["quality"] == "absent"

    def test_feedback_with_notes(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            ws,
            [
                "feedback",
                "gh-api",
                "test",
                "--title",
                "clean",
                "--description",
                "clean",
                "--locations",
                "noisy",
                "--locations-notes",
                "2/10 show +2 more",
                "--verdict",
                "acceptable",
                "--verdict-notes",
                "Edge case truncation",
            ],
        )
        assert result.exit_code == 0

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        fb = board.configs["gh-api"]["feedback"]
        assert fb["fields"]["locations"]["quality"] == "noisy"
        assert "2/10" in fb["fields"]["locations"]["notes"]
        assert fb["verdict_notes"] == "Edge case truncation"

    def test_poor_verdict_warns(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            ws,
            [
                "feedback",
                "gh-api",
                "test",
                "--title",
                "noisy",
                "--description",
                "clean",
                "--locations",
                "noisy",
                "--verdict",
                "poor",
                "--verdict-notes",
                "Titles noisy, locations incomplete",
            ],
        )
        assert result.exit_code == 0
        assert "force" in result.output.lower() or "poor" in result.output

    def test_verdict_required(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            ws, ["feedback", "gh-api", "test", "--title", "clean", "--description", "clean"]
        )
        # Missing --verdict should fail
        assert result.exit_code != 0

    def test_requires_explicit_for_populated_important(self, tmp_path, monkeypatch):
        """Important fields with coverage > 0 require explicit quality flags."""
        self._setup(tmp_path, monkeypatch)
        runner = CliRunner()
        # locations has 8/10 coverage — omitting --locations should fail
        result = runner.invoke(
            ws,
            [
                "feedback",
                "gh-api",
                "test",
                "--title",
                "clean",
                "--description",
                "clean",
                "--verdict",
                "good",
                "--verdict-notes",
                "Test missing locations flag",
            ],
        )
        assert result.exit_code != 0
        assert "--locations" in result.output


class TestQualityGates:
    """Test quality gate checks."""

    def test_all_gates_pass(self, tmp_path, monkeypatch):
        from src.workspace.commands.crawl import run_quality_gates

        monkeypatch.setattr("src.shared.constants.get_workspace_dir", lambda: tmp_path / ".ws")
        monkeypatch.setattr("src.workspace.state.get_workspace_dir", lambda: tmp_path / ".ws")

        ws_obj = Workspace(
            slug="test",
            name="Test",
            website="https://test.com",
            descriptions={
                "en": "A test company",
                "de": "Ein Testunternehmen",
                "fr": "Une entreprise test",
                "it": "Un'azienda test",
            },
        )
        # Create image artifacts
        art_dir = tmp_path / ".ws" / "test" / "artifacts" / "company"
        art_dir.mkdir(parents=True)
        (art_dir / "logo_original.svg").write_text("<svg></svg>")
        (art_dir / "icon_original.png").write_bytes(b"\x89PNG")

        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh-api"] = {
            "monitor_type": "greenhouse",
            "status": "tested",
            "run": {"jobs": 50},
            "feedback": {"verdict": "good"},
        }
        board.active_config = "gh-api"

        blockers, warnings = run_quality_gates(ws_obj, [board])
        assert blockers == []
        assert warnings == []

    def test_missing_image_artifacts_warns(self, tmp_path, monkeypatch):
        from src.workspace.commands.crawl import run_quality_gates

        monkeypatch.setattr("src.shared.constants.get_workspace_dir", lambda: tmp_path / ".ws")
        monkeypatch.setattr("src.workspace.state.get_workspace_dir", lambda: tmp_path / ".ws")

        ws_obj = Workspace(
            slug="test",
            name="Test",
            website="https://test.com",
        )
        # No image artifacts created
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh"] = {
            "status": "tested",
            "run": {"jobs": 10},
            "feedback": {"verdict": "good"},
        }
        board.active_config = "gh"

        _, warnings = run_quality_gates(ws_obj, [board])
        assert any("logo" in w.lower() for w in warnings)
        assert any("icon" in w.lower() for w in warnings)

    def test_no_boards(self):
        from src.workspace.commands.crawl import run_quality_gates

        ws_obj = Workspace(slug="test", name="Test", website="https://test.com")
        blockers, _ = run_quality_gates(ws_obj, [])
        assert any("No boards" in b for b in blockers)

    def test_missing_name(self):
        from src.workspace.commands.crawl import run_quality_gates

        ws_obj = Workspace(slug="test", website="https://test.com")
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh"] = {
            "status": "tested",
            "run": {"jobs": 10},
            "feedback": {"verdict": "good"},
        }
        board.active_config = "gh"

        blockers, _ = run_quality_gates(ws_obj, [board])
        assert any("name" in b.lower() for b in blockers)

    def test_no_feedback(self):
        from src.workspace.commands.crawl import run_quality_gates

        ws_obj = Workspace(slug="test", name="Test", website="https://test.com")
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh"] = {"status": "tested", "run": {"jobs": 10}}
        board.active_config = "gh"

        blockers, _ = run_quality_gates(ws_obj, [board])
        assert any("feedback" in b.lower() for b in blockers)

    def test_verified_empty_board_is_warning_not_blocker(self):
        from src.workspace.commands.crawl import run_quality_gates

        ws_obj = Workspace(slug="test", name="Test", website="https://test.com")
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["rss"] = {
            "status": "tested",
            "run": {"jobs": 0},
            "feedback": {
                "verdict": "acceptable",
                "verified_empty_board": True,
            },
        }
        board.active_config = "rss"

        blockers, warnings = run_quality_gates(ws_obj, [board])
        assert not any("0 jobs" in blocker for blocker in blockers)
        assert any("verified empty board" in warning for warning in warnings)

    def test_unusable_verdict(self):
        from src.workspace.commands.crawl import run_quality_gates

        ws_obj = Workspace(slug="test", name="Test", website="https://test.com")
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh"] = {
            "status": "tested",
            "run": {"jobs": 10},
            "feedback": {"verdict": "unusable"},
        }
        board.active_config = "gh"

        blockers, _ = run_quality_gates(ws_obj, [board])
        assert any("unusable" in b for b in blockers)

    def test_poor_verdict_blocks(self):
        from src.workspace.commands.crawl import run_quality_gates

        ws_obj = Workspace(slug="test", name="Test", website="https://test.com")
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh"] = {
            "status": "tested",
            "run": {"jobs": 10},
            "feedback": {"verdict": "poor"},
        }
        board.active_config = "gh"

        blockers, _ = run_quality_gates(ws_obj, [board])
        assert any("poor" in b for b in blockers)

    def test_missing_icons_warns(self, tmp_path, monkeypatch):
        from src.workspace.commands.crawl import run_quality_gates

        monkeypatch.setattr("src.shared.constants.get_workspace_dir", lambda: tmp_path / ".ws")
        monkeypatch.setattr("src.workspace.state.get_workspace_dir", lambda: tmp_path / ".ws")

        ws_obj = Workspace(slug="test", name="Test", website="https://test.com")
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh"] = {
            "status": "tested",
            "run": {"jobs": 10},
            "feedback": {"verdict": "good"},
        }
        board.active_config = "gh"

        _, warnings = run_quality_gates(ws_obj, [board])
        assert any("logo" in w.lower() for w in warnings)
        assert any("icon" in w.lower() for w in warnings)

    def test_zero_jobs_blocks(self):
        from src.workspace.commands.crawl import run_quality_gates

        ws_obj = Workspace(slug="test", name="Test", website="https://test.com")
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh"] = {
            "status": "tested",
            "run": {"jobs": 0},
            "feedback": {"verdict": "good"},
        }
        board.active_config = "gh"

        blockers, _ = run_quality_gates(ws_obj, [board])
        assert any("0 jobs" in b for b in blockers)

    def test_truncated_run_blocks(self):
        from src.workspace.commands.crawl import run_quality_gates

        ws_obj = Workspace(slug="test", name="Test", website="https://test.com")
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh"] = {
            "status": "tested",
            "run": {"jobs": 10, "truncated": True},
            "feedback": {"verdict": "good"},
        }
        board.active_config = "gh"

        blockers, _ = run_quality_gates(ws_obj, [board])
        assert any("truncated/incomplete" in blocker for blocker in blockers)

    def test_short_descriptions_warns(self, tmp_path, monkeypatch):
        from src.workspace.commands.crawl import run_quality_gates

        monkeypatch.setattr("src.shared.constants.get_workspace_dir", lambda: tmp_path / ".ws")
        monkeypatch.setattr("src.workspace.state.get_workspace_dir", lambda: tmp_path / ".ws")

        ws_obj = Workspace(
            slug="test",
            name="Test",
            website="https://test.com",
            logo_url="https://cdn.test.com/logo.png",
            icon_url="https://cdn.test.com/icon.png",
            descriptions={"en": "A test", "de": "Ein Test", "fr": "Un test", "it": "Un test"},
        )
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh"] = {
            "monitor_type": "greenhouse",
            "status": "tested",
            "run": {"jobs": 50},
            "scraper_run": {
                "description_samples": [
                    {"length": 50, "snippet": "Short desc"},
                    {"length": 30, "snippet": "Another short"},
                    {"length": 40, "snippet": "Also short"},
                ],
            },
            "feedback": {"verdict": "good"},
        }
        board.active_config = "gh"

        _, warnings = run_quality_gates(ws_obj, [board])
        assert any("under 200 chars" in w for w in warnings)

    def test_long_descriptions_no_warning(self, tmp_path, monkeypatch):
        from src.workspace.commands.crawl import run_quality_gates

        monkeypatch.setattr("src.shared.constants.get_workspace_dir", lambda: tmp_path / ".ws")
        monkeypatch.setattr("src.workspace.state.get_workspace_dir", lambda: tmp_path / ".ws")

        ws_obj = Workspace(
            slug="test",
            name="Test",
            website="https://test.com",
            logo_url="https://cdn.test.com/logo.png",
            icon_url="https://cdn.test.com/icon.png",
            descriptions={"en": "A test", "de": "Ein Test", "fr": "Un test", "it": "Un test"},
        )
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["gh"] = {
            "monitor_type": "greenhouse",
            "status": "tested",
            "run": {"jobs": 50},
            "scraper_run": {
                "description_samples": [
                    {"length": 500, "snippet": "A" * 200},
                    {"length": 800, "snippet": "B" * 200},
                    {"length": 350, "snippet": "C" * 200},
                ],
            },
            "feedback": {"verdict": "good"},
        }
        board.active_config = "gh"

        _, warnings = run_quality_gates(ws_obj, [board])
        assert not any("under 200 chars" in w for w in warnings)


# ── Phase 5: Cost Scoring ────────────────────────────────────────────


class TestCostScoring:
    """Test cost estimation functions."""

    def test_api_monitor_cost(self):
        from src.workspace.commands.crawl import _estimate_monitor_cost

        # API monitors have fixed ~1s cost
        cost = _estimate_monitor_cost("greenhouse", 200)
        assert cost == 1.0

    def test_sitemap_cost(self):
        from src.workspace.commands.crawl import _estimate_monitor_cost

        cost = _estimate_monitor_cost("sitemap", 200)
        assert cost == 1.5

    def test_api_sniffer_httpx_cost(self):
        from src.workspace.commands.crawl import _estimate_monitor_cost

        cost = _estimate_monitor_cost("api_sniffer", 200, {"items": 50, "browser": False})
        assert cost > 0
        # 200/50 = 4 pages, 0.3 * 4 = 1.2
        assert abs(cost - 1.2) < 0.01

    def test_api_sniffer_playwright_cost(self):
        from src.workspace.commands.crawl import _estimate_monitor_cost

        cost = _estimate_monitor_cost("api_sniffer", 200, {"items": 50, "browser": True})
        # 5.0 + 0.5 * 4 = 7.0
        assert abs(cost - 7.0) < 0.01

    def test_cycle_cost_rich_skips_scraper(self):
        from src.workspace.commands.crawl import _estimate_cycle_cost

        total = _estimate_cycle_cost(1.0, 200, rich=True)
        assert total == 1.0

    def test_cycle_cost_url_only_adds_scraper(self):
        from src.workspace.commands.crawl import _estimate_cycle_cost

        total = _estimate_cycle_cost(1.5, 200, rich=False)
        # Amortized scraper cost is tiny: 200/24000 * 0.3 ≈ 0.0025
        assert total > 1.5
        assert total < 1.51  # nearly negligible

    def test_initial_load_url_only(self):
        from src.workspace.commands.crawl import _estimate_initial_load

        # 200 jobs * 0.3s/job = 60s
        assert _estimate_initial_load(200) == 60.0
        assert _estimate_initial_load(200, scraper_per_job=0.5) == 100.0

    def test_initial_load_zero_for_rich(self):
        """Rich monitors have zero initial load (no scraper needed)."""
        from src.workspace.commands.crawl import _estimate_initial_load

        # Function returns raw n*cost; rich check happens in the caller.
        # This tests the formula correctness.
        assert _estimate_initial_load(0) == 0.0

    def test_select_monitor_records_cost(self, tmp_path, monkeypatch):
        """ws select monitor should record cost breakdown in config."""
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(ws, ["select", "monitor", "test", "greenhouse"])
        assert result.exit_code == 0

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        cfg = board.configs[board.active_config]
        assert "cost" in cfg
        assert "monitor_per_cycle" in cfg["cost"]
        assert "initial_load" in cfg["cost"]
        # Greenhouse is rich — initial load should be 0
        assert cfg["cost"]["initial_load"] == 0.0

    def test_select_monitor_url_only_has_initial_load(self, tmp_path, monkeypatch):
        """URL-only monitors should have non-zero initial load estimate."""
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        runner = CliRunner()
        result = runner.invoke(ws, ["select", "monitor", "test", "sitemap"])
        assert result.exit_code == 0

        from src.workspace.state import load_board

        board = load_board("test", "careers")
        cfg = board.configs[board.active_config]
        assert cfg["cost"]["initial_load"] > 0

    def test_select_rich_nextdata_accepts_page_metadata_pagination(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        config = {
            "source": "rsc",
            "path": "jobsData.data",
            "url_template": "{jobAdUrl}",
            "fields": {"title": "title", "locations": "cityName"},
            "pagination": {
                "path": "jobsData.meta",
                "page_count": "totalPages",
                "page_param": "page",
            },
        }
        runner = CliRunner()
        result = runner.invoke(
            ws,
            ["select", "monitor", "test", "nextdata", "--config", json.dumps(config)],
        )

        assert result.exit_code == 0, result.output
        board = load_board("test", "careers")
        cfg = board.configs[board.active_config]
        assert cfg["monitor_config"]["pagination"] == config["pagination"]
        assert cfg["cost"]["initial_load"] == 0.0

    def test_select_dom_accepts_path_pagination_url_template(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        save_board("test", board)
        ws_obj = load_workspace("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        config = {
            "url_filter": "/job/",
            "pagination": {
                "url_template": "https://test.com/jobs/{page}",
                "max_pages": 100,
            },
        }
        runner = CliRunner()
        result = runner.invoke(
            ws,
            ["select", "monitor", "test", "dom", "--config", json.dumps(config)],
        )

        assert result.exit_code == 0, result.output
        board = load_board("test", "careers")
        cfg = board.configs[board.active_config]
        assert cfg["monitor_config"]["pagination"] == config["pagination"]


# ── Phase 6: Submit robustness ──────────────────────────────────────────


def _setup_submittable_workspace(tmp_path, monkeypatch):
    """Create a workspace ready for submit (all quality gates pass)."""
    _patch_all(monkeypatch, tmp_path)
    _setup_csvs(tmp_path, companies="test,,,, \n")

    ws_obj = Workspace(
        slug="test",
        name="Test Corp",
        website="https://test.com",
        issue=1,
        pr=10,
        branch="add-company/test",
        pr_provenance=_test_pr_provenance(10),
        descriptions={
            "en": "A test company",
            "de": "Ein Testunternehmen",
            "fr": "Une entreprise test",
            "it": "Un'azienda test",
        },
    )
    save_workspace(ws_obj)
    set_active_slug("test")

    board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
    board.configs["greenhouse"] = {
        "monitor_type": "greenhouse",
        "monitor_config": {},
        "status": "tested",
        "run": {"jobs": 50},
        "feedback": {"verdict": "good", "fields": {"title": "clean", "description": "clean"}},
        "cost": {"monitor_per_cycle": 1.0, "initial_load": 0.0},
    }
    board.active_config = "greenhouse"
    board.monitor_run = {"jobs": 50, "time": 0.9, "has_rich_data": True, "sample_urls": []}
    save_board("test", board)

    monkeypatch.setattr(
        "src.workspace.git.get_pr_details_strict",
        lambda number: _test_pr_details(number),
    )
    monkeypatch.setattr("src.workspace.git.get_main_branch", lambda: "main")
    monkeypatch.setattr("src.workspace.git.get_authenticated_login_strict", lambda: "resolver")
    monkeypatch.setattr("src.workspace.git.remote_branch_oid_strict", lambda _branch: TEST_HEAD_OID)
    monkeypatch.setattr(
        "src.workspace.commands.lifecycle._authenticate_workspace_worktree",
        lambda _workspace: None,
    )
    monkeypatch.setattr(
        "src.workspace.worktree_auth.pivot_to_authenticated_worktree",
        lambda _workspace: tmp_path / "worktrees" / "test",
    )

    return ws_obj, board


class TestSubmitWorktreeAuthentication:
    @pytest.mark.parametrize("clear_path", [True, False])
    def test_submit_missing_persisted_identity_fails_before_unrelated_mutation(
        self, tmp_path, monkeypatch, clear_path
    ):
        _patch_all(monkeypatch, tmp_path)
        unrelated = tmp_path / "unrelated-checkout"
        unrelated.mkdir()
        marker = unrelated / "owned.txt"
        marker.write_text("untouched\n")
        canonical = tmp_path / "worktrees" / "acme"
        ws_obj = Workspace(
            slug="acme",
            issue=42,
            pr=7,
            branch="add-company/acme",
            pr_provenance=_test_pr_provenance(7, slug="acme", issue=42),
            worktree="" if clear_path else str(canonical),
            worktree_identity={},
        )
        save_workspace(ws_obj)

        with (
            patch("src.workspace.commands.lifecycle.is_local_mode", return_value=False),
            patch("src.workspace.git.worktrees_dir", return_value=tmp_path / "worktrees"),
            patch("src.shared.constants.get_repo_root", return_value=unrelated),
            patch("src.shared.constants.set_repo_root") as pivot,
            patch("src.workspace.git.commit") as commit,
            patch("src.workspace.git.push_branch_at_expected_oid") as push,
        ):
            result = CliRunner().invoke(ws, ["submit", "acme"])

        assert result.exit_code != 0
        assert "authenticated worktree" in str(result.exception)
        assert marker.read_text() == "untouched\n"
        pivot.assert_not_called()
        commit.assert_not_called()
        push.assert_not_called()

    def test_ready_missing_identity_cannot_push_or_mark_pr_ready(self, tmp_path, monkeypatch):
        from src.workspace.commands.task import _finalize_workflow
        from src.workspace.workflow import WorkflowState, _save_wf_to_disk

        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(
            slug="acme",
            issue=42,
            pr=7,
            branch="add-company/acme",
            pr_provenance=_test_pr_provenance(7, slug="acme", issue=42),
            worktree="",
            worktree_identity={},
        )
        ws_obj.submit_state = {"pushed": True}
        save_workspace(ws_obj)
        _save_wf_to_disk("acme", WorkflowState(current_step="reflect"))

        with (
            patch("src.workspace.commands.lifecycle.is_local_mode", return_value=False),
            patch("src.workspace.git.worktrees_dir", return_value=tmp_path / "worktrees"),
            patch("src.workspace.git.push_branch_at_expected_oid") as push,
            patch("src.workspace.git.mark_pr_ready") as ready,
            pytest.raises(WorkspaceError, match="authenticated worktree path"),
        ):
            _finalize_workflow("acme")
        push.assert_not_called()
        ready.assert_not_called()

    def test_terminal_rejects_prejournal_worktree_replacement_without_mutation(
        self, tmp_path, monkeypatch
    ):
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        canonical = tmp_path / "worktrees" / "acme"
        provenance = _test_pr_provenance(7, slug="acme", issue=42)
        ws_obj = Workspace(
            slug="acme",
            issue=42,
            pr=7,
            branch="add-company/acme",
            pr_provenance=provenance,
            worktree=str(canonical),
            worktree_identity={
                "version": 1,
                "path": str(canonical),
                "slug": "acme",
                "branch": "add-company/acme",
                "head": TEST_HEAD_OID,
                "dev": 1,
                "ino": 2,
                "issue": 42,
                "pr": 7,
                "pr_provenance": copy.deepcopy(provenance),
            },
        )
        save_workspace(ws_obj)

        with (
            patch("src.workspace.git.worktrees_dir", return_value=tmp_path / "worktrees"),
            patch(
                "src.workspace.git.authenticate_managed_worktree",
                side_effect=WorkspaceError("replacement filesystem entry"),
            ),
            patch("src.workspace.git.verify_recorded_pr") as verify_pr,
            patch("src.workspace.git.delete_remote_branch_at_expected_oid") as delete_remote,
            patch("src.workspace.git.remove_authenticated_worktree") as remove_worktree,
            pytest.raises(WorkspaceError, match="replacement filesystem entry"),
        ):
            lifecycle._run_terminal_cleanup(ws_obj, local=False)

        verify_pr.assert_not_called()
        delete_remote.assert_not_called()
        remove_worktree.assert_not_called()
        assert not lifecycle._lexists(lifecycle._terminal_pending_path("acme"))
        assert workspace_exists("acme")

    def test_mutable_noncanonical_worktree_path_is_rejected_before_pivot(
        self, tmp_path, monkeypatch
    ):
        from src.workspace.commands.lifecycle import _authenticate_workspace_worktree

        _patch_all(monkeypatch, tmp_path)
        canonical = tmp_path / "worktrees" / "acme"
        attacker = tmp_path / "attacker-checkout"
        attacker.mkdir()
        identity = {
            "version": 1,
            "path": str(canonical),
            "slug": "acme",
            "branch": "add-company/acme",
            "head": TEST_HEAD_OID,
            "dev": 1,
            "ino": 2,
            "issue": 42,
            "pr": 7,
            "pr_provenance": _test_pr_provenance(7, slug="acme", issue=42),
        }
        ws_obj = Workspace(
            slug="acme",
            issue=42,
            pr=7,
            branch="add-company/acme",
            pr_provenance=copy.deepcopy(identity["pr_provenance"]),
            worktree=str(attacker),
            worktree_identity=identity,
        )

        with (
            patch("src.workspace.git.worktrees_dir", return_value=tmp_path / "worktrees"),
            patch("src.workspace.git.authenticate_managed_worktree") as authenticate,
            pytest.raises(WorkspaceError, match="non-canonical"),
        ):
            _authenticate_workspace_worktree(ws_obj)
        authenticate.assert_not_called()

    def test_replaced_canonical_checkout_is_rejected_before_mutation(self, tmp_path, monkeypatch):
        from src.workspace.commands.lifecycle import _authenticate_workspace_worktree

        _patch_all(monkeypatch, tmp_path)
        canonical = tmp_path / "worktrees" / "acme"
        provenance = _test_pr_provenance(7, slug="acme", issue=42)
        ws_obj = Workspace(
            slug="acme",
            issue=42,
            pr=7,
            branch="add-company/acme",
            pr_provenance=provenance,
            worktree=str(canonical),
            worktree_identity={
                "version": 1,
                "path": str(canonical),
                "slug": "acme",
                "branch": "add-company/acme",
                "head": TEST_HEAD_OID,
                "dev": 1,
                "ino": 2,
                "issue": 42,
                "pr": 7,
                "pr_provenance": copy.deepcopy(provenance),
            },
        )

        with (
            patch("src.workspace.git.worktrees_dir", return_value=tmp_path / "worktrees"),
            patch(
                "src.workspace.git.authenticate_managed_worktree",
                side_effect=WorkspaceError("replacement filesystem entry"),
            ),
            pytest.raises(WorkspaceError, match="replacement filesystem entry"),
        ):
            _authenticate_workspace_worktree(ws_obj)


class TestSubmitStepRegistry:
    """Test the submit step registry and checkpoint logic."""

    def test_submit_step_list_exists(self):
        from src.workspace.commands.lifecycle import SUBMIT_STEPS

        assert len(SUBMIT_STEPS) == 8
        keys = [k for k, _, _ in SUBMIT_STEPS]
        assert "csv_written" in keys
        assert "pushed" in keys
        assert "pr_ready" not in keys
        assert "issue_completed" in keys

    def test_critical_steps_are_first(self):
        from src.workspace.commands.lifecycle import SUBMIT_STEPS

        critical_idx = [i for i, (_, _, c) in enumerate(SUBMIT_STEPS) if c]
        non_critical_idx = [i for i, (_, _, c) in enumerate(SUBMIT_STEPS) if not c]
        assert max(critical_idx) < min(non_critical_idx)

    def test_csv_fallback_writes_auto_scraper_config(self, tmp_path, monkeypatch):
        """Submit fallback writes the full partial-rich auto configuration."""
        ws_obj, board = _setup_submittable_workspace(tmp_path, monkeypatch)
        board.configs["paylocity"] = {
            "monitor_type": "paylocity",
            "monitor_config": {},
            # Reproduce workspace state written before auto scraper configs
            # were persisted: type present, required config missing.
            "scraper_type": "paylocity",
            "status": "tested",
            "run": {"jobs": 3},
            "feedback": {"verdict": "good"},
        }
        board.active_config = "paylocity"
        save_board("test", board)

        from src.shared.csv_io import read_csv
        from src.workspace.commands.lifecycle import _execute_submit_step

        _execute_submit_step("csv_written", ws_obj, [board], None)

        _, rows = read_csv(tmp_path / "boards.csv")
        row = next(row for row in rows if row["board_slug"] == "test-careers")
        assert row["scraper_type"] == "paylocity"
        assert json.loads(row["scraper_config"]) == {
            "enrich": ["description", "employment_type", "job_location_type"]
        }

    def test_csv_fallback_repairs_v1_null_scraper_placeholders(self, tmp_path, monkeypatch):
        ws_obj, _ = _setup_submittable_workspace(tmp_path, monkeypatch)
        board = Board.from_dict(
            {
                "alias": "careers",
                "slug": "test-careers",
                "url": "https://test.com/jobs",
                "monitor": {"type": "paylocity", "config": {}},
                "scraper": {},
            }
        )

        from src.shared.csv_io import read_csv
        from src.workspace.commands.lifecycle import _execute_submit_step

        _execute_submit_step("csv_written", ws_obj, [board], None)

        _, rows = read_csv(tmp_path / "boards.csv")
        row = next(row for row in rows if row["board_slug"] == "test-careers")
        assert row["scraper_type"] == "paylocity"
        assert json.loads(row["scraper_config"]) == {
            "enrich": ["description", "employment_type", "job_location_type"]
        }

    def test_csv_fallback_preserves_explicit_scraper_config(self, tmp_path, monkeypatch):
        ws_obj, board = _setup_submittable_workspace(tmp_path, monkeypatch)
        explicit = {"enrich": ["description"], "proxy": True}
        board.configs["paylocity"] = {
            "monitor_type": "paylocity",
            "monitor_config": {},
            "scraper_type": "paylocity",
            "scraper_config": explicit,
            "status": "tested",
            "run": {"jobs": 3},
            "feedback": {"verdict": "good"},
        }
        board.active_config = "paylocity"

        from src.shared.csv_io import read_csv
        from src.workspace.commands.lifecycle import _execute_submit_step

        _execute_submit_step("csv_written", ws_obj, [board], None)

        _, rows = read_csv(tmp_path / "boards.csv")
        row = next(row for row in rows if row["board_slug"] == "test-careers")
        assert json.loads(row["scraper_config"]) == explicit

    def test_csv_fallback_preserves_explicit_empty_scraper_config(self, tmp_path, monkeypatch):
        ws_obj, board = _setup_submittable_workspace(tmp_path, monkeypatch)
        from src.csvtool import board_add

        board_add(
            "test",
            board_slug="test-careers",
            board_url="https://test.com/jobs",
            monitor_type="paylocity",
            scraper_type="paylocity",
            scraper_config=json.dumps({"enrich": ["description"]}),
        )
        board.configs["paylocity"] = {
            "monitor_type": "paylocity",
            "monitor_config": {},
            "scraper_type": "paylocity",
            "scraper_config": {},
            "status": "tested",
            "run": {"jobs": 3},
            "feedback": {"verdict": "good"},
        }
        board.active_config = "paylocity"

        from src.shared.csv_io import read_csv
        from src.workspace.commands.lifecycle import _execute_submit_step

        _execute_submit_step("csv_written", ws_obj, [board], None)

        _, rows = read_csv(tmp_path / "boards.csv")
        row = next(row for row in rows if row["board_slug"] == "test-careers")
        assert row["scraper_type"] == "paylocity"
        assert row["scraper_config"] == ""

    def test_csv_write_restores_canonical_order(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(
            tmp_path,
            companies=(
                "zeta,Zeta,https://zeta.example,,,\n"
                "alpha,Alpha,https://alpha.example,,,\n"
                "test,Old Test,https://old.example,,,\n"
            ),
            boards=(
                "zeta,zeta-jobs,https://zeta.example/jobs,sitemap,,json-ld,\n"
                "alpha,alpha-jobs,https://alpha.example/jobs,sitemap,,json-ld,\n"
            ),
        )
        ws_obj = Workspace(slug="test", name="Test", website="https://test.example")
        board = Board(alias="careers", slug="test-careers", url="https://test.example/jobs")
        board.configs["sitemap"] = {
            "monitor_type": "sitemap",
            "scraper_type": "json-ld",
            "status": "tested",
            "run": {"jobs": 2},
        }
        board.active_config = "sitemap"

        from src.workspace.commands.lifecycle import _execute_submit_step

        _execute_submit_step("csv_written", ws_obj, [board], None)

        company_lines = (tmp_path / "companies.csv").read_text().splitlines()
        board_lines = (tmp_path / "boards.csv").read_text().splitlines()
        assert [line.split(",", 1)[0] for line in company_lines[1:4]] == [
            "alpha",
            "test",
            "zeta",
        ]
        assert [line.split(",", 1)[0] for line in board_lines[1:4]] == [
            "alpha",
            "test",
            "zeta",
        ]

    def test_csv_write_updates_resumed_board_by_slug_when_url_changes(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(
            tmp_path,
            companies="test,Old Test,https://old.example,,,\n",
            boards="test,test-careers,https://old.example/jobs,greenhouse,,json-ld,\n",
        )
        ws_obj = Workspace(slug="test", name="Test", website="https://test.example")
        board = Board(
            alias="careers",
            slug="test-careers",
            url="https://test.example/jobs",
        )
        board.configs["api"] = {
            "monitor_type": "api_sniffer",
            "monitor_config": {"api_url": "https://test.example/api/jobs"},
            "scraper_type": "skip",
            "status": "tested",
            "run": {"jobs": 2},
        }
        board.active_config = "api"

        from src.shared.csv_io import read_csv
        from src.workspace.commands.lifecycle import _execute_submit_step

        _execute_submit_step("csv_written", ws_obj, [board], None)

        _, rows = read_csv(tmp_path / "boards.csv")
        matching = [row for row in rows if row["board_slug"] == "test-careers"]
        assert len(matching) == 1
        assert matching[0]["board_url"] == "https://test.example/jobs"
        assert matching[0]["monitor_type"] == "api_sniffer"


class TestSubmitIdempotency:
    """Submit skips already-completed steps on rerun."""

    def test_skips_completed_steps(self, tmp_path, monkeypatch):
        ws_obj, board = _setup_submittable_workspace(tmp_path, monkeypatch)

        # Mark some steps as done
        ws_obj.submit_state = {
            "_active_configs": {
                "careers": {
                    "active": "greenhouse",
                    "url": "https://test.com/jobs",
                    "monitor_type": "greenhouse",
                    "monitor_config": {},
                    "scraper_type": None,
                    "scraper_config": {},
                }
            },
            "csv_written": True,
            "validated": True,
        }
        save_workspace(ws_obj)

        with ExitStack() as stack:
            stack.enter_context(patch("src.workspace.git._run"))
            runner = CliRunner()
            result = runner.invoke(ws, ["submit", "test"])

        assert "Write company/board CSVs (done)" in result.output
        assert "Validate CSVs (done)" in result.output

    def test_two_submit_commands_for_same_slug_are_serialized(self, tmp_path, monkeypatch):
        ws_obj, _ = _setup_submittable_workspace(tmp_path, monkeypatch)
        ws_obj.submit_state = {
            key: True
            for key in (
                "csv_written",
                "validated",
                "committed",
                "pushed",
                "pr_body_updated",
                "stats_posted",
                "transcript_posted",
                "issue_completed",
            )
        }
        save_workspace(ws_obj)
        first_entered = Event()
        release_first = Event()
        second_entered = Event()
        calls = 0

        def gates(*_args):
            nonlocal calls
            calls += 1
            if calls == 1:
                first_entered.set()
                assert release_first.wait(timeout=2)
            else:
                second_entered.set()
            return [], []

        from src.workspace.commands.lifecycle import submit

        assert submit.callback is not None
        with (
            patch("src.workspace.commands.crawl.run_quality_gates", side_effect=gates),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(submit.callback, slug="test", summary=None, force=False)
            assert first_entered.wait(timeout=2)
            second = pool.submit(submit.callback, slug="test", summary=None, force=False)
            assert not second_entered.wait(timeout=0.1)
            release_first.set()
            first.result(timeout=2)
            second.result(timeout=2)

        assert second_entered.is_set()

    def test_stale_submit_restarts(self, tmp_path, monkeypatch):
        ws_obj, board = _setup_submittable_workspace(tmp_path, monkeypatch)

        # Previous submit had a different active config
        ws_obj.submit_state = {
            "_active_configs": {"careers": "sitemap"},
            "csv_written": True,
            "validated": True,
        }
        save_workspace(ws_obj)

        with ExitStack() as stack:
            stack.enter_context(patch("src.workspace.git._run"))
            runner = CliRunner()
            result = runner.invoke(ws, ["submit", "test"])

        # Should detect stale config and restart
        assert "config changed" in result.output

    def test_stale_submit_rewrites_changed_scraper_config(self, tmp_path, monkeypatch):
        ws_obj, board = _setup_submittable_workspace(tmp_path, monkeypatch)
        from src.csvtool import board_add

        stale_config = {"enrich": ["description", "valid_through"]}
        current_config = {"enrich": ["description"]}
        board.configs["greenhouse"]["scraper_type"] = "api_sniffer"
        board.configs["greenhouse"]["scraper_config"] = current_config
        save_board("test", board)

        board_add(
            "test",
            board_slug="test-careers",
            board_url="https://test.com/jobs",
            monitor_type="greenhouse",
            scraper_type="api_sniffer",
            scraper_config=json.dumps(stale_config),
        )
        ws_obj.submit_state = {
            "_active_configs": {
                "careers": {
                    "active": "greenhouse",
                    "url": "https://test.com/jobs",
                    "monitor_type": "greenhouse",
                    "monitor_config": {},
                    "scraper_type": "api_sniffer",
                    "scraper_config": stale_config,
                }
            },
            "csv_written": True,
        }
        save_workspace(ws_obj)

        with ExitStack() as stack:
            stack.enter_context(patch("src.workspace.git._run"))
            runner = CliRunner()
            result = runner.invoke(ws, ["submit", "test"])

        assert "config changed" in result.output
        from src.shared.csv_io import read_csv

        _, rows = read_csv(tmp_path / "boards.csv")
        row = next(row for row in rows if row["board_slug"] == "test-careers")
        assert json.loads(row["scraper_config"]) == current_config

    def test_unpublished_workspace_creates_one_pr_after_push(self, tmp_path, monkeypatch):
        ws_obj, _ = _setup_submittable_workspace(tmp_path, monkeypatch)
        ws_obj.pr = None
        save_workspace(ws_obj)

        with ExitStack() as stack:
            stack.enter_context(
                patch("src.workspace.git.has_uncommitted_changes", return_value=False)
            )
            push = stack.enter_context(patch("src.workspace.git.push_branch_at_expected_oid"))
            remote_values = iter([None])
            stack.enter_context(
                patch(
                    "src.workspace.git.remote_branch_oid_strict",
                    side_effect=lambda _branch: next(remote_values, TEST_HEAD_OID),
                )
            )
            stack.enter_context(
                patch("src.workspace.git.find_open_pr_for_branch", return_value=None)
            )
            stack.enter_context(patch("src.workspace.git.check_existing_prs", return_value=[]))
            create_pr = stack.enter_context(
                patch("src.workspace.git.create_draft_pr", return_value=99)
            )
            stack.enter_context(patch("src.workspace.git._run"))

            runner = CliRunner()
            first = runner.invoke(ws, ["submit", "test"])
            second = runner.invoke(ws, ["submit", "test"])

        assert first.exit_code == 0, first.output
        assert second.exit_code == 0, second.output
        push.assert_called_once_with("add-company/test", TEST_HEAD_OID, None)
        create_pr.assert_called_once()
        assert load_workspace("test").pr == 99

    def test_fresh_pr_issue_link_lag_retries_without_corrupting_ownership(
        self, tmp_path, monkeypatch
    ):
        ws_obj, _ = _setup_submittable_workspace(tmp_path, monkeypatch)
        ws_obj.pr = None
        ws_obj.pr_provenance = {}
        ws_obj.worktree = str(tmp_path / "worktrees" / "test")
        ws_obj.worktree_identity = {
            "version": 1,
            "path": ws_obj.worktree,
            "slug": "test",
            "branch": ws_obj.branch,
            "head": TEST_HEAD_OID,
            "dev": 1,
            "ino": 2,
            "issue": ws_obj.issue,
            "pr": None,
            "pr_provenance": {},
        }
        save_workspace(ws_obj)

        pending = _test_pr_details(99)
        pending["closingIssuesReferences"] = []
        linked = _test_pr_details(99)
        detail_calls = 0

        def pr_details(_number):
            nonlocal detail_calls
            detail_calls += 1
            return pending if detail_calls <= 2 else linked

        with ExitStack() as stack:
            stack.enter_context(
                patch("src.workspace.git.has_uncommitted_changes", return_value=False)
            )
            stack.enter_context(patch("src.workspace.git.push_branch_at_expected_oid"))
            remote_values = iter([None, TEST_HEAD_OID])
            stack.enter_context(
                patch(
                    "src.workspace.git.remote_branch_oid_strict",
                    side_effect=lambda _branch: next(remote_values, TEST_HEAD_OID),
                )
            )
            stack.enter_context(
                patch("src.workspace.git.find_open_pr_for_branch", return_value=None)
            )
            stack.enter_context(patch("src.workspace.git.check_existing_prs", return_value=[]))
            stack.enter_context(patch("src.workspace.git.create_draft_pr", return_value=99))
            stack.enter_context(patch("src.workspace.git._run"))
            stack.enter_context(
                patch(
                    "src.workspace.git.get_pr_details_strict",
                    side_effect=pr_details,
                )
            )
            sleep = stack.enter_context(patch("src.workspace.commands.lifecycle.time.sleep"))

            result = CliRunner().invoke(ws, ["submit", "test"])

        assert result.exit_code == 0, result.output
        saved = load_workspace("test")
        assert saved.pr == 99
        assert saved.pr_provenance == _test_pr_provenance(99)
        assert saved.worktree_identity["pr"] == 99
        assert saved.worktree_identity["pr_provenance"] == saved.pr_provenance
        assert sleep.call_count == 2

    def test_failed_pr_attachment_restores_authentic_workspace_state(self, tmp_path, monkeypatch):
        from src.workspace.commands.lifecycle import _attach_workspace_pr

        ws_obj = Workspace(
            slug="test",
            issue=1,
            branch="add-company/test",
            worktree=str(tmp_path / "worktrees" / "test"),
            worktree_identity={"pr": None, "pr_provenance": {}},
        )
        original_identity = copy.deepcopy(ws_obj.worktree_identity)
        pending = _test_pr_details(99)
        pending["closingIssuesReferences"] = []

        with (
            patch("src.workspace.git.get_pr_details_strict", return_value=pending),
            patch("src.workspace.git.get_authenticated_login_strict", return_value="resolver"),
            patch("src.workspace.git.get_main_branch", return_value="main"),
            patch("src.workspace.commands.lifecycle.time.sleep"),
            pytest.raises(WorkspaceError, match="sole resolver"),
        ):
            _attach_workspace_pr(
                ws_obj,
                99,
                require_current_actor=True,
                expected_head_oid=TEST_HEAD_OID,
                retry_issue_link=True,
            )

        assert ws_obj.pr is None
        assert ws_obj.pr_provenance == {}
        assert ws_obj.worktree_identity == original_identity

    def test_interrupted_submit_recovers_pr_by_branch(self, tmp_path, monkeypatch):
        ws_obj, _ = _setup_submittable_workspace(tmp_path, monkeypatch)
        ws_obj.pr = None
        save_workspace(ws_obj)

        with ExitStack() as stack:
            stack.enter_context(
                patch("src.workspace.git.has_uncommitted_changes", return_value=False)
            )
            stack.enter_context(patch("src.workspace.git.push_branch_at_expected_oid"))
            remote_values = iter([None])
            stack.enter_context(
                patch(
                    "src.workspace.git.remote_branch_oid_strict",
                    side_effect=lambda _branch: next(remote_values, TEST_HEAD_OID),
                )
            )
            stack.enter_context(patch("src.workspace.git.find_open_pr_for_branch", return_value=99))
            issue_prs = stack.enter_context(patch("src.workspace.git.check_existing_prs"))
            create_pr = stack.enter_context(patch("src.workspace.git.create_draft_pr"))
            stack.enter_context(patch("src.workspace.git._run"))

            runner = CliRunner()
            result = runner.invoke(ws, ["submit", "test"])

        assert result.exit_code == 0, result.output
        assert "Recovered existing draft PR #99" in result.output
        issue_prs.assert_not_called()
        create_pr.assert_not_called()
        assert load_workspace("test").pr == 99

    def test_submit_refuses_concurrent_pr_on_another_branch(self, tmp_path, monkeypatch):
        ws_obj, _ = _setup_submittable_workspace(tmp_path, monkeypatch)
        ws_obj.pr = None
        save_workspace(ws_obj)

        with ExitStack() as stack:
            stack.enter_context(
                patch("src.workspace.git.has_uncommitted_changes", return_value=False)
            )
            stack.enter_context(patch("src.workspace.git.push_branch_at_expected_oid"))
            stack.enter_context(
                patch("src.workspace.git.remote_branch_oid_strict", side_effect=[None])
            )
            stack.enter_context(
                patch("src.workspace.git.find_open_pr_for_branch", return_value=None)
            )
            stack.enter_context(
                patch(
                    "src.workspace.git.check_existing_prs",
                    return_value=[{"number": 88}],
                )
            )
            stack.enter_context(
                patch("src.workspace.git.get_pr_branch", return_value="add-company/other")
            )
            create_pr = stack.enter_context(patch("src.workspace.git.create_draft_pr"))
            stack.enter_context(patch("src.workspace.git._run"))

            runner = CliRunner()
            result = runner.invoke(ws, ["submit", "test"])

        assert result.exit_code != 0
        assert "refusing to create a duplicate" in result.output
        create_pr.assert_not_called()


class TestSubmitForce:
    """Test --force flag with poor quality."""

    def test_poor_verdict_blocks_without_force(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "src.workspace.commands.lifecycle._authenticate_workspace_worktree",
            lambda _workspace: None,
        )
        monkeypatch.setattr(
            "src.workspace.worktree_auth.pivot_to_authenticated_worktree",
            lambda _workspace: tmp_path / "worktrees" / "test",
        )
        _setup_csvs(tmp_path, companies="test,,,,\n")

        ws_obj = Workspace(slug="test", name="Test", website="https://test.com", issue=1, pr=10)
        save_workspace(ws_obj)
        set_active_slug("test")

        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 50},
            "feedback": {"verdict": "poor", "fields": {"title": "clean", "description": "noisy"}},
        }
        board.active_config = "greenhouse"
        board.monitor_run = {"jobs": 50, "time": 0.9}
        save_board("test", board)

        runner = CliRunner()
        result = runner.invoke(ws, ["submit", "test"])
        assert result.exit_code != 0
        assert "Quality gates failed" in result.output

    def test_poor_verdict_passes_with_force(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "src.workspace.commands.lifecycle._authenticate_workspace_worktree",
            lambda _workspace: None,
        )
        monkeypatch.setattr(
            "src.workspace.worktree_auth.pivot_to_authenticated_worktree",
            lambda _workspace: tmp_path / "worktrees" / "test",
        )
        _setup_csvs(tmp_path, companies="test,,,,\n")

        ws_obj = Workspace(
            slug="test",
            name="Test",
            website="https://test.com",
            issue=1,
            pr=10,
            branch="add-company/test",
            pr_provenance=_test_pr_provenance(10),
            descriptions={
                "en": "A test company",
                "de": "Ein Testunternehmen",
                "fr": "Une entreprise test",
                "it": "Un'azienda test",
            },
        )
        save_workspace(ws_obj)
        set_active_slug("test")

        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 50},
            "feedback": {"verdict": "poor", "fields": {"title": "clean", "description": "noisy"}},
        }
        board.active_config = "greenhouse"
        board.monitor_run = {"jobs": 50, "time": 0.9}
        save_board("test", board)
        monkeypatch.setattr(
            "src.workspace.git.get_pr_details_strict", lambda number: _test_pr_details(number)
        )
        monkeypatch.setattr("src.workspace.git.get_main_branch", lambda: "main")
        monkeypatch.setattr(
            "src.workspace.git.remote_branch_oid_strict", lambda _branch: TEST_HEAD_OID
        )

        with ExitStack() as stack:
            stack.enter_context(patch("src.workspace.git._run"))
            runner = CliRunner()
            result = runner.invoke(ws, ["submit", "test", "--force"])

        assert result.exit_code == 0
        assert "forced" in result.output


class TestSubmitLastError:
    """Submit stores last_error on critical failure."""

    def test_stores_last_error_on_critical_failure(self, tmp_path, monkeypatch):
        _setup_submittable_workspace(tmp_path, monkeypatch)

        from src.workspace.errors import GitCommandError

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.workspace.git.has_uncommitted_changes",
                    return_value=True,
                )
            )
            stack.enter_context(patch("src.workspace.git.add_files"))
            stack.enter_context(
                patch(
                    "src.workspace.git.commit",
                    side_effect=GitCommandError(
                        cmd=["git", "commit"], returncode=1, stderr="nothing to commit"
                    ),
                )
            )

            runner = CliRunner()
            result = runner.invoke(ws, ["submit", "test"])

        assert result.exit_code != 0

        ws_reloaded = load_workspace("test")
        assert ws_reloaded.last_error
        assert ws_reloaded.last_error["step"] == "committed"
        assert "nothing to commit" in ws_reloaded.last_error["error"]

    def test_clears_last_error_on_success(self, tmp_path, monkeypatch):
        ws_obj, board = _setup_submittable_workspace(tmp_path, monkeypatch)
        ws_obj.last_error = {"command": "submit", "step": "pushed", "error": "timeout"}
        save_workspace(ws_obj)

        with ExitStack() as stack:
            stack.enter_context(patch("src.workspace.git._run"))
            runner = CliRunner()
            result = runner.invoke(ws, ["submit", "test"])

        assert result.exit_code == 0
        ws_reloaded = load_workspace("test")
        assert not ws_reloaded.last_error

    def test_submit_stages_kb_directory(self, tmp_path, monkeypatch):
        _setup_submittable_workspace(tmp_path, monkeypatch)

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.workspace.git.has_uncommitted_changes",
                    return_value=True,
                )
            )
            add_files = stack.enter_context(patch("src.workspace.git.add_files"))
            stack.enter_context(patch("src.workspace.git._run"))
            runner = CliRunner()
            result = runner.invoke(ws, ["submit", "test"])

        assert result.exit_code == 0
        staged_paths = add_files.call_args[0][0]
        assert "apps/crawler/src/workspace/kb/" in staged_paths


class TestBuildPrBody:
    """Test PR body generation."""

    def test_inventory_source_marker_is_preserved_for_queue_reconciliation(self):
        from src.ats_inventory.candidates import parse_candidate_markers
        from src.workspace.ats_seed import apply_inventory_seed, parse_inventory_seed
        from src.workspace.commands.lifecycle import _build_pr_body

        workspace = Workspace(slug="acme", name="Acme", issue=1)
        seed = parse_inventory_seed(_inventory_issue_body())
        assert seed is not None
        board = apply_inventory_seed(workspace, seed)
        workspace.ats_inventory["status"] = "verified"

        body = _build_pr_body(workspace, [board])

        assert parse_candidate_markers(body) == ((seed.source_key, seed.board_sha256),)
        assert f"Source key: `{seed.source_key}`" in body
        assert "Seed verification: `verified`" in body

    def test_image_preview_uses_commit_sha_url(self, tmp_path, monkeypatch):
        from src.workspace.commands.lifecycle import _build_pr_body

        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(
            slug="test", name="Test Corp", website="https://test.com", issue=1, pr=10
        )
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")

        img_dir = tmp_path / "images" / "test"
        img_dir.mkdir(parents=True)
        (img_dir / "logo.png").write_bytes(b"PNG")
        (img_dir / "icon.svg").write_text("<svg></svg>")

        monkeypatch.setattr(
            "src.workspace.git.repo_name_with_owner", lambda: "colophon-group/jobseek"
        )
        monkeypatch.setattr("src.workspace.git.current_commit", lambda: "abc123def")

        body = _build_pr_body(ws_obj, [board])

        assert (
            "https://raw.githubusercontent.com/colophon-group/jobseek/abc123def/"
            "apps/crawler/data/images/test/logo.png"
        ) in body
        assert (
            "https://raw.githubusercontent.com/colophon-group/jobseek/abc123def/"
            "apps/crawler/data/images/test/icon.svg"
        ) in body

    def test_includes_quality_and_configs(self, tmp_path, monkeypatch):
        from src.workspace.commands.lifecycle import _build_pr_body

        ws_obj = Workspace(
            slug="test",
            name="Test Corp",
            website="https://test.com",
            issue=1,
            pr=10,
        )
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 50},
            "feedback": {"verdict": "good", "fields": {"title": "clean", "description": "clean"}},
            "cost": {"monitor_per_cycle": 1.0},
        }
        board.configs["sitemap"] = {
            "monitor_type": "sitemap",
            "monitor_config": {},
            "status": "rejected",
        }
        board.active_config = "greenhouse"
        board.monitor_run = {"jobs": 50}

        body = _build_pr_body(ws_obj, [board])
        assert "Closes #1" in body
        assert "Test Corp" in body
        # Board slug as column header
        assert "test-careers" in body
        # Field quality rows in the table
        assert "title" in body and "clean" in body
        # Verdict row
        assert "**good**" in body
        # Configs evaluated section (>1 config)
        assert "Configurations evaluated" in body
        assert "**selected**" in body
        assert "rejected" in body

    def test_pr_body_normalizes_stale_selection_and_shows_truncation(self):
        from src.workspace.commands.lifecycle import _build_pr_body

        ws_obj = Workspace(slug="test", name="Test Corp")
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs = {
            "old": {
                "monitor_type": "api_sniffer",
                "status": "selected",
                "run": {"jobs": 10},
                "feedback": {"verdict": "unusable"},
            },
            "current": {
                "monitor_type": "oracle_hcm",
                "status": "tested",
                "run": {"jobs": 9997, "truncated": True},
                "feedback": {"verdict": "poor"},
            },
        }
        board.active_config = "current"

        body = _build_pr_body(ws_obj, [board])

        assert "| Completeness | **incomplete (truncated)** |" in body
        assert "| 1 | old | `api_sniffer` |" in body
        assert "| tested | unusable |" in body
        assert "**selected** (incomplete)" in body

    def test_single_config_no_comparison(self, tmp_path, monkeypatch):
        from src.workspace.commands.lifecycle import _build_pr_body

        ws_obj = Workspace(slug="test", name="Test Corp", issue=1, pr=10)
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
        }
        board.active_config = "greenhouse"

        body = _build_pr_body(ws_obj, [board])
        assert "Configurations evaluated" not in body

    def test_multi_board_horizontal(self, tmp_path, monkeypatch):
        from src.workspace.commands.lifecycle import _build_pr_body

        ws_obj = Workspace(
            slug="kpmg",
            name="KPMG",
            website="https://kpmg.com",
            issue=42,
            pr=99,
        )
        board1 = Board(alias="careers", slug="kpmg-careers", url="https://jobs.kpmg.ch")
        board1.configs["dom"] = {
            "monitor_type": "dom",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 56},
            "cost": {"monitor_per_cycle": 12.0},
            "feedback": {
                "verdict": "good",
                "fields": {"title": "clean", "description": "clean"},
            },
        }
        board1.active_config = "dom"
        board1.monitor_run = {"jobs": 56}

        board2 = Board(alias="fr", slug="kpmg-fr", url="https://kpmg.fr/emplois")
        board2.configs["dom"] = {
            "monitor_type": "dom",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 217},
            "cost": {"monitor_per_cycle": 5.0},
            "feedback": {
                "verdict": "acceptable",
                "verdict_notes": "Locations noisy",
                "fields": {
                    "title": "clean",
                    "description": "clean",
                    "locations": {"coverage": "200/217", "quality": "noisy"},
                },
            },
        }
        board2.active_config = "dom"
        board2.monitor_run = {"jobs": 217}

        body = _build_pr_body(ws_obj, [board1, board2])
        # Both board slugs appear as column headers
        assert "kpmg-careers" in body
        assert "kpmg-fr" in body
        # Single table — both boards' data in the same table
        assert "| URL |" in body or "URL" in body
        # Verdicts in same row
        assert "**good**" in body
        assert "**acceptable**" in body
        # Locations field from board2
        assert "200/217 (noisy)" in body


class TestFormatCrawlStats:
    """Test enriched crawl stats comment."""

    def test_verdict_in_metrics_no_field_tiers(self):
        from src.workspace.log import format_crawl_stats

        boards = {
            "careers": {
                "slug": "test-careers",
                "active_config": "greenhouse",
                "configs": {
                    "greenhouse": {
                        "monitor_type": "greenhouse",
                        "status": "tested",
                        "run": {"jobs": 50, "time": 0.9},
                        "cost": {"monitor_per_cycle": 0.9},
                        "feedback": {
                            "verdict": "acceptable",
                            "fields": {
                                "title": "clean",
                                "description": "clean",
                                "locations": "noisy",
                                "employment_type": "absent",
                            },
                        },
                    },
                },
            },
        }
        result = format_crawl_stats(boards)
        # Verdict appears in the board row
        assert "**acceptable**" in result
        # Field coverage is NOT in stats comment (only in PR body)
        assert "Field Coverage" not in result
        assert "Required" not in result

    def test_no_feedback_still_works(self):
        from src.workspace.log import format_crawl_stats

        boards = {
            "careers": {
                "slug": "test-careers",
                "active_config": "greenhouse",
                "configs": {
                    "greenhouse": {
                        "monitor_type": "greenhouse",
                        "run": {"jobs": 50, "time": 0.9},
                    },
                },
            },
        }
        result = format_crawl_stats(boards)
        assert "50" in result
        assert "Field Coverage" not in result


class TestGitStateHelpers:
    """Test has_uncommitted_changes and is_ahead_of_remote."""

    def test_has_uncommitted_changes_api(self):
        from src.workspace.git import has_uncommitted_changes, is_ahead_of_remote

        # Just verify they can be imported and have correct signatures
        assert callable(has_uncommitted_changes)
        assert callable(is_ahead_of_remote)


# ── Phase 7: Work continuation ──────────────────────────────────────────


class TestCliStartupWorktreeAuthentication:
    def test_startup_missing_identity_cannot_pivot_or_mutate(self, tmp_path, monkeypatch):
        from src.workspace.cli import _pivot_to_worktree

        _patch_all(monkeypatch, tmp_path)
        managed_worktrees = tmp_path / "managed-worktrees"
        managed_worktree = managed_worktrees / "test"
        save_workspace(
            Workspace(
                slug="test",
                branch="add-company/test",
                worktree=str(managed_worktree),
            )
        )
        set_active_slug("test")
        pivot = MagicMock()
        mutate = MagicMock()
        authenticate = MagicMock()
        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: managed_worktrees)
        monkeypatch.setattr("src.shared.constants.get_repo_root", lambda: tmp_path / "outer")
        monkeypatch.setattr("src.shared.constants.set_repo_root", pivot)
        monkeypatch.setattr("src.workspace.git.authenticate_managed_worktree", authenticate)
        monkeypatch.setattr("src.workspace.git._run", mutate)

        with pytest.raises(WorkspaceError, match="authenticated worktree identity"):
            _pivot_to_worktree()

        pivot.assert_not_called()
        authenticate.assert_not_called()
        mutate.assert_not_called()

    def test_startup_replaced_identity_cannot_pivot_or_mutate(self, tmp_path, monkeypatch):
        from src.workspace.cli import _pivot_to_worktree

        _patch_all(monkeypatch, tmp_path)
        managed_worktrees = tmp_path / "managed-worktrees"
        managed_worktree = managed_worktrees / "test"
        save_workspace(TestPreflight._owned_workspace(managed_worktree))
        set_active_slug("test")
        pivot = MagicMock()
        mutate = MagicMock()
        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: managed_worktrees)
        monkeypatch.setattr("src.shared.constants.get_repo_root", lambda: tmp_path / "outer")
        monkeypatch.setattr("src.shared.constants.set_repo_root", pivot)
        monkeypatch.setattr(
            "src.workspace.git.authenticate_managed_worktree",
            MagicMock(side_effect=WorkspaceError("replacement filesystem entry")),
        )
        monkeypatch.setattr("src.workspace.git._run", mutate)

        with pytest.raises(WorkspaceError, match="replacement filesystem entry"):
            _pivot_to_worktree()

        pivot.assert_not_called()
        mutate.assert_not_called()

    def test_terminal_delete_recovery_rejects_replaced_identity_before_pivot(
        self, tmp_path, monkeypatch
    ):
        from src.workspace import cli
        from src.workspace.commands import lifecycle

        _patch_all(monkeypatch, tmp_path)
        managed_worktrees = tmp_path / "managed-worktrees"
        managed_worktree = managed_worktrees / "test"
        managed_worktree.mkdir(parents=True)
        item = managed_worktree.stat()
        ws_obj = Workspace(
            slug="test",
            branch="add-company/test",
            worktree=str(managed_worktree),
            worktree_identity={
                "version": 1,
                "path": str(managed_worktree),
                "slug": "test",
                "branch": "add-company/test",
                "head": TEST_HEAD_OID,
                "dev": int(item.st_dev),
                "ino": int(item.st_ino),
                "issue": None,
                "pr": None,
                "pr_provenance": {},
            },
        )
        save_workspace(ws_obj)
        set_active_slug("test")
        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: managed_worktrees)
        monkeypatch.setattr("src.workspace.git.remote_branch_oid_strict", lambda *_: None)
        monkeypatch.setattr("src.workspace.git.local_branch_oid_strict", lambda *_: TEST_HEAD_OID)
        monkeypatch.setattr(
            "src.workspace.git.authenticate_managed_worktree", lambda *_args, **_kwargs: True
        )
        journal = lifecycle._initialize_terminal_journal(ws_obj, local=False, outcome=None)
        lifecycle._set_terminal_attempt(journal, "worktree_remove")

        pivot = MagicMock()
        mutate = MagicMock()
        monkeypatch.setattr("src.shared.constants.get_repo_root", lambda: tmp_path / "outer")
        monkeypatch.setattr("src.shared.constants.set_repo_root", pivot)
        monkeypatch.setattr(
            "src.workspace.git.authenticate_terminal_worktree_removal_state",
            MagicMock(side_effect=WorkspaceError("replacement filesystem entry")),
        )
        monkeypatch.setattr("src.workspace.git._run", mutate)

        with pytest.raises(WorkspaceError, match="replacement filesystem entry"):
            cli._pivot_to_worktree(["del", "test"])

        pivot.assert_not_called()
        mutate.assert_not_called()


class TestPreflight:
    """Test preflight checks."""

    @staticmethod
    def _owned_workspace(path, *, slug="test", branch="add-company/test"):
        return Workspace(
            slug=slug,
            branch=branch,
            worktree=str(path),
            worktree_identity={
                "version": 1,
                "path": str(path),
                "slug": slug,
                "branch": branch,
                "head": TEST_HEAD_OID,
                "dev": 1,
                "ino": 2,
                "issue": None,
                "pr": None,
                "pr_provenance": {},
            },
        )

    def test_preflight_detects_wrong_branch(self, tmp_path, monkeypatch):
        from src.workspace.preflight import run_preflight

        _patch_all(monkeypatch, tmp_path)

        # Branch exists but is not checked out
        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "branch" in args and "--list" in args:
                result.stdout = "  add-company/test\n"
            elif "rev-parse" in args and "--abbrev-ref" in args:
                result.stdout = "main\n"
            else:
                result.stdout = ""
            return result

        monkeypatch.setattr("src.workspace.git._run", mock_run)
        monkeypatch.setattr(
            "src.workspace.preflight.pivot_to_workspace_worktree", lambda _workspace: None
        )

        ws_obj = Workspace(slug="test", branch="add-company/test")
        issues = run_preflight(ws_obj)
        assert any(i.code == "wrong_branch" for i in issues)

    def test_preflight_no_issue_when_on_correct_branch(self, tmp_path, monkeypatch):
        from src.workspace.preflight import run_preflight

        _patch_all(monkeypatch, tmp_path)

        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "branch" in args and "--list" in args:
                result.stdout = "  add-company/test\n"
            elif "rev-parse" in args and "--abbrev-ref" in args:
                result.stdout = "add-company/test\n"
            else:
                result.stdout = ""
            return result

        monkeypatch.setattr("src.workspace.git._run", mock_run)
        monkeypatch.setattr(
            "src.workspace.preflight.pivot_to_workspace_worktree", lambda _workspace: None
        )

        ws_obj = Workspace(slug="test", branch="add-company/test")
        issues = run_preflight(ws_obj)
        assert not issues

    def test_preflight_pivots_only_after_exact_persisted_identity_authentication(
        self, tmp_path, monkeypatch
    ):
        from src.workspace.preflight import run_preflight

        _patch_all(monkeypatch, tmp_path, strict_worktree=True)

        outer_root = tmp_path / "resolver-worktree"
        managed_worktrees = tmp_path / "managed-worktrees"
        managed_worktree = managed_worktrees / "test"
        (managed_worktree / "apps" / "crawler" / "data").mkdir(parents=True)
        (managed_worktree / ".git").touch()
        repo_root = {"path": outer_root}

        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: managed_worktrees)
        monkeypatch.setattr(
            "src.workspace.git.authenticate_managed_worktree", lambda *_args, **_kwargs: True
        )
        monkeypatch.setattr(
            "src.workspace.git.local_branch_oid_strict", lambda _branch: TEST_HEAD_OID
        )
        monkeypatch.setattr("src.shared.constants.get_repo_root", lambda: repo_root["path"])
        monkeypatch.setattr(
            "src.shared.constants.set_repo_root",
            lambda path: repo_root.__setitem__("path", path),
        )

        def mock_run(args, **kwargs):
            result = MagicMock(returncode=0)
            if "branch" in args and "--list" in args:
                result.stdout = (
                    "  add-company/test\n" if repo_root["path"] == managed_worktree else ""
                )
            elif "rev-parse" in args and "--abbrev-ref" in args:
                result.stdout = "add-company/test\n"
            else:
                result.stdout = ""
            return result

        monkeypatch.setattr("src.workspace.git._run", mock_run)

        ws_obj = self._owned_workspace(managed_worktree)
        issues = run_preflight(ws_obj)

        assert repo_root["path"] == managed_worktree
        assert not issues

    def test_preflight_rejects_noncanonical_workspace_worktree(self, tmp_path, monkeypatch):
        from src.workspace.preflight import run_preflight

        _patch_all(monkeypatch, tmp_path, strict_worktree=True)

        managed_worktrees = tmp_path / "managed-worktrees"
        unexpected = tmp_path / "other-checkout"
        (managed_worktrees / "test" / "apps" / "crawler" / "data").mkdir(parents=True)
        (managed_worktrees / "test" / ".git").touch()
        (unexpected / "apps" / "crawler" / "data").mkdir(parents=True)
        (unexpected / ".git").touch()
        outer_root = tmp_path / "resolver-worktree"
        repo_root = {"path": outer_root}

        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: managed_worktrees)
        monkeypatch.setattr("src.shared.constants.get_repo_root", lambda: repo_root["path"])
        monkeypatch.setattr(
            "src.shared.constants.set_repo_root",
            lambda path: repo_root.__setitem__("path", path),
        )
        authenticate = MagicMock()
        mutate = MagicMock()
        monkeypatch.setattr("src.workspace.git.authenticate_managed_worktree", authenticate)
        monkeypatch.setattr("src.workspace.git._run", mutate)

        issues = run_preflight(
            Workspace(
                slug="test",
                branch="add-company/test",
                worktree=str(unexpected),
            )
        )

        assert repo_root["path"] == outer_root
        assert [(issue.code, issue.severity) for issue in issues] == [("worktree_auth", "critical")]
        authenticate.assert_not_called()
        mutate.assert_not_called()

    def test_preflight_rejects_missing_identity_before_board_mutation(self, tmp_path, monkeypatch):
        from src.workspace.preflight import run_preflight

        _patch_all(monkeypatch, tmp_path, strict_worktree=True)

        managed_worktrees = tmp_path / "managed-worktrees"
        managed_worktree = managed_worktrees / "test"
        (managed_worktree / "apps" / "crawler" / "data").mkdir(parents=True)
        repo_root = {"path": tmp_path / "resolver-worktree"}
        pivot = MagicMock(side_effect=lambda path: repo_root.__setitem__("path", path))
        authenticate = MagicMock()
        mutate = MagicMock()
        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: managed_worktrees)
        monkeypatch.setattr("src.shared.constants.get_repo_root", lambda: repo_root["path"])
        monkeypatch.setattr("src.shared.constants.set_repo_root", pivot)
        monkeypatch.setattr("src.workspace.git.authenticate_managed_worktree", authenticate)
        monkeypatch.setattr("src.workspace.git._run", mutate)

        issues = run_preflight(
            Workspace(
                slug="test",
                branch="add-company/test",
                worktree=str(managed_worktree),
            ),
            check_branch=False,
        )

        assert [(issue.code, issue.severity) for issue in issues] == [("worktree_auth", "critical")]
        assert repo_root["path"] == tmp_path / "resolver-worktree"
        pivot.assert_not_called()
        authenticate.assert_not_called()
        mutate.assert_not_called()

    def test_preflight_rejects_replaced_identity_before_board_mutation(self, tmp_path, monkeypatch):
        from src.workspace.preflight import run_preflight

        _patch_all(monkeypatch, tmp_path, strict_worktree=True)
        managed_worktrees = tmp_path / "managed-worktrees"
        managed_worktree = managed_worktrees / "test"
        outer_root = tmp_path / "resolver-worktree"
        repo_root = {"path": outer_root}
        pivot = MagicMock(side_effect=lambda path: repo_root.__setitem__("path", path))
        mutate = MagicMock()
        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: managed_worktrees)
        monkeypatch.setattr("src.shared.constants.get_repo_root", lambda: repo_root["path"])
        monkeypatch.setattr("src.shared.constants.set_repo_root", pivot)
        monkeypatch.setattr(
            "src.workspace.git.authenticate_managed_worktree",
            MagicMock(side_effect=WorkspaceError("replacement filesystem entry")),
        )
        monkeypatch.setattr("src.workspace.git._run", mutate)

        issues = run_preflight(
            self._owned_workspace(managed_worktree),
            check_branch=False,
        )

        assert [(issue.code, issue.severity) for issue in issues] == [("worktree_auth", "critical")]
        assert "replacement filesystem entry" in issues[0].message
        assert repo_root["path"] == outer_root
        pivot.assert_not_called()
        mutate.assert_not_called()

    def test_preflight_no_branch_check_when_disabled(self, tmp_path, monkeypatch):
        from src.workspace.preflight import run_preflight

        _patch_all(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "src.workspace.preflight.pivot_to_workspace_worktree", lambda _workspace: None
        )

        ws_obj = Workspace(slug="test", branch="add-company/test")
        issues = run_preflight(ws_obj, check_branch=False)
        assert not issues


class TestResume:
    """Test ws resume command."""

    def test_resume_preserves_partial_rich_auto_scraper_config(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(
            slug="test",
            name="Test Corp",
            website="https://test.com",
            active_board="careers",
        )
        save_workspace(ws_obj)
        set_active_slug("test")
        save_board(
            "test",
            Board(alias="careers", slug="test-careers", url="https://test.com/jobs"),
        )

        selected = CliRunner().invoke(ws, ["select", "monitor", "test", "paylocity"])
        assert selected.exit_code == 0

        ws_obj = load_workspace("test")
        ws_obj.branch = "add-company/test"
        save_workspace(ws_obj)

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.workspace.git._run",
                    return_value=MagicMock(stdout="  add-company/test\n", returncode=0),
                )
            )
            stack.enter_context(
                patch("src.workspace.git.current_branch", return_value="add-company/test")
            )
            resumed = CliRunner().invoke(ws, ["resume", "test"])

        assert resumed.exit_code == 0
        board = load_board("test", "careers")
        active = board.configs[board.active_config]
        assert active["scraper_type"] == "paylocity"
        assert active["scraper_config"] == {
            "enrich": ["description", "employment_type", "job_location_type"]
        }

    def test_resume_ready_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(
            slug="test",
            name="Test Corp",
            website="https://test.com",
            issue=1,
            pr=10,
            branch="add-company/test",
        )
        save_workspace(ws_obj)
        set_active_slug("test")

        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 50},
            "feedback": {"verdict": "good", "fields": {"title": "clean"}},
            "cost": {"monitor_per_cycle": 1.0},
        }
        board.active_config = "greenhouse"
        save_board("test", board)

        # Mock git calls (resume checks environment)
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.workspace.git._run",
                    return_value=MagicMock(stdout="  add-company/test\n", returncode=0),
                )
            )
            stack.enter_context(
                patch(
                    "src.workspace.git.current_branch",
                    return_value="add-company/test",
                )
            )

            runner = CliRunner()
            result = runner.invoke(ws, ["resume", "test"])

        assert result.exit_code == 0
        assert "Test Corp" in result.output
        assert "Ready" in result.output

    def test_resume_no_config(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(
            slug="test", name="Test Corp", website="https://test.com", branch="add-company/test"
        )
        save_workspace(ws_obj)
        set_active_slug("test")

        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        save_board("test", board)

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.workspace.git._run",
                    return_value=MagicMock(stdout="  add-company/test\n", returncode=0),
                )
            )
            stack.enter_context(
                patch(
                    "src.workspace.git.current_branch",
                    return_value="add-company/test",
                )
            )

            runner = CliRunner()
            result = runner.invoke(ws, ["resume", "test"])

        assert result.exit_code == 0
        assert "no config selected" in result.output

    def test_resume_shows_last_error(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(
            slug="test", name="Test", website="https://test.com", branch="add-company/test"
        )
        ws_obj.last_error = {
            "command": "submit",
            "step": "pushed",
            "error": "connection refused",
            "at": "2025-03-04T10:23:45Z",
        }
        save_workspace(ws_obj)
        set_active_slug("test")

        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 50},
            "feedback": {"verdict": "good", "fields": {}},
        }
        board.active_config = "greenhouse"
        save_board("test", board)

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.workspace.git._run",
                    return_value=MagicMock(stdout="  add-company/test\n", returncode=0),
                )
            )
            stack.enter_context(
                patch(
                    "src.workspace.git.current_branch",
                    return_value="add-company/test",
                )
            )

            runner = CliRunner()
            result = runner.invoke(ws, ["resume", "test"])

        assert result.exit_code == 0
        assert "Last error" in result.output
        assert "connection refused" in result.output

    def test_resume_no_boards(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(
            slug="test", name="Test", website="https://test.com", branch="add-company/test"
        )
        save_workspace(ws_obj)
        set_active_slug("test")

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.workspace.git._run",
                    return_value=MagicMock(stdout="  add-company/test\n", returncode=0),
                )
            )
            stack.enter_context(
                patch(
                    "src.workspace.git.current_branch",
                    return_value="add-company/test",
                )
            )

            runner = CliRunner()
            result = runner.invoke(ws, ["resume", "test"])

        assert result.exit_code == 0
        assert "No boards configured" in result.output


class TestStatusEnhanced:
    """Test enhanced status output with named configs."""

    def test_status_shows_config_info(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(
            slug="test",
            name="Test Corp",
            website="https://test.com",
            issue=1,
            pr=10,
        )
        save_workspace(ws_obj)
        set_active_slug("test")
        ws_obj.active_board = "careers"
        save_workspace(ws_obj)

        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 50},
            "feedback": {"verdict": "good", "fields": {}},
            "cost": {"monitor_per_cycle": 1.0},
        }
        board.active_config = "greenhouse"
        save_board("test", board)

        runner = CliRunner()
        result = runner.invoke(ws, ["status", "test"])
        assert result.exit_code == 0
        assert "greenhouse" in result.output
        assert "50 jobs" in result.output
        assert "good" in result.output

    def test_status_shows_last_error(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(slug="test", name="Test", website="https://test.com")
        ws_obj.last_error = {"command": "submit", "step": "pushed", "error": "timeout"}
        save_workspace(ws_obj)
        set_active_slug("test")

        runner = CliRunner()
        result = runner.invoke(ws, ["status", "test"])
        assert result.exit_code == 0
        assert "Last error" in result.output
        assert "timeout" in result.output

    def test_status_ready_to_submit(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(slug="test", name="Test", website="https://test.com")
        save_workspace(ws_obj)
        set_active_slug("test")

        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 50},
            "feedback": {"verdict": "good", "fields": {}},
        }
        board.active_config = "greenhouse"
        save_board("test", board)

        runner = CliRunner()
        result = runner.invoke(ws, ["status", "test"])
        assert result.exit_code == 0
        assert "ready to submit" in result.output


# ── Phase 8: Edge Case Hardening ─────────────────────────────────────


class TestYamlCorruption:
    """YAML corruption handling in load/list functions."""

    def test_load_workspace_corrupt_yaml(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        # Create a valid workspace first so the dir structure exists
        save_workspace(Workspace(slug="test"))
        # Corrupt the YAML
        ws_yaml_path("test").write_text(": : : invalid yaml {{{\n")

        import pytest

        with pytest.raises(WorkspaceStateError, match="Corrupt workspace YAML"):
            load_workspace("test")

    def test_load_workspace_non_mapping(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        ws_yaml_path("test").write_text("just a string\n")

        import pytest

        with pytest.raises(WorkspaceStateError, match="expected mapping"):
            load_workspace("test")

    def test_load_board_corrupt_yaml(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="a", slug="test-a", url="https://a.com"))
        board_yaml_path("test", "a").write_text("{{invalid\n")

        import pytest

        with pytest.raises(WorkspaceStateError, match="Corrupt board YAML"):
            load_board("test", "a")

    def test_load_board_non_mapping(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="a", slug="test-a", url="https://a.com"))
        board_yaml_path("test", "a").write_text("42\n")

        import pytest

        with pytest.raises(WorkspaceStateError, match="expected mapping"):
            load_board("test", "a")

    def test_list_boards_skips_corrupt(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board("test", Board(alias="good", slug="test-good", url="https://good.com"))
        save_board("test", Board(alias="bad", slug="test-bad", url="https://bad.com"))
        # Corrupt one board
        board_yaml_path("test", "bad").write_text("{{invalid\n")

        boards = list_boards("test")
        assert len(boards) == 1
        assert boards[0].alias == "good"

    def test_list_workspaces_skips_corrupt(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="alpha"))
        save_workspace(Workspace(slug="beta"))
        # Corrupt one
        ws_yaml_path("beta").write_text("{{invalid\n")

        from src.workspace.state import list_workspaces

        workspaces = list_workspaces()
        assert len(workspaces) == 1
        assert workspaces[0].slug == "alpha"


class TestStaleProbeDetection:
    """Stale probe detection when board URL changed since probe."""

    def test_select_monitor_warns_stale_probe(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test", active_board="a"))
        set_active_slug("test")
        board = Board(alias="a", slug="test-a", url="https://new-url.com/jobs")
        # Detections were made against the old URL
        board.detections = {
            "_meta": {"url": "https://old-url.com/jobs"},
            "greenhouse": {"token": "abc"},
        }
        save_board("test", board)

        monkeypatch.setattr("src.workspace.preflight.run_preflight", lambda *a, **kw: [])

        with patch("src.workspace.commands.crawl.save_board"):
            runner = CliRunner()
            result = runner.invoke(ws, ["select", "monitor", "greenhouse"])
        assert result.exit_code == 0
        assert "re-probe recommended" in result.output

    def test_select_monitor_no_warning_when_url_matches(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test", active_board="a"))
        set_active_slug("test")
        board = Board(alias="a", slug="test-a", url="https://same.com/jobs")
        board.detections = {
            "_meta": {"url": "https://same.com/jobs"},
            "greenhouse": {"token": "abc"},
        }
        save_board("test", board)

        monkeypatch.setattr("src.workspace.preflight.run_preflight", lambda *a, **kw: [])

        with patch("src.workspace.commands.crawl.save_board"):
            runner = CliRunner()
            result = runner.invoke(ws, ["select", "monitor", "greenhouse"])
        assert result.exit_code == 0
        assert "re-probe" not in result.output


class TestMonitorRegression:
    """Monitor regression detection: previous run had jobs, now 0."""

    def test_regression_warning_on_zero_jobs(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test", active_board="a"))
        set_active_slug("test")
        board = Board(alias="a", slug="test-a", url="https://test.com/jobs")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "tested",
            "run": {"jobs": 50},  # Previous run had 50 jobs
        }
        board.active_config = "greenhouse"
        save_board("test", board)

        monkeypatch.setattr("src.workspace.preflight.run_preflight", lambda *a, **kw: [])

        # Mock monitor_one to return 0 jobs
        @dataclass
        class FakeResult:
            urls: set
            jobs_by_url: dict | None
            filtered_count: int

        fake = FakeResult(urls=set(), jobs_by_url=None, filtered_count=0)

        with ExitStack() as stack:
            asyncio_run = _enter_asyncio_run_patch(stack)
            asyncio_run.return_value = _as_run_monitor_result(
                fake, 1.0, [], monitor_type="greenhouse"
            )
            stack.enter_context(patch("src.workspace.commands.crawl.save_board"))
            stack.enter_context(
                patch(
                    "src.workspace.artifacts.monitor_run_dir",
                    return_value=tmp_path / "run",
                )
            )
            stack.enter_context(patch("src.workspace.artifacts.capture_structlog", return_value=[]))
            stack.enter_context(patch("src.workspace.artifacts.save_http_log"))
            stack.enter_context(patch("src.workspace.artifacts.save_events"))
            stack.enter_context(patch("src.workspace.artifacts.save_jobs"))

            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor"])

        assert "Regression" in result.output
        assert "previous run found 50 jobs" in result.output

    def test_no_regression_on_first_run(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test", active_board="a"))
        set_active_slug("test")
        board = Board(alias="a", slug="test-a", url="https://test.com/jobs")
        board.configs["greenhouse"] = {
            "monitor_type": "greenhouse",
            "monitor_config": {},
            "status": "selected",
        }
        board.active_config = "greenhouse"
        save_board("test", board)

        monkeypatch.setattr("src.workspace.preflight.run_preflight", lambda *a, **kw: [])

        @dataclass
        class FakeResult:
            urls: set
            jobs_by_url: dict | None
            filtered_count: int

        fake = FakeResult(urls=set(), jobs_by_url=None, filtered_count=0)

        with ExitStack() as stack:
            asyncio_run = _enter_asyncio_run_patch(stack)
            asyncio_run.return_value = _as_run_monitor_result(
                fake, 1.0, [], monitor_type="greenhouse"
            )
            stack.enter_context(patch("src.workspace.commands.crawl.save_board"))
            stack.enter_context(
                patch(
                    "src.workspace.artifacts.monitor_run_dir",
                    return_value=tmp_path / "run",
                )
            )
            stack.enter_context(patch("src.workspace.artifacts.capture_structlog", return_value=[]))
            stack.enter_context(patch("src.workspace.artifacts.save_http_log"))
            stack.enter_context(patch("src.workspace.artifacts.save_events"))
            stack.enter_context(patch("src.workspace.artifacts.save_jobs"))

            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor"])

        # 0 jobs warning should appear, but NOT the regression warning
        assert "0 jobs" in result.output
        assert "Regression" not in result.output


class TestPreflightBranchMissing:
    """Preflight detects missing branches."""

    def test_preflight_branch_missing_aborts(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)

        save_workspace(Workspace(slug="test", branch="add-company/test", active_board="a"))
        set_active_slug("test")
        save_board("test", Board(alias="a", slug="test-a", url="https://test.com/jobs"))

        # Mock git to show branch doesn't exist
        mock_result = MagicMock()
        mock_result.stdout = ""  # Branch not in list
        mock_result.returncode = 0

        with patch("src.workspace.git._run", return_value=mock_result):
            runner = CliRunner()
            result = runner.invoke(ws, ["select", "monitor", "greenhouse"])

        # Should abort with critical preflight issue
        assert result.exit_code != 0
        assert "not found locally" in result.output

    def test_preflight_branch_exists_no_abort(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)

        save_workspace(Workspace(slug="test", branch="add-company/test", active_board="a"))
        set_active_slug("test")
        save_board("test", Board(alias="a", slug="test-a", url="https://test.com/jobs"))

        # Mock: branch list shows the branch, current branch matches
        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "branch" in args and "--list" in args:
                result.stdout = "  add-company/test\n"
            elif "rev-parse" in args:
                result.stdout = "add-company/test\n"
            else:
                result.stdout = ""
            return result

        with (
            patch("src.workspace.git._run", side_effect=mock_run),
            patch("src.workspace.commands.crawl.save_board"),
        ):
            runner = CliRunner()
            result = runner.invoke(ws, ["select", "monitor", "greenhouse"])

        assert result.exit_code == 0


class TestResumeMergedPr:
    """Resume handles merged PRs."""

    def test_resume_shows_merged_pr(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        ws_obj = Workspace(
            slug="test",
            branch="add-company/test",
            pr=42,
            name="Test",
            website="https://test.com",
        )
        save_workspace(ws_obj)
        set_active_slug("test")
        save_board("test", Board(alias="a", slug="test-a", url="https://test.com/jobs"))

        def mock_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 0
            if "branch" in args and "--list" in args:
                result.stdout = "  add-company/test\n"
            elif "rev-parse" in args and "--abbrev-ref" in args:
                result.stdout = "add-company/test\n"
            elif "pr" in args and "view" in args:
                import json as j

                result.stdout = j.dumps({"state": "MERGED"})
            else:
                result.stdout = ""
            return result

        with patch("src.workspace.git._run", side_effect=mock_run):
            runner = CliRunner()
            result = runner.invoke(ws, ["resume"])

        assert result.exit_code == 0
        assert "already merged" in result.output


def _write_discovery_state(tmp_path, slug, hreflang_links, homepage_url="https://acme.com"):
    """Write a minimal discovery.state.yaml with hreflang data."""
    import yaml as _yaml

    ws_state_dir = tmp_path / ".ws" / slug
    ws_state_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1,
        "homepage_url": homepage_url,
        "hreflang": {
            "total": len(hreflang_links),
            "career_filtered": len(hreflang_links),
            "links": hreflang_links,
        },
    }
    (ws_state_dir / "discovery.state.yaml").write_text(_yaml.dump(state, default_flow_style=False))


class TestAddBoards:
    def test_add_boards_from_hreflang(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test"))
        set_active_slug("test")
        _write_discovery_state(
            tmp_path,
            "test",
            [
                {"url": "https://acme.com/en/careers", "hreflang": "en-US"},
                {"url": "https://acme.com/de/karriere", "hreflang": "de-DE"},
                {"url": "https://acme.com/fr/carrieres", "hreflang": "fr-FR"},
                {"url": "https://acme.com/careers", "hreflang": "x-default"},
            ],
        )

        runner = CliRunner()
        result = runner.invoke(ws, ["add", "boards"])
        assert result.exit_code == 0
        assert "Created 3 boards" in result.output

        boards = list_boards("test")
        aliases = {b.alias for b in boards}
        assert aliases == {"careers-en-us", "careers-de-de", "careers-fr-fr"}

        # Active board set to last created
        ws_obj = load_workspace("test")
        assert ws_obj.active_board == "careers-fr-fr"

    def test_add_boards_skips_existing(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test"))
        set_active_slug("test")
        save_board(
            "test",
            Board(
                alias="careers-en-us", slug="test-careers-en-us", url="https://acme.com/en/careers"
            ),
        )

        _write_discovery_state(
            tmp_path,
            "test",
            [
                {"url": "https://acme.com/en/careers", "hreflang": "en-US"},
                {"url": "https://acme.com/de/karriere", "hreflang": "de-DE"},
            ],
        )

        runner = CliRunner()
        result = runner.invoke(ws, ["add", "boards"])
        assert result.exit_code == 0
        assert "Created 1 boards" in result.output
        assert "skipped-duplicate" in result.output

        boards = list_boards("test")
        assert len(boards) == 2

    def test_add_boards_only_filter(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test"))
        set_active_slug("test")
        _write_discovery_state(
            tmp_path,
            "test",
            [
                {"url": "https://acme.com/en/careers", "hreflang": "en-US"},
                {"url": "https://acme.com/de/karriere", "hreflang": "de-DE"},
                {"url": "https://acme.com/fr/carrieres", "hreflang": "fr-FR"},
            ],
        )

        runner = CliRunner()
        result = runner.invoke(ws, ["add", "boards", "--only", "en-US,de-DE"])
        assert result.exit_code == 0
        assert "Created 2 boards" in result.output

        boards = list_boards("test")
        aliases = {b.alias for b in boards}
        assert aliases == {"careers-en-us", "careers-de-de"}

    def test_add_boards_exclude_filter(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test"))
        set_active_slug("test")
        _write_discovery_state(
            tmp_path,
            "test",
            [
                {"url": "https://acme.com/en/careers", "hreflang": "en-US"},
                {"url": "https://acme.com/de/karriere", "hreflang": "de-DE"},
                {"url": "https://acme.com/fr/carrieres", "hreflang": "fr-FR"},
            ],
        )

        runner = CliRunner()
        result = runner.invoke(ws, ["add", "boards", "--exclude", "fr-FR"])
        assert result.exit_code == 0
        assert "Created 2 boards" in result.output

        boards = list_boards("test")
        aliases = {b.alias for b in boards}
        assert aliases == {"careers-en-us", "careers-de-de"}

    def test_add_boards_no_discovery_state(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test"))
        set_active_slug("test")

        runner = CliRunner()
        result = runner.invoke(ws, ["add", "boards"])
        assert result.exit_code != 0
        assert "No discovery state found" in result.output


class TestProbeAllBoards:
    def test_probe_all_boards_calls_each(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test"))
        set_active_slug("test")
        save_board(
            "test",
            Board(alias="careers-us", slug="test-careers-us", url="https://acme.com/us/careers"),
        )
        save_board(
            "test",
            Board(alias="careers-de", slug="test-careers-de", url="https://acme.com/de/karriere"),
        )
        save_board(
            "test",
            Board(alias="careers-fr", slug="test-careers-fr", url="https://acme.com/fr/carrieres"),
        )

        probed_urls = []

        from src.workspace.commands.crawl import _process_probe_results

        def patched_probe_all(slug, current_jobs):
            from src.workspace import output as _out

            boards_list = list_boards(slug)
            for b in boards_list:
                probed_urls.append(b.url)
                results = [
                    ("greenhouse", {"jobs": 10, "token": "acme"}, "Greenhouse API — 10 jobs")
                ]
                _process_probe_results(slug, b, results, current_jobs)

            _out.info("probe", f"Batch probe complete — {len(boards_list)} boards")

        monkeypatch.setattr("src.workspace.commands.crawl._probe_all_boards", patched_probe_all)

        runner = CliRunner()
        result = runner.invoke(ws, ["probe", "monitor", "-n", "10", "--all-boards"])
        assert result.exit_code == 0
        assert len(probed_urls) == 3
        assert "https://acme.com/us/careers" in probed_urls
        assert "https://acme.com/de/karriere" in probed_urls
        assert "https://acme.com/fr/carrieres" in probed_urls

    def test_probe_all_boards_stores_detections(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="test"))
        set_active_slug("test")
        save_board(
            "test",
            Board(alias="careers-us", slug="test-careers-us", url="https://acme.com/us/careers"),
        )
        save_board(
            "test",
            Board(alias="careers-de", slug="test-careers-de", url="https://acme.com/de/karriere"),
        )

        from src.workspace.commands.crawl import _process_probe_results

        def patched_probe_all(slug, current_jobs):
            from src.workspace import output as _out

            boards_list = list_boards(slug)
            for b in boards_list:
                results = [
                    ("greenhouse", {"jobs": 42, "token": "acme"}, "Greenhouse API — 42 jobs"),
                    ("dom", None, "Not detected"),
                ]
                _process_probe_results(slug, b, results, current_jobs)

            _out.info("probe", f"Batch probe complete — {len(boards_list)} boards")

        monkeypatch.setattr("src.workspace.commands.crawl._probe_all_boards", patched_probe_all)

        runner = CliRunner()
        result = runner.invoke(ws, ["probe", "monitor", "-n", "42", "--all-boards"])
        assert result.exit_code == 0

        board_us = load_board("test", "careers-us")
        assert "greenhouse" in board_us.detections
        assert board_us.detections["greenhouse"]["jobs"] == 42

        board_de = load_board("test", "careers-de")
        assert "greenhouse" in board_de.detections
        assert board_de.detections["greenhouse"]["jobs"] == 42


class TestNewIdempotent:
    """ws new should not fail when slug already exists in CSV from a prior attempt."""

    def test_new_does_not_replace_same_slug_workspace_owned_by_another_issue(
        self, tmp_path, monkeypatch
    ):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        save_workspace(Workspace(slug="acme", issue=1, branch="add-company/acme"))

        with patch("src.workspace.git.ensure_clone", return_value=tmp_path):
            result = CliRunner().invoke(ws, ["new", "acme", "--issue", "2"])

        assert result.exit_code != 0
        assert "belongs to issue #1" in result.output
        assert load_workspace("acme").issue == 1

    def _git_mocks(
        self,
        stack,
        tmp_path,
        *,
        pr_branch=None,
        branch_pr=None,
        existing_prs=None,
        issue_body="",
        issue_labels=(),
    ):
        """Set up common git mocks for new() tests."""
        stack.enter_context(
            patch(
                "src.workspace.commands.lifecycle.is_local_mode",
                return_value=False,
            )
        )
        ensure_clone = stack.enter_context(
            patch("src.workspace.git.ensure_clone", return_value=tmp_path)
        )
        stack.enter_context(patch("src.workspace.git.check_gh_auth", return_value=True))
        stack.enter_context(
            patch(
                "src.workspace.git.fetch_issue",
                return_value={
                    "title": "Add company: Acme",
                    "body": issue_body,
                    "labels": [{"name": label} for label in issue_labels],
                },
            )
        )
        stack.enter_context(patch("src.workspace.git.fetch"))
        stack.enter_context(
            patch("src.workspace.git.worktrees_dir", return_value=tmp_path / "worktrees")
        )
        stack.enter_context(patch("src.workspace.git.get_main_branch", return_value="main"))
        resolved_branch = pr_branch or "add-company/acme"
        stack.enter_context(
            patch(
                "src.workspace.git.get_pr_details_strict",
                side_effect=lambda number: _test_pr_details(
                    number,
                    slug="acme",
                    branch=resolved_branch,
                ),
            )
        )
        stack.enter_context(
            patch("src.workspace.git.get_authenticated_login_strict", return_value="resolver")
        )
        stack.enter_context(
            patch(
                "src.workspace.git.remote_branch_oid_strict",
                return_value=(TEST_HEAD_OID if pr_branch or existing_prs else None),
            )
        )
        stack.enter_context(
            patch("src.workspace.git.find_open_pr_for_branch", return_value=branch_pr)
        )
        delete_remote = stack.enter_context(patch("src.workspace.git.delete_remote_branch"))
        identity = {"head": TEST_HEAD_OID, "dev": 1, "ino": 2}
        create_worktree = stack.enter_context(
            patch("src.workspace.git.create_worktree", return_value=identity)
        )
        stack.enter_context(
            patch("src.workspace.git.managed_worktree_identity_strict", return_value=identity)
        )
        stack.enter_context(patch("src.workspace.git.remove_authenticated_worktree"))
        stack.enter_context(patch("src.workspace.git.delete_local_branch_at_expected_oid"))
        sync_branch = stack.enter_context(patch("src.workspace.git.sync_branch_with_main"))
        stack.enter_context(patch("src.shared.constants.set_repo_root"))
        stack.enter_context(patch("src.shared.constants.get_repo_root", return_value=tmp_path))
        stack.enter_context(
            patch("src.workspace.git.check_existing_prs_strict", return_value=existing_prs or [])
        )
        add_files = stack.enter_context(patch("src.workspace.git.add_files"))
        commit = stack.enter_context(patch("src.workspace.git.commit"))
        push = stack.enter_context(patch("src.workspace.git.push"))
        create_pr = stack.enter_context(patch("src.workspace.git.create_draft_pr", return_value=99))
        return (
            add_files,
            commit,
            push,
            create_pr,
            create_worktree,
            delete_remote,
            sync_branch,
            ensure_clone,
        )

    def test_new_skips_commit_when_slug_already_in_csv(self, tmp_path, monkeypatch):
        """When company_add raises NothingToUpdateError (e.g. --pr reattach to
        a branch that already has the slug), bootstrap remains local, preserves
        the attached PR, and restores its existing configuration."""
        _patch_all(monkeypatch, tmp_path)
        # Empty CSV in the original repo. The attached PR worktree populates it
        # after the initial main-branch duplicate check.
        _setup_csvs(tmp_path)

        with ExitStack() as stack:
            (
                add_files,
                commit,
                push,
                create_pr,
                create_worktree,
                delete_remote,
                sync_branch,
                _,
            ) = self._git_mocks(stack, tmp_path, pr_branch="add-company/acme")

            def populate_attached_pr(*_args, **_kwargs):
                _setup_csvs(
                    tmp_path,
                    companies="acme,Acme,https://acme.example,,,\n",
                    boards=("acme,acme-careers,https://acme.example/jobs,greenhouse,{},skip,\n"),
                )
                return {"head": TEST_HEAD_OID, "dev": 1, "ino": 2}

            create_worktree.side_effect = populate_attached_pr

            runner = CliRunner()
            result = runner.invoke(ws, ["new", "acme", "--issue", "1", "--pr", "42"])

        assert result.exit_code == 0, result.output
        # company_add raised NothingToUpdateError, so commit must NOT be called
        add_files.assert_not_called()
        commit.assert_not_called()
        push.assert_not_called()
        create_pr.assert_not_called()
        delete_remote.assert_not_called()
        create_worktree.assert_called_once_with(
            "add-company/acme",
            tmp_path / "worktrees" / "acme",
            start_point=TEST_HEAD_OID,
        )
        sync_branch.assert_not_called()
        # Workspace should be registered
        assert workspace_exists("acme")
        resumed = load_workspace("acme")
        assert resumed.pr == 42
        assert resumed.name == "Acme"
        assert resumed.website == "https://acme.example"
        assert resumed.active_board == "careers"
        assert [(board.alias, board.url) for board in list_boards("acme")] == [
            ("careers", "https://acme.example/jobs")
        ]

    def test_new_keeps_fresh_stub_local_until_submit(self, tmp_path, monkeypatch):
        """Normal bootstrap writes the stub but publishes no commit or PR."""
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)  # empty CSV — slug not present

        with ExitStack() as stack:
            add_files, commit, push, create_pr, _, _, sync_branch, ensure_clone = self._git_mocks(
                stack, tmp_path
            )

            runner = CliRunner()
            result = runner.invoke(ws, ["new", "acme", "--issue", "1"])

        assert result.exit_code == 0, result.output
        add_files.assert_not_called()
        commit.assert_not_called()
        push.assert_not_called()
        create_pr.assert_not_called()
        sync_branch.assert_not_called()
        ensure_clone.assert_called_once_with(reset=False)
        assert "acme" in (tmp_path / "companies.csv").read_text()
        assert load_workspace("acme").pr is None
        assert load_workspace("acme").ats_inventory == {}
        assert list_boards("acme") == []

    def test_new_seeds_valid_inventory_board_without_publishing(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)

        with ExitStack() as stack:
            add_files, commit, push, create_pr, _, _, _, _ = self._git_mocks(
                stack,
                tmp_path,
                issue_body=_inventory_issue_body(),
                issue_labels=("source:ats-inventory",),
            )
            result = CliRunner().invoke(ws, ["new", "acme", "--issue", "1"])

        assert result.exit_code == 0, result.output
        assert "Seeded greenhouse monitor" in result.output
        workspace = load_workspace("acme")
        assert workspace.ats_inventory["source_key"].startswith("ats-scrapers:greenhouse:")
        assert workspace.ats_inventory["status"] == "pending"
        board = load_board("acme", "careers")
        assert board.active_config == "inventory-seed"
        assert board.configs["inventory-seed"]["status"] == "selected"
        assert board.configs["inventory-seed"]["monitor_type"] == "greenhouse"
        add_files.assert_not_called()
        commit.assert_not_called()
        push.assert_not_called()
        create_pr.assert_not_called()

    def test_reconfig_preserves_existing_board_and_adds_inventory_seed(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(
            tmp_path,
            companies="acme,Acme,https://acme.example,,,\n",
            boards=("acme,acme-careers,https://acme.example/jobs,dom,{},dom,{}\n"),
        )

        with ExitStack() as stack:
            self._git_mocks(
                stack,
                tmp_path,
                issue_body=_inventory_issue_body(),
                issue_labels=("source:ats-inventory",),
            )
            result = CliRunner().invoke(ws, ["new", "acme", "--issue", "1", "--reconfig"])

        assert result.exit_code == 0, result.output
        assert "Seeded greenhouse monitor" in result.output
        assert {board.alias for board in list_boards("acme")} == {
            "careers",
            "inventory-careers",
        }
        assert load_board("acme", "careers").url == "https://acme.example/jobs"
        seeded = load_board("acme", "inventory-careers")
        assert seeded.url == "https://boards.greenhouse.io/acme"
        assert seeded.active_config == "inventory-seed"
        workspace = load_workspace("acme")
        assert workspace.active_board == "inventory-careers"
        assert workspace.ats_inventory["board_alias"] == "inventory-careers"
        from src.workspace.workflow import _load_wf_from_disk

        workflow = _load_wf_from_disk("acme")
        assert workflow.current_step == "select_monitor"
        assert workflow.current_board == "inventory-careers"

    def test_reconfig_falls_back_when_inventory_seed_matches_existing_board(
        self, tmp_path, monkeypatch
    ):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(
            tmp_path,
            companies="acme,Acme,https://acme.example,,,\n",
            boards=("acme,acme-careers,https://boards.greenhouse.io/acme,greenhouse,{},dom,{}\n"),
        )

        with ExitStack() as stack:
            self._git_mocks(
                stack,
                tmp_path,
                issue_body=_inventory_issue_body(),
                issue_labels=("source:ats-inventory",),
            )
            result = CliRunner().invoke(ws, ["new", "acme", "--issue", "1", "--reconfig"])

        assert result.exit_code == 0, result.output
        assert "using normal discovery" in result.output
        assert {board.alias for board in list_boards("acme")} == {"careers"}
        workspace = load_workspace("acme")
        assert workspace.active_board == "careers"
        assert workspace.ats_inventory["status"] == "fallback"

    def test_new_ignores_inventory_marker_without_protected_label(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)

        with ExitStack() as stack:
            self._git_mocks(stack, tmp_path, issue_body=_inventory_issue_body())
            result = CliRunner().invoke(ws, ["new", "acme", "--issue", "1"])

        assert result.exit_code == 0, result.output
        assert "Seeded greenhouse monitor" not in result.output
        assert load_workspace("acme").ats_inventory == {}
        assert list_boards("acme") == []

    def test_new_quarantines_seed_that_now_matches_current_registry(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(
            tmp_path,
            companies="existing,Existing,https://existing.example,,,\n",
            boards=(
                "existing,existing-careers,https://boards.greenhouse.io/acme,"
                "greenhouse,{},skip,{}\n"
            ),
        )

        with ExitStack() as stack:
            self._git_mocks(
                stack,
                tmp_path,
                issue_body=_inventory_issue_body(),
                issue_labels=("source:ats-inventory",),
            )
            result = CliRunner().invoke(ws, ["new", "acme", "--issue", "1"])

        assert result.exit_code == 0, result.output
        assert "matches the current registry" in result.output
        workspace = load_workspace("acme")
        assert workspace.ats_inventory["status"] == "fallback"
        assert "existing_ats_tenant" in workspace.ats_inventory["reason"]
        assert list_boards("acme") == []

    def test_new_leaves_linked_draft_byte_for_byte_unchanged(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)

        with ExitStack() as stack:
            (
                add_files,
                commit,
                push,
                create_pr,
                create_worktree,
                delete_remote,
                sync_branch,
                _,
            ) = self._git_mocks(
                stack,
                tmp_path,
                pr_branch="add-company/acme",
                existing_prs=[
                    {
                        "number": 77,
                        "headRefName": "add-company/acme",
                        "isDraft": True,
                    }
                ],
                issue_labels=("company-request",),
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["new", "acme", "--issue", "1"])

        assert result.exit_code != 0
        assert "refusing cross-run branch takeover" in result.output
        add_files.assert_not_called()
        commit.assert_not_called()
        push.assert_not_called()
        create_pr.assert_not_called()
        delete_remote.assert_not_called()
        create_worktree.assert_not_called()
        sync_branch.assert_not_called()
        assert not workspace_exists("acme")

    def test_approved_draft_with_newer_main_is_never_attached_or_merged(
        self, tmp_path, monkeypatch
    ):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)

        with ExitStack() as stack:
            self._git_mocks(
                stack,
                tmp_path,
                existing_prs=[
                    {
                        "number": 77,
                        "headRefName": "add-company/acme",
                        "isDraft": True,
                    }
                ],
                issue_labels=("company-request",),
            )
            details = _test_pr_details(77, slug="acme", issue=1)
            details["comments"] = [
                {
                    "author": {"login": "reviewer"},
                    "createdAt": "2026-08-26T07:13:07Z",
                    "body": (
                        f"Independent exact-head review APPROVED `{TEST_HEAD_OID}`. "
                        "Required CI and CodeQL are green; remains draft behind capacity gate."
                    ),
                }
            ]
            get_details = stack.enter_context(
                patch("src.workspace.git.get_pr_details_strict", return_value=details)
            )
            create_worktree = stack.enter_context(patch("src.workspace.git.create_worktree"))
            merge = stack.enter_context(patch("src.workspace.git.sync_branch_with_main"))
            result = CliRunner().invoke(ws, ["new", "acme", "--issue", "1"])

        assert result.exit_code != 0
        assert "refusing cross-run branch takeover" in result.output
        get_details.assert_not_called()
        create_worktree.assert_not_called()
        merge.assert_not_called()
        assert not workspace_exists("acme")

    def test_scheduled_resolver_cannot_use_explicit_pr_escape_hatch(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)
        monkeypatch.setenv("JOBSEEK_CODEX_RUN_ID", "issue-1-test")

        with ExitStack() as stack:
            self._git_mocks(stack, tmp_path, pr_branch="add-company/acme")
            get_details = stack.enter_context(patch("src.workspace.git.get_pr_details_strict"))
            create_worktree = stack.enter_context(patch("src.workspace.git.create_worktree"))
            result = CliRunner().invoke(
                ws,
                ["new", "acme", "--issue", "1", "--pr", "42"],
            )

        assert result.exit_code != 0
        assert "Scheduled company resolvers cannot attach" in result.output
        get_details.assert_not_called()
        create_worktree.assert_not_called()

    def test_reconfig_defers_pr_until_submit(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="acme,Acme,https://acme.com,,,\n")

        with ExitStack() as stack:
            add_files, commit, push, create_pr, _, _, sync_branch, _ = self._git_mocks(
                stack, tmp_path
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["new", "acme", "--reconfig"])

        assert result.exit_code == 0, result.output
        add_files.assert_not_called()
        commit.assert_not_called()
        push.assert_not_called()
        create_pr.assert_not_called()
        sync_branch.assert_not_called()
        reconfig = load_workspace("acme")
        assert reconfig.pr is None
        assert reconfig.branch == "fix-crawler/acme"

    def test_reconfig_refuses_to_delete_branch_owned_by_another_issue(self, tmp_path, monkeypatch):
        """Two issues that resolve to one company must not replace each other's PR."""
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="acme,Acme,https://acme.com,,,\n")

        with ExitStack() as stack:
            *_, create_worktree, delete_remote, _, _ = self._git_mocks(
                stack,
                tmp_path,
                branch_pr=77,
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["new", "acme", "--issue", "2", "--reconfig"])

        assert result.exit_code != 0
        assert "Branch 'fix-crawler/acme' is owned by open PR #77" in result.output
        assert "refusing to delete an active PR" in result.output
        delete_remote.assert_not_called()
        create_worktree.assert_not_called()
        assert not workspace_exists("acme")

    def test_new_never_reaches_retired_main_sync_for_linked_draft(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path)

        from src.workspace.errors import WorkspaceError

        with ExitStack() as stack:
            *_, sync_branch, _ = self._git_mocks(
                stack,
                tmp_path,
                pr_branch="add-company/acme",
                existing_prs=[
                    {
                        "number": 77,
                        "headRefName": "add-company/acme",
                        "isDraft": True,
                    }
                ],
                issue_labels=("company-request",),
            )
            sync_branch.side_effect = WorkspaceError("must not run")
            remove_worktree = stack.enter_context(
                patch("src.workspace.git.remove_authenticated_worktree")
            )

            runner = CliRunner()
            result = runner.invoke(ws, ["new", "acme", "--issue", "1"])

        assert result.exit_code != 0
        assert "refusing cross-run branch takeover" in result.output
        sync_branch.assert_not_called()
        remove_worktree.assert_not_called()
        assert not workspace_exists("acme")
