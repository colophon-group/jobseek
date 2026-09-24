"""Default-off Go Ashby rich monitor and same-byte projection boundary."""

from __future__ import annotations

import json
import stat
import subprocess
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from src.core.monitors import ashby as ashby_monitor
from src.core.monitors.ashby import discover
from src.processing.board import _monitor_runtime_for_board
from src.runtime.ashby_go import GoAshbyMonitorRuntime

BOARD_URL = "https://jobs.ashbyhq.com/acme"
API_URL = "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
CONFIG = {"token": "acme", "scraper_type": "skip"}
GO_MODULE = Path(__file__).resolve().parents[1] / "go" / "ashby-monitor"


def fake_binary(tmp_path: Path, payload: dict, *, exit_code: int = 0) -> str:
    path = tmp_path / "ashby-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['--token', 'acme']\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


def test_ashby_route_is_default_off_and_explicit(monkeypatch):
    monkeypatch.delenv("ASHBY_GO_BOARD_IDS", raising=False)
    assert _monitor_runtime_for_board("board-a", None).implementation == "python"
    monkeypatch.setenv("ASHBY_GO_BOARD_IDS", " board-a, ")
    assert _monitor_runtime_for_board("board-a", None).implementation == "go-ashby"
    assert _monitor_runtime_for_board("board-b", None).implementation == "python"


@pytest.mark.asyncio
async def test_same_bytes_produce_same_rich_job():
    body = json.dumps(
        {
            "jobs": [
                {
                    "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                    "title": "Engineer",
                    "descriptionHtml": "<p>Build</p>",
                    "location": "Zurich",
                    "secondaryLocations": [{"location": "London"}, "Zurich"],
                    "employmentType": "FullTime",
                    "workplaceType": "Hybrid",
                    "publishedAt": "2026-09-01",
                    "department": "Engineering",
                    "id": "one",
                    "compensationTierSummary": "tier-1",
                },
                {"jobUrl": "https://jobs.ashbyhq.com/acme/2", "isListed": False},
            ],
            "compensation": {
                "compensationTierSummary": [
                    {
                        "id": "tier-1",
                        "min": 80000,
                        "max": 120000,
                        "currency": "CHF",
                        "interval": "annually",
                    }
                ]
            },
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
    fields = (
        "url",
        "title",
        "description",
        "locations",
        "employment_type",
        "job_location_type",
        "date_posted",
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
                    "url": "https://jobs.ashbyhq.com/acme/1",
                    "title": "Engineer",
                    "description": "<p>Build</p>",
                    "locations": ["Zurich"],
                    "employment_type": "FullTime",
                    "job_location_type": "hybrid",
                    "date_posted": "2026-09-01",
                    "base_salary": None,
                    "metadata": None,
                }
            ],
            "truncated": False,
            "status": 200,
            "requests": 1,
            "responses": 1,
            "bytes": 100,
            "final_url": API_URL,
        },
    )
    runtime = GoAshbyMonitorRuntime(binary, board_id="board-a")
    results = [result async for result in runtime.stream(BOARD_URL, "ashby", CONFIG, None)]
    assert len(results) == 1
    assert results[0].jobs_by_url["https://jobs.ashbyhq.com/acme/1"].title == "Engineer"
    with pytest.raises(ValueError, match="unchanged rich token"):
        async for _ in runtime.stream(BOARD_URL, "ashby", {**CONFIG, "proxy": True}, None):
            pass


@pytest.mark.asyncio
async def test_natural_capture_preserves_one_existing_response(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ashby_monitor, "_CAPTURE_DIR", tmp_path)
    monkeypatch.setenv("ASHBY_CAPTURE_TOKENS", "acme")
    requests = 0
    body = b'{"jobs":[]}'

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await discover({"board_url": BOARD_URL, "metadata": CONFIG}, client)
        await discover({"board_url": BOARD_URL, "metadata": CONFIG}, client)
    artifact = tmp_path / "jobseek-ashby-acme.body"
    assert requests == 2
    assert artifact.read_bytes() == body
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
