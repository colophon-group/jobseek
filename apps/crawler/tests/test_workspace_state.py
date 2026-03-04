"""Tests for workspace state management."""

from __future__ import annotations

import yaml

from src.workspace.state import (
    Board,
    Workspace,
    clear_active_slug,
    delete_workspace,
    get_active_slug,
    list_boards,
    list_workspaces,
    load_board,
    load_workspace,
    resolve_slug,
    resolve_two_args,
    save_board,
    save_workspace,
    set_active_slug,
    update_workspace,
    workspace_exists,
)


class TestWorkspace:
    def test_to_dict_roundtrip(self):
        ws = Workspace(
            slug="stripe",
            created_at="2026-03-03T14:22:00Z",
            branch="add-company/stripe",
            issue=42,
            pr=45,
            name="Stripe",
            website="https://stripe.com",
            active_board="careers",
        )
        d = ws.to_dict()
        ws2 = Workspace.from_dict(d)
        assert ws2.slug == "stripe"
        assert ws2.branch == "add-company/stripe"
        assert ws2.issue == 42
        assert ws2.pr == 45
        assert ws2.name == "Stripe"
        assert ws2.active_board == "careers"

    def test_from_dict_defaults(self):
        ws = Workspace.from_dict({"slug": "test"})
        assert ws.slug == "test"
        assert ws.branch == ""
        assert ws.issue is None
        assert ws.pr is None
        assert ws.submitted is False

    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        ws = Workspace(slug="test", name="Test Co")
        save_workspace(ws)
        loaded = load_workspace("test")
        assert loaded.slug == "test"
        assert loaded.name == "Test Co"

    def test_workspace_exists(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        assert not workspace_exists("test")
        save_workspace(Workspace(slug="test"))
        assert workspace_exists("test")

    def test_delete_workspace(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="test"))
        assert workspace_exists("test")
        delete_workspace("test")
        assert not workspace_exists("test")

    def test_list_workspaces(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="alpha"))
        save_workspace(Workspace(slug="beta"))
        wss = list_workspaces()
        assert len(wss) == 2
        assert wss[0].slug == "alpha"
        assert wss[1].slug == "beta"

    def test_submit_state_tracking(self):
        ws = Workspace(slug="test")
        assert ws.submitted is False
        ws.submit_state["csv_written"] = True
        ws.submit_state["validated"] = True
        ws.submit_state["committed"] = True
        assert ws.submitted is False  # pushed still missing
        ws.submit_state["pushed"] = True
        assert ws.submitted is True
        d = ws.to_dict()
        ws2 = Workspace.from_dict(d)
        assert ws2.submitted is True

    def test_v1_migration(self):
        """v1 workspace YAML (with progress dict) loads correctly."""
        v1_data = {
            "version": 1,
            "slug": "test",
            "created_at": "2026-03-03T14:22:00Z",
            "git": {"branch": "add-company/test", "issue": 42, "pr": 7},
            "company": {"name": "Test", "website": "https://test.com", "logo_url": "", "icon_url": ""},
            "active_board": "careers",
            "progress": {
                "board_added": True,
                "monitor_selected": True,
                "monitor_tested": True,
                "scraper_selected": False,
                "scraper_tested": False,
                "submitted": False,
            },
        }
        ws = Workspace.from_dict(v1_data)
        assert ws.slug == "test"
        assert ws.branch == "add-company/test"
        assert ws.issue == 42
        assert ws.pr == 7
        assert ws.name == "Test"
        # v1 progress is discarded — submitted is derived from submit_state
        assert ws.submitted is False

    def test_to_dict_version_2(self):
        ws = Workspace(slug="test")
        d = ws.to_dict()
        assert d["version"] == 2
        assert "progress" not in d
        assert "submit_state" in d


