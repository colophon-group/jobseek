"""Tests for workspace CLI commands.

Uses Click's CliRunner for command invocation testing.
Mocks git/gh operations since these tests run without a real repo.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from src.workspace.cli import ws
from src.workspace.state import (
    Board,
    Workspace,
    get_active_slug,
    load_workspace,
    save_board,
    save_workspace,
    set_active_slug,
    workspace_exists,
)

COMPANIES_HEADER = "slug,name,website,logo_url,icon_url\n"
BOARDS_HEADER = "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"


def _setup_csvs(tmp_path, companies="", boards=""):
    (tmp_path / "companies.csv").write_text(COMPANIES_HEADER + companies)
    (tmp_path / "boards.csv").write_text(BOARDS_HEADER + boards)


def _patch_all(monkeypatch, tmp_path):
    """Patch DATA_DIR and WORKSPACE_DIR for testing."""
    monkeypatch.setattr("src.shared.constants.DATA_DIR", tmp_path)
    monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path / ".ws")
    monkeypatch.setattr("src.csvtool.DATA_DIR", tmp_path)
    monkeypatch.setattr("src.inspect.DATA_DIR", tmp_path)


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
        assert "No workspaces found" in result.output

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

    def test_list_workspaces(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="alpha"))
        save_workspace(Workspace(slug="beta"))
        runner = CliRunner()
        # No active workspace — shows list
        result = runner.invoke(ws, ["status"])
        assert "alpha" in result.output
        assert "beta" in result.output


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


class TestAddBoard:
    def test_add_board(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        runner = CliRunner()
        result = runner.invoke(ws, ["add", "board", "test", "careers", "--url", "https://test.com/jobs"])
        assert result.exit_code == 0
        assert "test-careers" in result.output

        ws_obj = load_workspace("test")
        assert ws_obj.active_board == "careers"
        assert ws_obj.progress["board_added"] is True

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
        result = runner.invoke(ws, ["add", "board", "test", "test-careers", "--url", "https://test.com/jobs"])
        assert result.exit_code == 0
        assert "already prefixed" in result.output


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

        with patch("src.workspace.git.comment_on_issue") as mock_comment, \
             patch("src.workspace.git.close_issue") as mock_close:
            result = runner.invoke(ws, [
                "reject", "--issue", "42",
                "--reason", "no-job-board",
                "--message", "No careers page found",
            ])
            assert result.exit_code == 0
            mock_comment.assert_called_once()
            mock_close.assert_called_once_with(42)

    def test_reject_from_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", issue=42))
        runner = CliRunner()

        with patch("src.workspace.git.comment_on_issue"), \
             patch("src.workspace.git.close_issue") as mock_close:
            result = runner.invoke(ws, [
                "reject", "test",
                "--reason", "no-open-positions",
                "--message", "Zero listings visible",
            ])
            assert result.exit_code == 0
            mock_close.assert_called_once_with(42)

    def test_reject_from_active_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test", issue=42))
        set_active_slug("test")
        runner = CliRunner()

        with patch("src.workspace.git.comment_on_issue"), \
             patch("src.workspace.git.close_issue") as mock_close:
            result = runner.invoke(ws, [
                "reject",
                "--reason", "no-open-positions",
                "--message", "Zero listings visible",
            ])
            assert result.exit_code == 0
            mock_close.assert_called_once_with(42)


class TestDel:
    def test_del_workspace(self, tmp_path, monkeypatch):
        _patch_all(monkeypatch, tmp_path)
        _setup_csvs(tmp_path, companies="test,Test,,,\n")
        save_workspace(Workspace(slug="test", branch="add-company/test", pr=10))
        runner = CliRunner()

        with patch("src.workspace.git.close_pr") as mock_close_pr, \
             patch("src.workspace.git.delete_branch") as mock_del_branch:
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

        with patch("src.workspace.git.close_pr"), \
             patch("src.workspace.git.delete_branch"):
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
        assert "Unknown monitor type" in result.output or "Unknown monitor type" in (result.stderr_bytes or b"").decode()

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
        assert "Unknown scraper type" in result.output or "Unknown scraper type" in (result.stderr_bytes or b"").decode()

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
    stack.enter_context(patch("src.workspace.artifacts.monitor_run_dir", return_value=tmp_path / "artifacts"))
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
    stack.enter_context(patch("src.workspace.artifacts.scraper_run_dir", return_value=tmp_path / "artifacts"))
    stack.enter_context(patch("src.workspace.artifacts.save_results"))
    stack.enter_context(patch("src.workspace.artifacts.save_quality"))
    stack.enter_context(patch("src.workspace.artifacts.save_http_log"))
    stack.enter_context(patch("src.workspace.artifacts.save_events"))
    stack.enter_context(patch("src.workspace.artifacts.capture_structlog", return_value=[]))
    stack.enter_context(patch("random.sample", return_value=["https://test.com/jobs/1", "https://test.com/jobs/2"]))
    return stack, mock_asyncio


class TestRunMonitorOutput:
    def _setup_monitor_board(self, tmp_path, monkeypatch, monitor_type="sitemap"):
        _patch_all(monkeypatch, tmp_path)
        save_workspace(Workspace(slug="test"))
        save_board(
            "test",
            Board(alias="careers", slug="test-careers", url="https://test.com/jobs",
                  monitor_type=monitor_type),
        )
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
                url="https://test.com/jobs/1", title="Engineer",
                description="<p>Build</p>", locations=["NYC"],
                employment_type="FULL_TIME", date_posted="2026-01-01",
            ),
            "https://test.com/jobs/2": DiscoveredJob(
                url="https://test.com/jobs/2", title="Designer",
                description="<p>Design</p>", locations=["SF"],
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
        save_board(
            "test",
            Board(
                alias="careers", slug="test-careers", url="https://test.com/jobs",
                monitor_type="sitemap", scraper_type=scraper_type,
                monitor_run={"sample_urls": ["https://test.com/jobs/1", "https://test.com/jobs/2"]},
            ),
        )
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

    def test_all_titles_suggests_submit(self, tmp_path, monkeypatch):
        """When all titles extracted, suggest ws submit."""
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

        assert "ws submit" in result.output
        assert "No titles" not in result.output

    def test_optional_fields_shown(self, tmp_path, monkeypatch):
        """Optional fields with data should be shown in output."""
        self._setup_board_with_monitor(tmp_path, monkeypatch)

        from src.core.scrapers import JobContent

        contents = [
            JobContent(
                title="Engineer", description="<p>Hi</p>", locations=["NYC"],
                employment_type="FULL_TIME", date_posted="2026-01-01",
                skills=["Python", "SQL"],
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
