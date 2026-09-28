"""Native DOM extraction owns the fetched document, without fetching it again."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import stat
import subprocess
from pathlib import Path

import httpx
import pytest

from src.core.scrapers import dom
from src.runtime import dom_go_parse


@pytest.fixture(scope="module")
def native_binary(tmp_path_factory) -> str:
    binary = tmp_path_factory.mktemp("dom-go") / "dom-detail-parse"
    subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/parse"],
        cwd=Path(__file__).resolve().parents[1] / "go/dom-detail",
        check=True,
    )
    return str(binary)


@pytest.fixture
def native(monkeypatch, native_binary, tmp_path):
    monkeypatch.setenv("DOM_GO_PARSE_ENABLED", "1")
    monkeypatch.setattr(dom_go_parse, "_BINARY", native_binary)
    monkeypatch.setattr(dom_go_parse, "_CAPTURE_PREFIX", str(tmp_path / "capture"))
    return tmp_path


@pytest.mark.asyncio
async def test_normal_fetch_native_fields_fragment_and_url_defaults(native):
    url = "https://jobs.example.test/position#job"
    html = "<h1>Unrelated</h1><h1 id='job'>Engineer</h1><p>Build &amp; ship.</p>"
    config = {
        "steps": [
            {"tag": "h1", "field": "title"},
            {"tag": "p", "field": "description", "to_end": True, "html": True},
        ],
        "defaults": {"locations": ["Default"]},
        "defaults_by_url": {url: {"locations": ["Zurich"]}},
    }
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(200, text=html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        content = await dom.scrape(url, config, client)
    assert len(calls) == 1
    assert content.title == "Engineer"
    assert content.description == "<p>Build &amp; ship.</p>"
    assert content.locations == ["Zurich"]
    capture = native / "capture-0.json"
    assert stat.S_IMODE(capture.stat().st_mode) == 0o600
    request = json.loads(capture.read_text())
    assert request == {"mode": "parse", "url": url, "html": html, "config": config}
    assert dom_go_parse.parse_html(html, config).title == "Unrelated"


@pytest.mark.asyncio
async def test_frozen_parse_outputs_match_through_async_bridge(
    native_binary, monkeypatch, tmp_path
):
    monkeypatch.setattr(dom_go_parse, "_CAPTURE_PREFIX", str(tmp_path / "capture"))
    fixture = Path(__file__).resolve().parents[1] / "go/dom-detail/testdata/python_cases.json"
    cases = [c for c in json.loads(fixture.read_text()) if c["request"]["mode"] == "parse"]
    for index, case in enumerate(cases):
        request = case["request"]
        if "error" in case:
            with pytest.raises(ValueError):
                await dom_go_parse.parse_fetched_html(
                    request["html"], request["config"], "", binary=native_binary
                )
        else:
            content = await dom_go_parse.parse_fetched_html(
                request["html"], request["config"], request.get("url", ""), binary=native_binary
            )
            assert dataclasses.asdict(content) == case["expected"], index
    assert len(list(tmp_path.glob("capture-*.json"))) == 8


def test_reversal_and_input_bounds(monkeypatch):
    monkeypatch.delenv("DOM_GO_PARSE_ENABLED", raising=False)
    assert dom_go_parse.enabled()
    for value in ["0", "true", "", "invalid"]:
        monkeypatch.setenv("DOM_GO_PARSE_ENABLED", value)
        assert not dom_go_parse.enabled()
    monkeypatch.setattr(dom_go_parse, "_LIMIT", 64)
    with pytest.raises(ValueError, match="input exceeds"):
        dom_go_parse.parse_html("a" * 65, {})


@pytest.mark.asyncio
async def test_cancel_reaps_child(monkeypatch, tmp_path):
    pid = tmp_path / "pid"
    binary = tmp_path / "slow"
    binary.write_text(f"#!/bin/sh\necho $$ > '{pid}'\nexec sleep 30\n")
    binary.chmod(0o700)
    task = asyncio.create_task(dom_go_parse.parse_fetched_html("", {}, "", binary=str(binary)))
    for _ in range(100):
        if pid.exists():
            break
        await asyncio.sleep(0.01)
    assert pid.exists()
    child_id = int(pid.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    import os

    with pytest.raises(ProcessLookupError):
        os.kill(child_id, 0)


@pytest.mark.asyncio
async def test_reject_oversized_child_output(monkeypatch, tmp_path):
    binary = tmp_path / "large"
    binary.write_text("#!/bin/sh\nprintf '%1000s' ''\n")
    binary.chmod(0o700)
    monkeypatch.setattr(dom_go_parse, "_LIMIT", 128)
    with pytest.raises(ValueError, match="output exceeds"):
        await dom_go_parse.parse_fetched_html("", {}, "", binary=str(binary))
