"""Default-off Go Lever rich monitor and same-response projection boundary."""

from __future__ import annotations

import json
import stat
import subprocess
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from src.core.monitors import lever as lever_monitor
from src.core.monitors.lever import discover
from src.processing.board import _monitor_runtime_for_board
from src.runtime.lever_go import GoLeverMonitorRuntime

BOARD_URL = "https://jobs.lever.co/acme"
API_URL = "https://api.lever.co/v0/postings/acme?limit=100&skip=0"
CONFIG = {"token": "acme", "scraper_type": "skip"}
GO_MODULE = Path(__file__).resolve().parents[1] / "go" / "lever-monitor"


def fake_binary(tmp_path: Path, payload: dict) -> str:
    path = tmp_path / "lever-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['--token', 'acme', '--region', '']\n"
        f"print(json.dumps({payload!r}))\n"
    )
    path.chmod(0o755)
    return str(path)


def test_lever_route_is_default_off_and_explicit(monkeypatch):
    monkeypatch.delenv("LEVER_GO_BOARD_IDS", raising=False)
    assert _monitor_runtime_for_board("board-a", None).implementation == "python"
    monkeypatch.setenv("LEVER_GO_BOARD_IDS", " board-a, ")
    assert _monitor_runtime_for_board("board-a", None).implementation == "go-lever"
    assert _monitor_runtime_for_board("board-b", None).implementation == "python"


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [CONFIG, {"scraper_type": "skip"}])
async def test_same_bytes_produce_same_rich_job(config: dict):
    body = json.dumps(
        [
            {
                "hostedUrl": "https://jobs.lever.co/acme/1",
                "text": "Engineer",
                "description": "<p>Build</p>",
                "lists": [{"text": "Benefits", "content": "<li>Travel</li>"}],
                "additional": "<p>More</p>",
                "categories": {
                    "allLocations": ["Zurich", "London"],
                    "commitment": "Full-time",
                    "team": "Platform",
                    "department": "Engineering",
                },
                "workplaceType": "hybrid",
                "salaryRange": {
                    "currency": "CHF",
                    "min": 80000,
                    "max": 120000,
                    "interval": "per-year-salary",
                },
                "id": "one",
            },
            {"text": "No URL"},
        ]
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
        python_jobs = await discover({"board_url": BOARD_URL, "metadata": config}, client)
    fields = (
        "url",
        "title",
        "description",
        "locations",
        "employment_type",
        "job_location_type",
        "base_salary",
        "metadata",
    )
    assert go_jobs == [{field: asdict(job)[field] for field in fields} for job in python_jobs]


@pytest.mark.asyncio
async def test_selected_runtime_delivers_content_and_rejects_changed_config(tmp_path: Path):
    binary = fake_binary(
        tmp_path,
        {
            "jobs": [
                {
                    "url": "https://jobs.lever.co/acme/1",
                    "title": "Engineer",
                    "description": "<p>Build</p>",
                    "locations": ["Zurich"],
                    "employment_type": "Full-time",
                    "job_location_type": "hybrid",
                    "base_salary": None,
                    "metadata": None,
                }
            ],
            "truncated": False,
            "status": 200,
            "requests": 1,
            "responses": 1,
            "bytes": 100,
            "last_skip": 0,
            "final_url": API_URL,
        },
    )
    runtime = GoLeverMonitorRuntime(binary, board_id="board-a")
    results = [result async for result in runtime.stream(BOARD_URL, "lever", CONFIG, None)]
    assert len(results) == 1
    assert results[0].jobs_by_url["https://jobs.lever.co/acme/1"].title == "Engineer"
    with pytest.raises(ValueError, match="unchanged direct rich token"):
        async for _ in runtime.stream(BOARD_URL, "lever", {**CONFIG, "proxy": True}, None):
            pass


@pytest.mark.asyncio
async def test_selected_runtime_derives_direct_url_token_like_python(tmp_path: Path):
    binary = fake_binary(
        tmp_path,
        {
            "jobs": [],
            "truncated": False,
            "status": 200,
            "requests": 1,
            "responses": 1,
            "bytes": 2,
            "last_skip": 0,
            "final_url": API_URL,
        },
    )
    runtime = GoLeverMonitorRuntime(binary, board_id="board-a")
    results = [
        result
        async for result in runtime.stream(BOARD_URL, "lever", {"scraper_type": "skip"}, None)
    ]
    assert results == []


@pytest.mark.asyncio
async def test_natural_capture_preserves_existing_response(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(lever_monitor, "_CAPTURE_DIR", tmp_path)
    monkeypatch.setenv("LEVER_CAPTURE_TOKENS", "acme")
    requests = 0
    body = b'[{"hostedUrl":"https://jobs.lever.co/acme/1"}]'

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await discover({"board_url": BOARD_URL, "metadata": CONFIG}, client)
    artifact = tmp_path / "jobseek-lever-acme-0.body"
    assert requests == 1
    assert artifact.read_bytes() == body
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
