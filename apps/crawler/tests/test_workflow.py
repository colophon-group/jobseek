"""Tests for the task-driven workflow engine."""

from __future__ import annotations

import pytest

from src.workspace.state import Board, Workspace, save_board, save_workspace
from src.workspace.workflow import (
    StepDef,
    WorkflowState,
    _all_step_defs,
    _load_wf_from_disk,
    _save_wf_to_disk,
    advance,
    build_context,
    check_gate,
    search_kb,
    should_skip,
)


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Create a temporary workspace directory and return the slug."""
    slug = "test-wf"
    ws_root = tmp_path / ".workspace"
    monkeypatch.setattr("src.shared.constants.get_workspace_dir", lambda: ws_root)
    monkeypatch.setattr("src.workspace.state.get_workspace_dir", lambda: ws_root)

    ws = Workspace(
        slug=slug,
        created_at="2026-01-01T00:00:00Z",
        branch="add-company/test-wf",
        issue=42,
        pr=100,
        name="Test Corp",
        website="https://test.com",
        logo_url="https://test.com/logo.png",
        icon_url="https://test.com/icon.png",
    )
    save_workspace(ws)

    # Set up active workspace
    active_path = ws_root / "active"
    active_path.write_text(slug)

    return slug, ws, ws_root


@pytest.fixture()
def board_careers(workspace):
    """Add a board with detections and tested config."""
    slug, ws, ws_root = workspace
    board = Board(
        alias="careers",
        slug="test-wf-careers",
        url="https://boards.greenhouse.io/test",
        active_config="greenhouse",
        detections={"greenhouse": {"token": "test"}},
        configs={
            "greenhouse": {
                "monitor_type": "greenhouse",
                "monitor_config": {"token": "test"},
                "status": "tested",
                "rich": True,
                "run": {"jobs": 50, "time": 2.0, "has_rich_data": True},
                "scraper_run": {},
                "feedback": {"verdict": "good", "verdict_notes": "All clean"},
            }
        },
    )
    save_board(slug, board)
    return board


@pytest.fixture()
def board_second(workspace):
    """Add a second board with detections."""
    slug, ws, ws_root = workspace
    board = Board(
        alias="careers-de",
        slug="test-wf-careers-de",
        url="https://test.com/de/careers",
        detections={"dom": {}},
        configs={},
    )
    save_board(slug, board)
    return board


class TestStepDefs:
    def test_all_steps_loaded(self):
        steps = _all_step_defs()
        assert len(steps) == 7
        assert steps[0].id == "setup"
        assert steps[-1].id == "reflect"

    def test_phases(self):
        steps = _all_step_defs()
        phases = [s.phase for s in steps]
        assert phases == [
            "global",
            "global",
            "per_board",
            "per_board",
            "per_board",
            "final",
            "final",
        ]

    def test_skip_when(self):
        steps = _all_step_defs()
        scraper_step = next(s for s in steps if s.id == "select_scraper")
        assert scraper_step.skip_when == "rich_monitor"


class TestWorkflowState:
    def test_default(self):
        wf = WorkflowState()
        assert wf.current_step == "setup"
        assert wf.current_board is None
        assert wf.completed_boards == []
        assert wf.reflections == []
        assert not wf.failed

    def test_roundtrip(self):
        wf = WorkflowState(
            current_step="select_monitor",
            current_board="careers",
            completed_boards=["careers-us"],
            reflections=[{"step": "validate", "notes": "ok"}],
        )
        d = wf.to_dict()
        wf2 = WorkflowState.from_dict(d)
        assert wf2.current_step == "select_monitor"
        assert wf2.current_board == "careers"
        assert wf2.completed_boards == ["careers-us"]
        assert wf2.reflections == [{"step": "validate", "notes": "ok"}]

    def test_persistence(self, workspace):
        slug, ws, ws_root = workspace
        wf = WorkflowState(current_step="setup")
        _save_wf_to_disk(slug, wf)
        loaded = _load_wf_from_disk(slug)
        assert loaded.current_step == "setup"


class TestGates:
    def test_company_complete(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        boards = [board_careers]
        step = StepDef(
            id="setup",
            title="",
            instructions="",
            gate_type="state",
            gate_check="company_complete",
            phase="global",
        )
        passed, _ = check_gate(step, ws, boards)
        assert passed

    def test_company_incomplete(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        ws.name = ""
        save_workspace(ws)
        from src.workspace.state import load_workspace

        ws = load_workspace(slug)
        boards = [board_careers]
        step = StepDef(
            id="setup",
            title="",
            instructions="",
            gate_type="state",
            gate_check="company_complete",
            phase="global",
        )
        passed, reason = check_gate(step, ws, boards)
        assert not passed

    def test_all_boards_added(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        boards = [board_careers]
        step = StepDef(
            id="add_boards",
            title="",
            instructions="",
            gate_type="state",
            gate_check="all_boards_added",
            phase="global",
        )
        passed, _ = check_gate(step, ws, boards)
        assert passed

    def test_all_boards_added_known_ats_no_detections(self, workspace):
        """Known ATS board passes gate even without probe detections."""
        slug, ws, ws_root = workspace
        board = Board(
            alias="careers",
            slug="test-wf-careers",
            url="https://jobs.ashbyhq.com/TestCo",
            detections={},
        )
        save_board(slug, board)
        step = StepDef(
            id="add_boards",
            title="",
            instructions="",
            gate_type="state",
            gate_check="all_boards_added",
            phase="global",
        )
        passed, _ = check_gate(step, ws, [board])
        assert passed

    def test_no_boards(self, workspace):
        slug, ws, ws_root = workspace
        step = StepDef(
            id="add_boards",
            title="",
            instructions="",
            gate_type="state",
            gate_check="all_boards_added",
            phase="global",
        )
        passed, _ = check_gate(step, ws, [])
        assert not passed

    def test_monitor_tested(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        step = StepDef(
            id="select_monitor",
            title="",
            instructions="",
            gate_type="state",
            gate_check="monitor_tested",
            phase="per_board",
        )
        passed, _ = check_gate(step, ws, [board_careers], board_careers)
        assert passed

    def test_feedback_recorded(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        step = StepDef(
            id="verify_and_feedback",
            title="",
            instructions="",
            gate_type="state",
            gate_check="feedback_recorded",
            phase="per_board",
        )
        passed, _ = check_gate(step, ws, [board_careers], board_careers)
        assert passed

    def test_manual_gate_never_auto_passes(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        step = StepDef(id="reflect", title="", instructions="", gate_type="manual", phase="final")
        passed, _ = check_gate(step, ws, [board_careers])
        assert not passed


class TestSkipConditions:
    def test_rich_monitor_skips_scraper(self, board_careers):
        step = StepDef(
            id="select_scraper",
            title="",
            instructions="",
            gate_type="state",
            gate_check="scraper_tested",
            skip_when="rich_monitor",
            phase="per_board",
        )
        assert should_skip(step, board_careers)

    def test_non_rich_monitor(self):
        board = Board(
            alias="careers",
            slug="test-careers",
            url="https://test.com",
            active_config="sitemap",
            configs={
                "sitemap": {"monitor_type": "sitemap", "status": "tested", "run": {"jobs": 10}}
            },
        )
        step = StepDef(
            id="select_scraper",
            title="",
            instructions="",
            gate_type="state",
            gate_check="scraper_tested",
            skip_when="rich_monitor",
            phase="per_board",
        )
        assert not should_skip(step, board)


class TestAdvance:
    def test_advance_through_global(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        wf = WorkflowState(current_step="setup")
        _save_wf_to_disk(slug, wf)

        # Advance from setup (state gate — company_complete)
        next_step, msg = advance(slug, "Company configured")
        assert next_step is not None
        assert next_step.id == "add_boards"

    def test_advance_blocks_on_failed_gate(self, workspace):
        slug, ws, ws_root = workspace
        # Remove company name to fail the gate
        ws.name = ""
        save_workspace(ws)

        wf = WorkflowState(current_step="setup")
        _save_wf_to_disk(slug, wf)

        next_step, msg = advance(slug, "trying to advance")
        assert "Cannot advance" in msg

    def test_per_board_loop(self, workspace, board_careers, board_second):
        slug, ws, ws_root = workspace
        wf = WorkflowState(current_step="add_boards")
        _save_wf_to_disk(slug, wf)

        # Advance from add_boards to first board's select_monitor
        next_step, msg = advance(slug, "2 boards added")
        assert next_step.id == "select_monitor"
        assert msg == ""

        # Check that current_board is set (alphabetical order: careers-de before careers)
        wf = _load_wf_from_disk(slug)
        assert wf.current_board == "careers-de"

    def test_rich_monitor_skips_scraper(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        wf = WorkflowState(current_step="select_monitor", current_board="careers")
        _save_wf_to_disk(slug, wf)

        next_step, msg = advance(slug, "greenhouse 50 jobs")
        # Should skip scraper and go to verify
        assert next_step.id == "verify_and_feedback"

    def test_records_reflections(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        wf = WorkflowState(current_step="setup")
        _save_wf_to_disk(slug, wf)

        advance(slug, "Company configured with logos")
        wf = _load_wf_from_disk(slug)
        assert len(wf.reflections) == 1
        assert wf.reflections[0]["step"] == "setup"
        assert "logos" in wf.reflections[0]["notes"]

    def test_none_notes_recorded(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        wf = WorkflowState(current_step="setup")
        _save_wf_to_disk(slug, wf)

        advance(slug, "none")
        wf = _load_wf_from_disk(slug)
        assert wf.reflections[0]["notes"] == "none"


class TestContextInjection:
    def test_context_without_board(self, workspace):
        slug, ws, ws_root = workspace
        wf = WorkflowState()
        ctx = build_context(ws, [], wf)
        assert ctx["slug"] == "test-wf"
        assert ctx["issue"] == "42"

    def test_context_with_board(self, workspace, board_careers):
        slug, ws, ws_root = workspace
        wf = WorkflowState(current_board="careers")
        boards = [board_careers]
        ctx = build_context(ws, boards, wf, board_careers)
        assert ctx["board_url"] == "https://boards.greenhouse.io/test"
        assert "1/1" in ctx["board_progress"]


class TestKBSearch:
    def test_search_finds_match(self):
        results = search_kb("sitemap")
        assert len(results) > 0
        assert any("sitemap" in r["symptom"].lower() for r in results)

    def test_search_no_match(self):
        results = search_kb("xyznonexistent123")
        assert len(results) == 0

    def test_token_search(self):
        results = search_kb("zero jobs probe")
        assert len(results) > 0


class TestLocalMode:
    def test_is_local_mode(self, monkeypatch):
        from src.workspace.commands.lifecycle import is_local_mode

        monkeypatch.delenv("WS_LOCAL", raising=False)
        assert not is_local_mode()

        monkeypatch.setenv("WS_LOCAL", "1")
        assert is_local_mode()

        monkeypatch.setenv("WS_LOCAL", "true")
        assert is_local_mode()

        monkeypatch.setenv("WS_LOCAL", "yes")
        assert is_local_mode()

        monkeypatch.setenv("WS_LOCAL", "0")
        assert not is_local_mode()

        monkeypatch.setenv("WS_LOCAL", "")
        assert not is_local_mode()


class TestWorkflowFail:
    def test_fail_sets_state(self, workspace):
        slug, ws, ws_root = workspace
        wf = WorkflowState(current_step="select_monitor", current_board="careers")
        _save_wf_to_disk(slug, wf)

        wf.failed = True
        wf.fail_reason = "No monitor works"
        _save_wf_to_disk(slug, wf)

        loaded = _load_wf_from_disk(slug)
        assert loaded.failed
        assert loaded.fail_reason == "No monitor works"
        assert loaded.current_step == "select_monitor"
