"""Workable Go handoff uses the same page bytes and keeps detail scraping."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from src.core.monitors import workable_capture
from src.core.monitors.workable import discover
from src.processing.board import _monitor_runtime_for_board
from src.runtime.workable_go import GoWorkableMonitorRuntime, percentage_selected

BOARD_ID = "06e7e8f2-4741-49b2-a135-96cfeb3bcfdd"
BOARD_URL = "https://apply.workable.com/pix4d/"
CONFIG = {"token": "pix4d", "scraper_type": "workable"}
GO_MODULE = Path(__file__).resolve().parents[1] / "go" / "workable-monitor"


def fake_binary(tmp_path: Path, payload: dict) -> str:
    path = tmp_path / "workable-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['--slug', 'pix4d']\n"
        f"print(json.dumps({payload!r}))\n"
    )
    path.chmod(0o755)
    return str(path)


def test_default_off_strict_route_and_percentage(monkeypatch):
    monkeypatch.delenv("WORKABLE_GO_BOARD_IDS", raising=False)
    monkeypatch.delenv("WORKABLE_GO_PERCENT", raising=False)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "python"
    monkeypatch.setenv("WORKABLE_GO_BOARD_IDS", BOARD_ID)
    assert (
        _monitor_runtime_for_board(
            BOARD_ID,
            None,
            monitor_type="workable",
            board_url=BOARD_URL,
            monitor_config=CONFIG,
        ).implementation
        == "go-workable"
    )
    monkeypatch.delenv("WORKABLE_GO_BOARD_IDS")
    monkeypatch.setenv("WORKABLE_GO_PERCENT", "100")
    stable = {**CONFIG, "recent_discovered_counts": [3, 3, 3]}
    assert percentage_selected(BOARD_ID, BOARD_URL, stable)
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**stable, "proxy": True})
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**stable, "token": "other"})
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**stable, "scraper_type": "skip"})
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**CONFIG, "recent_discovered_counts": [3]})


@pytest.mark.asyncio
async def test_runtime_emits_url_only_inventory(tmp_path):
    urls = ["https://apply.workable.com/pix4d/j/A/", "https://apply.workable.com/pix4d/j/B/"]
    runtime = GoWorkableMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "urls": urls,
                "truncated": False,
                "verified_empty": False,
                "requests": 1,
                "responses": 1,
                "bytes": 100,
                "status": 200,
                "final_url": "https://apply.workable.com/api/v3/accounts/pix4d/jobs",
            },
        ),
        board_id=BOARD_ID,
    )
    results = [r async for r in runtime.stream(BOARD_URL, "workable", CONFIG, None)]
    assert len(results) == 1
    assert results[0].urls == set(urls)
    assert not results[0].jobs_by_url


@pytest.mark.asyncio
async def test_verified_zero_keeps_publisher_empty_policy(tmp_path):
    runtime = GoWorkableMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "urls": [],
                "truncated": False,
                "verified_empty": True,
                "requests": 5,
                "responses": 5,
                "bytes": 120,
                "status": 200,
                "final_url": "https://apply.workable.com/pix4d/llms.txt",
            },
        ),
        board_id=BOARD_ID,
    )
    results = [r async for r in runtime.stream(BOARD_URL, "workable", CONFIG, None)]
    assert results[0].urls == set()
    assert results[0].verified_empty_reason == (
        "Workable llms.txt advertises zero current openings"
    )


@pytest.mark.asyncio
async def test_same_pages_produce_exact_python_and_go_urls(monkeypatch, tmp_path):
    pages = [
        {"results": [{"shortcode": "A"}, {"shortcode": "B"}], "nextPage": "cursor"},
        {"results": [{"shortcode": "B"}, {"shortcode": "C"}], "nextPage": None},
    ]
    raw_pages = [json.dumps(page).encode() for page in pages]
    replay = subprocess.run(
        ["go", "run", "./cmd/replay"],
        input=json.dumps({"slug": "pix4d", "pages": pages}),
        text=True,
        capture_output=True,
        cwd=GO_MODULE,
        check=True,
    )
    go_urls = set(json.loads(replay.stdout)["urls"])
    calls = 0
    monkeypatch.setenv("WORKABLE_CAPTURE_SLUGS", "pix4d")
    monkeypatch.setattr(workable_capture, "_CAPTURE_DIR", tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.url.path == "/api/v3/accounts/pix4d/jobs"
        assert request.method == "POST"
        body = raw_pages[calls]
        calls += 1
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        python_urls = await discover({"board_url": BOARD_URL, "metadata": CONFIG}, client)
    assert calls == 2
    assert go_urls == python_urls
    assert (tmp_path / "jobseek-workable-pix4d-page0.json").read_bytes() == raw_pages[0]
    assert (tmp_path / "jobseek-workable-pix4d-page1.json").read_bytes() == raw_pages[1]
    digest = hashlib.sha256("\n".join(sorted(go_urls)).encode()).hexdigest()
    assert digest == hashlib.sha256("\n".join(sorted(python_urls)).encode()).hexdigest()


def test_capture_is_bounded_and_mode_0600(monkeypatch, tmp_path):
    monkeypatch.setenv("WORKABLE_CAPTURE_SLUGS", "pix4d")
    monkeypatch.setattr(workable_capture, "_CAPTURE_DIR", tmp_path)
    url = "https://apply.workable.com/api/v3/accounts/pix4d/jobs"
    workable_capture.capture_workable_response("pix4d", 0, url, b'{"results":[]}')
    path = tmp_path / "jobseek-workable-pix4d-page0.json"
    assert path.read_bytes() == b'{"results":[]}'
    assert path.stat().st_mode & 0o777 == 0o600
    workable_capture.capture_workable_response("pix4d", 0, url, b"changed")
    assert path.read_bytes() == b'{"results":[]}'
    workable_capture.capture_workable_response("pix4d", 1, "https://example.com/jobs", b"bad")
    assert not (tmp_path / "jobseek-workable-pix4d-page1.json").exists()
