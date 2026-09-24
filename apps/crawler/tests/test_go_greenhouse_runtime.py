"""Exact rich-field replay and the selected Go Greenhouse execution seam."""

from __future__ import annotations

import json
import stat
import subprocess
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from src.core.monitors.greenhouse import discover
from src.processing.board import _monitor_runtime_for_board
from src.runtime.greenhouse_go import GoGreenhouseMonitorRuntime

BOARD_URL = "https://job-boards.greenhouse.io/elastic"
API_URL = "https://boards-api.greenhouse.io/v1/boards/elastic/jobs?content=true"
CONFIG = {"token": "elastic", "scraper_type": "skip"}
GO_MODULE = Path(__file__).resolve().parents[1] / "go" / "greenhouse-monitor"


def fake_binary(tmp_path: Path, payload: dict, *, exit_code: int = 0) -> str:
    path = tmp_path / "greenhouse-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


def test_go_route_is_default_off_and_exact_board_only(monkeypatch) -> None:
    monkeypatch.delenv("GREENHOUSE_GO_BOARD_ID", raising=False)
    assert (
        _monitor_runtime_for_board("0b0b0ae8-3635-47b3-929d-79e439879598", None).implementation
        == "python"
    )
    monkeypatch.setenv("GREENHOUSE_GO_BOARD_ID", "0b0b0ae8-3635-47b3-929d-79e439879598")
    assert isinstance(
        _monitor_runtime_for_board("0b0b0ae8-3635-47b3-929d-79e439879598", None),
        GoGreenhouseMonitorRuntime,
    )
    assert _monitor_runtime_for_board("other-board", None).implementation == "python"


@pytest.mark.asyncio
async def test_same_bytes_produce_same_rich_jobs() -> None:
    body = json.dumps(
        {
            "jobs": [
                {
                    "absolute_url": "https://job-boards.greenhouse.io/elastic/jobs/1",
                    "title": " Senior\tEngineer  ",
                    "content": "<p>Role</p><a>Read more</a><a href='/x'>Learn more</a>",
                    "location": {"name": " New\tYork "},
                    "offices": [{"name": "New York"}, {"name": " Zürich "}],
                    "departments": [{"name": "Engineering"}],
                    "education": {"degree": "BS"},
                    "requisition_id": "REQ-1",
                    "first_published": "2026-09-01T00:00:00Z",
                    "language": "en",
                },
                {
                    "absolute_url": "https://job-boards.greenhouse.io/elastic/jobs/2",
                    "title": "Designer",
                    "content": "<p>Design</p>",
                },
            ]
        }
    )
    replay = subprocess.run(
        ["go", "run", "./cmd/replay"],
        input=body,
        text=True,
        capture_output=True,
        cwd=GO_MODULE,
        check=True,
    )
    go_jobs = json.loads(replay.stdout)["jobs"]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        python_jobs = await discover({"board_url": BOARD_URL, "metadata": CONFIG}, client)
    fields = ("url", "title", "description", "locations", "date_posted", "language", "metadata")
    assert go_jobs == [{field: asdict(job)[field] for field in fields} for job in python_jobs]


@pytest.mark.asyncio
async def test_scheduled_capture_uses_existing_response_once(tmp_path: Path, monkeypatch) -> None:
    body = b'{"jobs":[]}'
    capture = tmp_path / "elastic.body"
    monkeypatch.setenv("GREENHOUSE_ELASTIC_CAPTURE_PATH", str(capture))
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await discover({"board_url": BOARD_URL, "metadata": CONFIG}, client)
        await discover({"board_url": BOARD_URL, "metadata": CONFIG}, client)
    assert requests == 2
    assert capture.read_bytes() == body
    assert stat.S_IMODE(capture.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_runtime_delivers_rich_jobs_and_rejects_changed_config(tmp_path: Path) -> None:
    binary = fake_binary(
        tmp_path,
        {
            "jobs": [
                {
                    "url": "https://job-boards.greenhouse.io/elastic/jobs/1",
                    "title": "Engineer",
                    "description": "<p>Role</p>",
                    "locations": ["Zürich"],
                    "date_posted": "2026-09-01",
                    "language": "en",
                    "metadata": {"departments": ["Engineering"]},
                }
            ],
            "truncated": False,
            "status": 200,
            "requests": 1,
            "responses": 1,
            "bytes": 120,
            "final_url": API_URL,
        },
    )
    runtime = GoGreenhouseMonitorRuntime(binary)
    results = [result async for result in runtime.stream(BOARD_URL, "greenhouse", CONFIG, None)]
    assert len(results) == 1
    assert results[0].jobs_by_url is not None
    job = results[0].jobs_by_url["https://job-boards.greenhouse.io/elastic/jobs/1"]
    assert job.title == "Engineer" and job.description == "<p>Role</p>"
    with pytest.raises(ValueError, match="unchanged Elastic"):
        async for _ in runtime.stream(BOARD_URL, "greenhouse", {**CONFIG, "proxy": True}, None):
            pass


@pytest.mark.asyncio
async def test_runtime_preserves_board_gone_signal(tmp_path: Path) -> None:
    from src.core.monitors import BoardGoneError

    binary = fake_binary(
        tmp_path,
        {
            "jobs": None,
            "truncated": False,
            "status": 404,
            "requests": 1,
            "responses": 1,
            "bytes": 0,
            "final_url": API_URL,
            "error": "Greenhouse returned HTTP 404",
        },
        exit_code=1,
    )
    runtime = GoGreenhouseMonitorRuntime(binary)
    with pytest.raises(BoardGoneError):
        async for _ in runtime.stream(BOARD_URL, "greenhouse", CONFIG, None):
            pass
