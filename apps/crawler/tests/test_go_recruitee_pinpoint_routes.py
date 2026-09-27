"""Published-provider routing, downstream policy and child lifetime contracts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import pytest
import structlog.testing

from src.processing.board import _monitor_runtime_for_board
from src.runtime import pinpoint_go, recruitee_go

PROVIDERS = [
    ("recruitee", recruitee_go, recruitee_go.GoRecruiteeMonitorRuntime),
    ("pinpoint", pinpoint_go, pinpoint_go.GoPinpointMonitorRuntime),
]


@pytest.mark.parametrize("name,module,runtime_type", PROVIDERS)
def test_frozen_current_registry_endpoint_mapping(name, module, runtime_type, monkeypatch):
    monkeypatch.delenv(f"{name.upper()}_GO_PERCENT", raising=False)
    path = (
        Path(__file__).parents[1] / "go" / f"{name}-monitor" / "testdata" / "python_endpoints.json"
    )
    for row in json.loads(path.read_text()):
        selected = module._eligible(row["board_url"], name, row["config"], None)
        endpoint = (
            selected + "/api/offers"
            if name == "recruitee"
            else f"https://{selected}.pinpointhq.com/postings.json"
        )
        assert endpoint == row["endpoint"]
        assert module.percentage_selected("board", row["board_url"], row["config"])
        assert isinstance(
            _monitor_runtime_for_board(
                "board",
                None,
                monitor_type=name,
                board_url=row["board_url"],
                monitor_config=row["config"],
            ),
            runtime_type,
        )


@pytest.mark.parametrize("name,module,runtime_type", PROVIDERS)
def test_route_reversal_and_no_history_gate(name, module, runtime_type, monkeypatch):
    board = f"https://acme.{'recruitee.com' if name == 'recruitee' else 'pinpointhq.com'}"
    monkeypatch.delenv(f"{name.upper()}_GO_PERCENT", raising=False)
    for counts in (None, [], [0], [10000] * 3):
        assert module.percentage_selected("board", board, {"recent_discovered_counts": counts})
    for extra in ({"proxy": "datacenter"}, {"headers": {"X-Test": "x"}}, {"browser": True}):
        assert not module.percentage_selected("board", board, extra)
    monkeypatch.setenv(f"{name.upper()}_GO_PERCENT", "0")
    assert not module.percentage_selected("board", board, {})
    monkeypatch.setenv(f"{name.upper()}_GO_BOARD_IDS", "board")
    assert isinstance(_monitor_runtime_for_board("board", None), runtime_type)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,module,runtime_type", PROVIDERS)
@pytest.mark.parametrize("empty", [False, True])
async def test_shared_policy_and_empty_inventory(name, module, runtime_type, tmp_path, empty):
    domain = "recruitee.com" if name == "recruitee" else "pinpointhq.com"
    endpoint = f"https://acme.{domain}/" + (
        "api/offers" if name == "recruitee" else "postings.json"
    )
    jobs = (
        []
        if empty
        else [
            {"url": "https://jobs.example/123-title", "title": "Engineer"},
            {"url": "https://other.example/456"},
        ]
    )
    payload = {
        "jobs": jobs,
        "truncated": False,
        "requests": 1,
        "responses": 1,
        "bytes": 100,
        "status": 200,
        "final_url": endpoint,
    }
    binary = tmp_path / "native"
    binary.write_text(
        "#!/usr/bin/env python3\nimport json\nprint(json.dumps(" + repr(payload) + "))\n"
    )
    binary.chmod(0o755)
    runtime = runtime_type(str(binary), board_id="board")
    config = {
        "url_allowlist": r"^https://jobs\.example/.*$",
        "url_transform": {"find": r"-title$", "replace": ""},
    }
    with structlog.testing.capture_logs() as logs:
        results = [x async for x in runtime.stream(f"https://acme.{domain}", name, config, None)]
    urls = set().union(*(x.urls for x in results))
    assert urls == (set() if empty else {"https://jobs.example/123"})
    event = next(x for x in logs if x["event"] == f"go_{name}.monitor_postprocessed")
    assert event["url_sha256"] == hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,module,runtime_type", PROVIDERS)
@pytest.mark.parametrize("cancel", [False, True])
async def test_child_bound_and_reaping(name, module, runtime_type, tmp_path, monkeypatch, cancel):
    pid_file = tmp_path / "pid"
    binary = tmp_path / "native"
    binary.write_text(
        "#!/usr/bin/env python3\nimport os,signal,time\n"
        "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
        + f'open({str(pid_file)!r},"w").write(str(os.getpid()))\n'
        + ("" if cancel else 'os.write(1,b"x"*2048)\n')
        + "time.sleep(30)\n"
    )
    binary.chmod(0o755)
    monkeypatch.setattr(module, "_MAX_OUTPUT_BYTES", 1024)
    monkeypatch.setattr(module, "_CHILD_STOP_SECONDS", 0.05)
    runtime = runtime_type(str(binary), board_id="board")

    async def consume():
        return [
            x
            async for x in runtime.stream(
                "https://acme." + ("recruitee.com" if name == "recruitee" else "pinpointhq.com"),
                name,
                {},
                None,
            )
        ]

    task = asyncio.create_task(consume())
    async with asyncio.timeout(5):
        while not pid_file.exists() or not pid_file.read_text():
            await asyncio.sleep(0.01)
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(ValueError, match="exceeded"):
                await task
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
