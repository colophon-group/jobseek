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
from src.runtime.ashby_go import GoAshbyMonitorRuntime, direct_ashby_token

BOARD_URL = "https://jobs.ashbyhq.com/acme"
API_URL = "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
CONFIG = {"token": "acme", "scraper_type": "skip"}
GO_MODULE = Path(__file__).resolve().parents[1] / "go" / "ashby-monitor"


def fake_binary(tmp_path: Path, payload: dict, *, exit_code: int = 0, token: str = "acme") -> str:
    path = tmp_path / "ashby-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"assert sys.argv[1:] == ['--token', {token!r}]\n"
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


ENDPOINT_CASES = json.loads((GO_MODULE / "testdata/python_endpoints.json").read_text())


@pytest.mark.parametrize("case", ENDPOINT_CASES, ids=lambda case: case["token"])
@pytest.mark.asyncio
async def test_frozen_production_configuration_preserves_python_request(case, monkeypatch):
    observed = []

    def capture(request):
        observed.append(str(request.url))
        return httpx.Response(200, json={"jobs": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(capture)) as client:
        assert (
            await discover({"board_url": case["board_url"], "metadata": case["config"]}, client)
            == []
        )
    assert observed == [case["endpoint"]]
    assert direct_ashby_token(case["board_url"], case["config"]) == case["token"]
    monkeypatch.setenv("ASHBY_GO_PERCENT", "100")
    assert (
        _monitor_runtime_for_board(
            case["board_id"],
            None,
            monitor_type="ashby",
            board_url=case["board_url"],
            monitor_config=case["config"],
        ).implementation
        == "go-ashby"
    )


@pytest.mark.parametrize(
    "url,patch",
    [
        (BOARD_URL, {"token": "../escape"}),
        (BOARD_URL, {"token": "a%2Fb"}),
        (BOARD_URL, {"token": "a?query=1"}),
        (BOARD_URL, {"token": "a#fragment"}),
        (BOARD_URL, {"token": "acme "}),
        (BOARD_URL, {"proxy": True}),
        (BOARD_URL, {"render": True}),
        (BOARD_URL, {"ssl_verify": False}),
        (BOARD_URL, {"org": "different"}),
        (BOARD_URL, {"board_token": "different"}),
        (BOARD_URL, {"blast_radius_floor": True}),
        (BOARD_URL, {"blast_radius_floor": 1.1}),
        (BOARD_URL, {"blast_radius_floor": float("nan")}),
        (BOARD_URL, {"scraper_config": "invalid"}),
        ("https://user:password@jobs.ashbyhq.com/acme", {}),
        ("https://jobs.ashbyhq.com:bad/acme", {}),
        ("https://jobs.ashbyhq.com:8443/acme", {}),
        ("https://jobs.ashbyhq.com/acme?different=1", {}),
        ("https://jobs.ashbyhq.com/acme/one", {"token": None}),
        ("https://jobs.ashbyhq.com/Flock%20Safety", {"token": None}),
        ("https://jobs.ashbyhq.com/lakera.ai", {"token": None}),
        ("https://example.com/jobs", {"token": None}),
    ],
)
def test_unsupported_configuration_stays_python(url, patch, monkeypatch):
    config = {**CONFIG, **patch}
    assert direct_ashby_token(url, config) is None
    monkeypatch.setenv("ASHBY_GO_PERCENT", "100")
    assert (
        _monitor_runtime_for_board(
            "board-a", None, monitor_type="ashby", board_url=url, monitor_config=config
        ).implementation
        == "python"
    )


@pytest.mark.asyncio
async def test_spaced_token_accepts_exact_encoded_response_endpoint(tmp_path):
    token = "Flock Safety"
    endpoint = (
        "https://api.ashbyhq.com/posting-api/job-board/Flock%20Safety?includeCompensation=true"
    )
    payload = {
        "jobs": [],
        "truncated": False,
        "status": 200,
        "requests": 1,
        "responses": 1,
        "bytes": 11,
        "final_url": endpoint,
    }
    runtime = GoAshbyMonitorRuntime(fake_binary(tmp_path, payload, token=token), board_id="board-a")
    assert [
        r
        async for r in runtime.stream(
            "https://jobs.ashbyhq.com/Flock%20Safety", "ashby", {**CONFIG, "token": token}, None
        )
    ] == []
    payload["final_url"] = endpoint.replace("Flock%20Safety", "other")
    runtime = GoAshbyMonitorRuntime(fake_binary(tmp_path, payload, token=token), board_id="board-a")
    with pytest.raises(ValueError, match="unexpected response endpoint"):
        async for _ in runtime.stream(BOARD_URL, "ashby", {**CONFIG, "token": token}, None):
            pass


def test_monitor_routing_preserves_separate_detail_and_drop_settings(monkeypatch):
    from src.processing.board import _monitor_owns_existing_description
    from src.processing.scrape import _effective_board_enrich, _is_skip_no_scrape

    config = {
        "token": "acme",
        "scraper_type": "json-ld",
        "blast_radius_floor": 0.9,
        "scraper_config": {"render": True, "enrich": ["description"]},
    }
    monkeypatch.setenv("ASHBY_GO_PERCENT", "100")
    assert (
        _monitor_runtime_for_board(
            "board-a",
            None,
            monitor_type="ashby",
            board_url="https://example.com/jobs",
            monitor_config=config,
        ).implementation
        == "go-ashby"
    )
    assert _effective_board_enrich(config, "ashby") == ["description"]
    assert not _monitor_owns_existing_description(_effective_board_enrich(config, "ashby"))
    assert not _is_skip_no_scrape(config, "ashby")
    assert config["blast_radius_floor"] == 0.9