class TestActiveWorkspace:
    def test_no_active_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        assert get_active_slug() is None

    def test_set_and_get(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="stripe"))
        set_active_slug("stripe")
        assert get_active_slug() == "stripe"

    def test_clear(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="stripe"))
        set_active_slug("stripe")
        clear_active_slug()
        assert get_active_slug() is None

    def test_returns_none_for_deleted_workspace(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="stripe"))
        set_active_slug("stripe")
        delete_workspace("stripe")
        assert get_active_slug() is None

    def test_resolve_slug_explicit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        assert resolve_slug("stripe") == "stripe"

    def test_resolve_slug_from_active(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="stripe"))
        set_active_slug("stripe")
        assert resolve_slug(None) == "stripe"

    def test_resolve_slug_no_active_dies(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        import pytest

        with pytest.raises(SystemExit):
            resolve_slug(None)

    def test_resolve_two_args_both(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        slug, val = resolve_two_args("stripe", "greenhouse")
        assert slug == "stripe"
        assert val == "greenhouse"

    def test_resolve_two_args_one_with_active(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="stripe"))
        set_active_slug("stripe")
        slug, val = resolve_two_args("greenhouse", None)
        assert slug == "stripe"
        assert val == "greenhouse"


class TestBoard:
    def test_to_dict_roundtrip(self):
        board = Board(alias="careers", slug="stripe-careers", url="https://boards.greenhouse.io/stripe")
        board.monitor_type = "greenhouse"
        board.monitor_config = {"token": "stripe"}
        d = board.to_dict()
        b2 = Board.from_dict(d)
        assert b2.alias == "careers"
        assert b2.slug == "stripe-careers"
        assert b2.monitor_type == "greenhouse"
        assert b2.monitor_config == {"token": "stripe"}

    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        save_board("test", board)
        loaded = load_board("test", "careers")
        assert loaded.alias == "careers"
        assert loaded.url == "https://test.com/jobs"

    def test_list_boards(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_board("test", Board(alias="a", slug="test-a", url="https://a.com"))
        save_board("test", Board(alias="b", slug="test-b", url="https://b.com"))
        boards = list_boards("test")
        assert len(boards) == 2
        assert boards[0].alias == "a"
        assert boards[1].alias == "b"

    def test_board_with_run_data(self):
        board = Board(alias="careers", slug="test-careers", url="https://test.com")
        board.monitor_type = "greenhouse"
        board.monitor_run = {"jobs": 50, "time": 2.1}
        board.scraper_run = {"count": 3, "avg_time": 1.0}
        d = board.to_dict()
        b2 = Board.from_dict(d)
        assert b2.monitor_run["jobs"] == 50
        assert b2.scraper_run["avg_time"] == 1.0

    def test_yaml_format_v2(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        board = Board(alias="careers", slug="test-careers", url="https://test.com/jobs")
        board.monitor_type = "greenhouse"
        board.monitor_config = {"token": "test"}
        save_board("test", board)
        yaml_text = (tmp_path / "test" / "boards" / "careers.yaml").read_text()
        data = yaml.safe_load(yaml_text)
        assert "configs" in data
        assert data["active_config"] == "greenhouse"
        assert data["configs"]["greenhouse"]["monitor_type"] == "greenhouse"
        assert data["configs"]["greenhouse"]["monitor_config"]["token"] == "test"

    def test_v1_migration(self):
        """v1 board YAML (with monitor/scraper dicts) loads correctly."""
        v1_data = {
            "alias": "careers",
            "slug": "stripe-careers",
            "url": "https://boards.greenhouse.io/stripe",
            "monitor": {"type": "greenhouse", "config": {"token": "stripe"}},
            "scraper": {"type": None, "config": {}},
            "monitor_run": {"jobs": 138, "has_rich_data": True, "sample_urls": ["https://a.com"]},
        }
        board = Board.from_dict(v1_data)
        assert board.alias == "careers"
        assert board.slug == "stripe-careers"
        assert board.monitor_type == "greenhouse"
        assert board.monitor_config == {"token": "stripe"}
        assert board.monitor_run["jobs"] == 138
        assert board.active_config == "greenhouse"
        cfg = board.configs["greenhouse"]
        assert cfg["status"] == "tested"
        assert cfg["rich"] is True

    def test_v1_migration_no_monitor(self):
        """v1 board with no monitor type creates no configs."""
        v1_data = {
            "alias": "careers",
            "slug": "test-careers",
            "url": "https://test.com/jobs",
            "monitor": {"type": None, "config": {}},
            "scraper": {"type": None, "config": {}},
        }
        board = Board.from_dict(v1_data)
        assert board.active_config is None
        assert board.configs == {}
        assert board.monitor_type is None

    def test_v1_migration_with_scraper_run(self):
        """v1 board with scraper_run data migrates correctly."""
        v1_data = {
            "alias": "careers",
            "slug": "test-careers",
            "url": "https://test.com",
            "monitor": {"type": "sitemap", "config": {}},
            "scraper": {"type": "json-ld", "config": {}},
            "monitor_run": {"jobs": 50},
            "scraper_run": {"count": 3, "avg_time": 1.0},
        }
        board = Board.from_dict(v1_data)
        assert board.monitor_type == "sitemap"
        assert board.scraper_type == "json-ld"
        assert board.monitor_run["jobs"] == 50
        assert board.scraper_run["avg_time"] == 1.0

    def test_property_setters(self):
        """Property setters create config entries correctly."""
        board = Board(alias="careers", slug="test-careers", url="https://test.com")
        assert board.monitor_type is None
        assert board.active_config is None

        board.monitor_type = "greenhouse"
        assert board.active_config == "greenhouse"
        assert board.monitor_type == "greenhouse"

        board.monitor_config = {"token": "test"}
        assert board.monitor_config == {"token": "test"}

        board.scraper_type = "json-ld"
        assert board.scraper_type == "json-ld"

        board.monitor_run = {"jobs": 50}
        assert board.monitor_run["jobs"] == 50

        # Mutation after assignment works
        board.monitor_run["quality"] = {"title": 50}
        assert board.monitor_run["quality"] == {"title": 50}

    def test_ready_property(self):
        board = Board(alias="careers", slug="test-careers", url="https://test.com")
        assert board.ready is False

        board.monitor_type = "greenhouse"
        assert board.ready is False

        board.configs["greenhouse"]["status"] = "tested"
        assert board.ready is False  # No feedback yet

        board.configs["greenhouse"]["feedback"] = {"verdict": "good"}
        assert board.ready is True

    def test_ready_acceptable(self):
        board = Board(alias="careers", slug="test-careers", url="https://test.com")
        board.monitor_type = "greenhouse"
        board.configs["greenhouse"]["status"] = "tested"
        board.configs["greenhouse"]["feedback"] = {"verdict": "acceptable"}
        assert board.ready is True

    def test_ready_poor_verdict(self):
        board = Board(alias="careers", slug="test-careers", url="https://test.com")
        board.monitor_type = "greenhouse"
        board.configs["greenhouse"]["status"] = "tested"
        board.configs["greenhouse"]["feedback"] = {"verdict": "poor"}
        assert board.ready is False

    def test_atomic_write(self, tmp_path, monkeypatch):
        """save_board uses atomic write (file exists after save)."""
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        board = Board(alias="careers", slug="test-careers", url="https://test.com")
        save_board("test", board)
        path = tmp_path / "test" / "boards" / "careers.yaml"
        assert path.exists()
        # No .tmp files should remain
        assert not list(path.parent.glob("*.tmp"))


class TestUpdateWorkspace:
    def test_read_modify_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="test", name="Before"))
        with update_workspace("test") as ws:
            ws.name = "After"
        loaded = load_workspace("test")
        assert loaded.name == "After"

    def test_changes_persisted_on_exit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="test", active_board="old"))
        with update_workspace("test") as ws:
            ws.active_board = "new"
        loaded = load_workspace("test")
        assert loaded.active_board == "new"

    def test_exception_inside_does_not_save(self, tmp_path, monkeypatch):
        """If the body raises, changes are not persisted."""
        import pytest

        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="test", name="Original"))
        with pytest.raises(ValueError, match="boom"):
            with update_workspace("test") as ws:
                ws.name = "Changed"
                raise ValueError("boom")
        loaded = load_workspace("test")
        assert loaded.name == "Original"


class TestFileLocking:
    def test_save_board_creates_lock_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        board = Board(alias="careers", slug="test-careers", url="https://test.com")
        save_board("test", board)
        lock = tmp_path / "test" / "boards" / "careers.yaml.lock"
        assert lock.exists()

    def test_save_workspace_creates_lock_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        save_workspace(Workspace(slug="test"))
        lock = tmp_path / "test" / "workspace.yaml.lock"
        assert lock.exists()
