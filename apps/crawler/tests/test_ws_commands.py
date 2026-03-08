"""Tests for workspace CLI commands.

Uses Click's CliRunner for command invocation testing.
Mocks git/gh operations since these tests run without a real repo.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from src.workspace.cli import ws
from src.workspace.errors import WorkspaceStateError
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

COMPANIES_HEADER = "slug,name,website,logo_url,icon_url\n"
BOARDS_HEADER = (
    "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
)


def _setup_csvs(tmp_path, companies="", boards=""):
    (tmp_path / "companies.csv").write_text(COMPANIES_HEADER + companies)
    (tmp_path / "boards.csv").write_text(BOARDS_HEADER + boards)


def _patch_all(monkeypatch, tmp_path):
    """Patch path getters for testing."""
    ws_dir = tmp_path / ".ws"
    _data = lambda: tmp_path  # noqa: E731
    _ws = lambda: ws_dir  # noqa: E731
    monkeypatch.setattr("src.shared.constants.get_data_dir", _data)
    monkeypatch.setattr("src.shared.constants.get_workspace_dir", _ws)
    monkeypatch.setattr("src.csvtool.get_data_dir", _data)
    monkeypatch.setattr("src.inspect.get_data_dir", _data)
    monkeypatch.setattr("src.workspace.commands.lifecycle.get_data_dir", _data)
    monkeypatch.setattr("src.workspace.state.get_workspace_dir", _ws)

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


class TestDelBoard:
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
            patch("src.workspace.git.comment_on_issue") as mock_comment,
            patch("src.workspace.git.close_issue") as mock_close,
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
            mock_comment.assert_called_once()
            mock_close.assert_called_once_with(42)

    def test_reject_from_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", issue=42))
        runner = CliRunner()

        with (
            patch("src.workspace.git.comment_on_issue"),
            patch("src.workspace.git.close_issue") as mock_close,
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
            mock_close.assert_called_once_with(42)

    def test_reject_from_active_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", issue=42))
        set_active_slug("test")
        runner = CliRunner()

        with (
            patch("src.workspace.git.comment_on_issue"),
            patch("src.workspace.git.close_issue") as mock_close,
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
            mock_close.assert_called_once_with(42)

    def test_reject_explicit_issue_not_overridden_by_active_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="active-ws", issue=38))
        set_active_slug("active-ws")
        runner = CliRunner()

        with (
            patch("src.workspace.git.comment_on_issue"),
            patch("src.workspace.git.close_issue") as mock_close,
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
            mock_close.assert_called_once_with(39)

    def test_reject_slug_issue_mismatch_fails_fast(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", issue=38))
        runner = CliRunner()

        with (
            patch("src.workspace.git.comment_on_issue") as mock_comment,
            patch("src.workspace.git.close_issue") as mock_close,
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


class TestTaskIssueBinding:
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
        assert "Step 1/7" in result.output

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


class TestDel:
    def test_del_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="test,Test,,,\n")
        save_workspace(Workspace(slug="test", branch="add-company/test", pr=10))
        runner = CliRunner()

        with (
            patch("src.workspace.git.close_pr") as mock_close_pr,
            patch("src.workspace.git.delete_branch") as mock_del_branch,
        ):
            result = runner.invoke(ws, ["del", "test"])
            assert result.exit_code == 0
            mock_close_pr.assert_called_once_with(10)
            mock_del_branch.assert_called_once()

        assert not workspace_exists("test")

    def test_del_clears_active(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="test,Test,,,\n")
        save_workspace(Workspace(slug="test", branch="add-company/test", pr=10))
        set_active_slug("test")
        runner = CliRunner()

        with patch("src.workspace.git.close_pr"), patch("src.workspace.git.delete_branch"):
            result = runner.invoke(ws, ["del", "test"])
            assert result.exit_code == 0

        assert get_active_slug() is None


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


def _enter_monitor_patches(tmp_path) -> tuple[ExitStack, MagicMock]:
    """Enter common patches for run monitor tests. Returns (stack, mock_asyncio)."""
    stack = ExitStack()
    mock_asyncio = stack.enter_context(patch("src.workspace.commands.crawl.asyncio"))
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
    return stack, mock_asyncio


def _enter_scraper_patches(tmp_path) -> tuple[ExitStack, MagicMock]:
    """Enter common patches for run scraper tests. Returns (stack, mock_asyncio)."""
    stack = ExitStack()
    mock_asyncio = stack.enter_context(patch("src.workspace.commands.crawl.asyncio"))
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
    return stack, mock_asyncio


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
            mock_asyncio.run.return_value = (fake_result, 1.5, [])
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
            mock_asyncio.run.return_value = (fake_result, 2.0, [])
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor", "test"])

        assert "\u2713" in result.output  # Checkmark symbol
        assert "2 jobs" in result.output

    def test_nextdata_suggests_nextdata_scraper(self, tmp_path, monkeypatch):
        """nextdata monitor should suggest nextdata scraper, not json-ld."""
        self._setup_monitor_board(tmp_path, monkeypatch, monitor_type="nextdata")

        @dataclass
        class FakeResult:
            urls: set[str]
            jobs_by_url: dict | None
            filtered_count: int = 0

        fake_result = FakeResult(
            urls={"https://test.com/jobs/1"},
            jobs_by_url=None,
        )

        stack, mock_asyncio = _enter_monitor_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = (fake_result, 1.0, [])
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor", "test"])

        assert "ws select scraper nextdata" in result.output

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
            mock_asyncio.run.return_value = (fake_result, 1.0, [])
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
            mock_asyncio.run.return_value = (
                [
                    ("https://test.com/jobs/1", contents[0], 0.5),
                    ("https://test.com/jobs/2", contents[1], 0.3),
                ],
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
            mock_asyncio.run.return_value = (
                [
                    ("https://test.com/jobs/1", contents[0], 0.5),
                    ("https://test.com/jobs/2", contents[1], 0.3),
                ],
                [],
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "scraper", "test"])

        assert "No titles extracted" in result.output
        assert "ws select scraper dom" in result.output

    def test_all_titles_suggests_feedback(self, tmp_path, monkeypatch):
        """When all titles extracted, suggest ws feedback."""
        self._setup_board_with_monitor(tmp_path, monkeypatch)

        from src.core.scrapers import JobContent

        contents = [
            JobContent(title="Engineer", description="<p>Hi</p>", locations=["NYC"]),
            JobContent(title="Designer", description="<p>Hi</p>", locations=["SF"]),
        ]

        stack, mock_asyncio = _enter_scraper_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = (
                [
                    ("https://test.com/jobs/1", contents[0], 0.5),
                    ("https://test.com/jobs/2", contents[1], 0.3),
                ],
                [],
            )
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "scraper", "test"])

        assert "ws feedback" in result.output
        assert "No titles" not in result.output

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
            mock_asyncio.run.return_value = (
                [
                    ("https://test.com/jobs/1", contents[0], 0.5),
                    ("https://test.com/jobs/2", contents[1], 0.3),
                ],
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
            mock_asyncio.run.return_value = (
                [
                    ("https://test.com/jobs/1", contents[0], 0.5),
                    ("https://test.com/jobs/2", contents[1], 0.3),
                ],
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
            mock_asyncio.run.return_value = (
                [("https://test.com/jobs/1", contents[0], 0.5)],
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
            mock_asyncio.run.return_value = (fake_result, 2.0, [])
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
            mock_asyncio.run.return_value = (fake_result, 1.0, [])
            runner = CliRunner()
            result = runner.invoke(ws, ["run", "monitor", "test"])

        assert "Verify: compare this count" not in result.output


def _enter_probe_scraper_patches(tmp_path) -> tuple[ExitStack, MagicMock]:
    """Enter common patches for probe scraper tests. Returns (stack, mock_asyncio)."""
    stack = ExitStack()
    mock_asyncio = stack.enter_context(patch("src.workspace.commands.crawl.asyncio"))
    stack.enter_context(
        patch(
            "src.workspace.artifacts.scraper_probe_run_dir",
            return_value=tmp_path / "artifacts",
        )
    )
    stack.enter_context(patch("src.workspace.artifacts.save_probe"))
    return stack, mock_asyncio


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

    def test_next_step_suppressed_on_zero_titles(self, tmp_path, monkeypatch):
        """0/N titles should suppress Next: and show warning."""
        self._setup_board_with_monitor(tmp_path, monkeypatch)

        # Probe results: best scraper has 0 titles
        fake_results = [
            (
                "json-ld",
                {
                    "config": {},
                    "total": 1,
                    "titles": 0,
                    "descriptions": 1,
                    "locations": 0,
                    "fields": {"description": 1},
                },
                "0/1 titles, 1/1 desc, 0/1 locations",
            ),
            ("nextdata", None, "Not detected"),
            ("dom", None, "Not detected"),
            ("api_sniffer", None, "Skipped \u2014 Playwright not available"),
        ]

        stack, mock_asyncio = _enter_probe_scraper_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = (fake_results, False)
            runner = CliRunner()
            result = runner.invoke(ws, ["probe", "scraper", "test"])

        assert "Next:" not in result.output
        assert "0/N titles" in result.output or "heuristic config is wrong" in result.output

    def test_next_step_shown_when_fields_ok(self, tmp_path, monkeypatch):
        """When required fields are populated, Next: should be shown."""
        self._setup_board_with_monitor(tmp_path, monkeypatch)

        fake_results = [
            (
                "json-ld",
                {
                    "config": {},
                    "total": 1,
                    "titles": 1,
                    "descriptions": 1,
                    "locations": 1,
                    "fields": {"title": 1, "description": 1, "locations": 1},
                },
                "1/1 titles, 1/1 desc, 1/1 locations",
            ),
            ("nextdata", None, "Not detected"),
            ("dom", None, "Not detected"),
            ("api_sniffer", None, "Skipped \u2014 Playwright not available"),
        ]

        stack, mock_asyncio = _enter_probe_scraper_patches(tmp_path)
        with stack:
            mock_asyncio.run.return_value = (fake_results, False)
            runner = CliRunner()
            result = runner.invoke(ws, ["probe", "scraper", "test"])

        assert "Next:" in result.output
        assert "ws select scraper json-ld" in result.output

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
            mock_asyncio.run.return_value = (fake_results, True)
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

    return ws_obj, board


class TestSubmitStepRegistry:
    """Test the submit step registry and checkpoint logic."""

    def test_submit_step_list_exists(self):
        from src.workspace.commands.lifecycle import SUBMIT_STEPS

        assert len(SUBMIT_STEPS) == 9
        keys = [k for k, _, _ in SUBMIT_STEPS]
        assert "csv_written" in keys
        assert "pushed" in keys
        assert "pr_ready" in keys
        assert "issue_completed" in keys

    def test_critical_steps_are_first(self):
        from src.workspace.commands.lifecycle import SUBMIT_STEPS

        critical_idx = [i for i, (_, _, c) in enumerate(SUBMIT_STEPS) if c]
        non_critical_idx = [i for i, (_, _, c) in enumerate(SUBMIT_STEPS) if not c]
        assert max(critical_idx) < min(non_critical_idx)


class TestSubmitIdempotency:
    """Submit skips already-completed steps on rerun."""

    def test_skips_completed_steps(self, tmp_path, monkeypatch):
        ws_obj, board = _setup_submittable_workspace(tmp_path, monkeypatch)

        # Mark some steps as done
        ws_obj.submit_state = {
            "_active_configs": {"careers": "greenhouse"},
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


class TestSubmitForce:
    """Test --force flag with poor quality."""

    def test_poor_verdict_blocks_without_force(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
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

        with ExitStack() as stack:
            stack.enter_context(patch("src.workspace.git._run"))
            runner = CliRunner()
            result = runner.invoke(ws, ["submit", "test", "--force"])

        assert result.exit_code == 0
        assert "forced" in result.output


class TestSubmitLastError:
    """Submit stores last_error on critical failure."""

    def test_stores_last_error_on_critical_failure(self, tmp_path, monkeypatch):
        ws_obj, board = _setup_submittable_workspace(tmp_path, monkeypatch)

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


class TestBuildPrBody:
    """Test PR body generation."""

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


class TestPreflight:
    """Test preflight checks."""

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

        ws_obj = Workspace(slug="test", branch="add-company/test")
        issues = run_preflight(ws_obj)
        assert not issues

    def test_preflight_no_branch_check_when_disabled(self, tmp_path, monkeypatch):
        from src.workspace.preflight import run_preflight

        _patch_all(monkeypatch, tmp_path)

        ws_obj = Workspace(slug="test", branch="add-company/test")
        issues = run_preflight(ws_obj, check_branch=False)
        assert not issues


class TestResume:
    """Test ws resume command."""

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
        assert "ws submit" in result.output

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
        assert "ws probe monitor" in result.output

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
        assert "ws add board" in result.output


class TestNextStepLogic:
    """Test next step suggestion priority."""

    def test_next_steps_priority_order(self):
        from src.workspace.commands.lifecycle import _NEXT_STEPS

        codes = [c for c, _ in _NEXT_STEPS]
        # None sentinel (meaning "all good") should be last
        assert codes[-1] is None
        # branch issues should be first
        assert codes[0] == "branch_missing"

    def test_no_issues_suggests_submit(self):
        from src.workspace.commands.lifecycle import _NEXT_STEPS

        issue_codes: set[str] = set()
        for code, suggestion in _NEXT_STEPS:
            if code is None or code in issue_codes:
                assert "ws submit" in suggestion
                break


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
            stack.enter_context(
                patch(
                    "src.workspace.commands.crawl.asyncio.run",
                    return_value=(fake, 1.0, []),
                )
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
            stack.enter_context(
                patch(
                    "src.workspace.commands.crawl.asyncio.run",
                    return_value=(fake, 1.0, []),
                )
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
