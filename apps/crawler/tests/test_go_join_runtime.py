"""The selected JOIN inventory is produced exclusively by its Go runtime."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from src.core.monitors import BoardGoneError
from src.processing.board import _monitor_runtime_for_board
from src.runtime.join_go import GoJoinMonitorRuntime, percentage_selected

BOARD_ID = "20eae165-5251-40d4-b9a0-0254f4bd1ab3"
BOARD_URL = "https://join.com/companies/acme"
CONFIG = {"slug": "acme", "recent_discovered_counts": [7, 7, 7], "suspect_streak": 0}
MODULE = Path(__file__).resolve().parents[1] / "go/join-monitor"


def test_go_join_native_transport_and_parity(tmp_path):
    subprocess.run(["go", "test", "-race", "./..."], cwd=MODULE, check=True, capture_output=True)
    binary = tmp_path / "join-live"
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/live"], cwd=MODULE, check=True)
    subprocess.run(
        ["python3", str(MODULE / "testdata/verify_installed.py"), str(binary)], check=True
    )


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
                "final_url": "http://join.com/companies/acme-other",
            },
        ),
        board_id=BOARD_ID,
    )
    with pytest.raises(ValueError, match="unexpected endpoint"):
        async for _ in runtime.stream(BOARD_URL, "join", CONFIG, None):
            pass


def test_full_provider_default_explicit_selection_and_strict_percent(monkeypatch):
    monkeypatch.delenv("JOIN_GO_PERCENT", raising=False)
    monkeypatch.delenv("JOIN_GO_BOARD_IDS", raising=False)
    assert percentage_selected(BOARD_ID, BOARD_URL, CONFIG)
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
    assert percentage_selected(BOARD_ID, BOARD_URL, {"slug": "acme"})
    assert percentage_selected(BOARD_ID, BOARD_URL, {**CONFIG, "recent_discovered_counts": []})
    monkeypatch.setenv("JOIN_GO_PERCENT", "0")
    assert not percentage_selected(BOARD_ID, BOARD_URL, CONFIG)
    monkeypatch.setenv("JOIN_GO_PERCENT", "01")
    assert not percentage_selected(BOARD_ID, BOARD_URL, CONFIG)


@pytest.mark.asyncio
async def test_go_join_output_bound_and_cancel_reap(tmp_path, monkeypatch):
    from src.runtime import join_go

    children = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        proc = await create(*args, **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    monkeypatch.setattr(join_go, "_MAX_OUTPUT_BYTES", 100)
    path = tmp_path / "child"
    path.write_text(
        "#!/usr/bin/env python3\nimport time\nprint('x'*200,flush=True)\ntime.sleep(60)\n"
    )
    path.chmod(0o755)
    runtime = GoJoinMonitorRuntime(str(path), board_id=BOARD_ID)

    async def run():
        return [r async for r in runtime.stream(BOARD_URL, "join", CONFIG, None)]

    with pytest.raises(ValueError, match="output exceeded"):
        await asyncio.wait_for(run(), 10)
    assert children[-1].returncode is not None
    path.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n")
    task = asyncio.create_task(run())
    async with asyncio.timeout(5):
        while len(children) < 2:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert children[-1].returncode is not None
