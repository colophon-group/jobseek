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
        assert ws.progress["board_added"] is False

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

    def test_progress_tracking(self):
        ws = Workspace(slug="test")
        assert ws.progress["board_added"] is False
        ws.progress["board_added"] = True
        d = ws.to_dict()
        ws2 = Workspace.from_dict(d)
        assert ws2.progress["board_added"] is True


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
        board = Board(
            alias="careers",
            slug="stripe-careers",
            url="https://boards.greenhouse.io/stripe",
            monitor_type="greenhouse",
            monitor_config={"token": "stripe"},
        )
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
        board = Board(
            alias="careers",
            slug="test-careers",
            url="https://test.com",
            monitor_run={"jobs": 50, "time": 2.1},
            scraper_run={"count": 3, "avg_time": 1.0},
        )
        d = board.to_dict()
        b2 = Board.from_dict(d)
        assert b2.monitor_run["jobs"] == 50
        assert b2.scraper_run["avg_time"] == 1.0

    def test_yaml_format(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.workspace.state.WORKSPACE_DIR", tmp_path)
        board = Board(
            alias="careers",
            slug="test-careers",
            url="https://test.com/jobs",
            monitor_type="greenhouse",
            monitor_config={"token": "test"},
        )
        save_board("test", board)
        yaml_text = (tmp_path / "test" / "boards" / "careers.yaml").read_text()
        data = yaml.safe_load(yaml_text)
        assert data["monitor"]["type"] == "greenhouse"
        assert data["monitor"]["config"]["token"] == "test"
