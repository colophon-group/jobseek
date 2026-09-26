"""The selected JOIN inventory is produced exclusively by its Go runtime."""

from __future__ import annotations

import pytest

from src.core.monitors import BoardGoneError
from src.processing.board import _monitor_runtime_for_board
from src.runtime.join_go import GoJoinMonitorRuntime, percentage_selected

BOARD_ID = "20eae165-5251-40d4-b9a0-0254f4bd1ab3"
BOARD_URL = "https://join.com/companies/acme"
CONFIG = {"slug": "acme", "recent_discovered_counts": [7, 7, 7], "suspect_streak": 0}


def fake_binary(tmp_path, payload: dict, *, exit_code: int = 0) -> str:
    path = tmp_path / "join-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"assert sys.argv[1:] == {['--board-url', BOARD_URL, '--slug', 'acme']!r}\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


@pytest.mark.asyncio
async def test_go_join_emits_complete_url_set(tmp_path):
    urls = [f"{BOARD_URL}/1-engineer", f"{BOARD_URL}/2-designer"]
    runtime = GoJoinMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "urls": urls,
                "requests": 2,
                "responses": 2,
                "bytes": 100,
                "status": 200,
                "final_url": f"{BOARD_URL}?page=2",
            },
        ),
        board_id=BOARD_ID,
    )
    result = [item async for item in runtime.stream(BOARD_URL, "join", CONFIG, None)]
    assert len(result) == 1
    assert result[0].urls == set(urls)


@pytest.mark.asyncio
async def test_go_join_maps_first_page_retirement(tmp_path):
    runtime = GoJoinMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "urls": [],
                "requests": 1,
                "responses": 1,
                "bytes": 4,
                "status": 404,
                "final_url": BOARD_URL,
                "error_kind": "gone",
                "error": "JOIN board returned HTTP 404",
            },
            exit_code=1,
        ),
        board_id=BOARD_ID,
    )
    with pytest.raises(BoardGoneError) as exc:
        async for _ in runtime.stream(BOARD_URL, "join", CONFIG, None):
            pass
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_go_join_rejects_config_expansion_before_request(tmp_path):
    runtime = GoJoinMonitorRuntime(fake_binary(tmp_path, {}), board_id=BOARD_ID)
    with pytest.raises(ValueError, match="unchanged slug-only"):
        async for _ in runtime.stream(BOARD_URL, "join", {**CONFIG, "proxy": True}, None):
            pass


@pytest.mark.asyncio
async def test_go_join_rejects_mismatched_response_endpoint(tmp_path):
    runtime = GoJoinMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "urls": [],
                "requests": 1,
                "responses": 1,
                "bytes": 1,
                "status": 200,
                "final_url": "https://join.com/companies/acme-other",
            },
        ),
        board_id=BOARD_ID,
    )
    with pytest.raises(ValueError, match="unexpected endpoint"):
        async for _ in runtime.stream(BOARD_URL, "join", CONFIG, None):
            pass


def test_dark_default_explicit_selection_and_strict_percent(monkeypatch):
    monkeypatch.delenv("JOIN_GO_PERCENT", raising=False)
    monkeypatch.delenv("JOIN_GO_BOARD_IDS", raising=False)
    assert not percentage_selected(BOARD_ID, BOARD_URL, CONFIG)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "python"
    monkeypatch.setenv("JOIN_GO_BOARD_IDS", BOARD_ID)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "go-join"
    monkeypatch.delenv("JOIN_GO_BOARD_IDS")
    monkeypatch.setenv("JOIN_GO_PERCENT", "100")
    assert percentage_selected(BOARD_ID, BOARD_URL, CONFIG)
    assert (
        _monitor_runtime_for_board(
            BOARD_ID,
            None,
            monitor_type="join",
            board_url=BOARD_URL,
            monitor_config=CONFIG,
        ).implementation
        == "go-join"
    )
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**CONFIG, "proxy": True})
    assert not percentage_selected(
        BOARD_ID, BOARD_URL, {**CONFIG, "recent_discovered_counts": [1, 2]}
    )
    assert not percentage_selected(
        BOARD_ID, BOARD_URL, {**CONFIG, "recent_discovered_counts": [1, True, 2]}
    )
    monkeypatch.setenv("JOIN_GO_PERCENT", "01")
    assert not percentage_selected(BOARD_ID, BOARD_URL, CONFIG)
