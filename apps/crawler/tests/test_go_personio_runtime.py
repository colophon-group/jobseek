"""The default-off Personio route preserves inventory and rich job fields."""

from __future__ import annotations

import httpx
import pytest

from src.processing.board import _monitor_runtime_for_board
from src.runtime.personio_go import GoPersonioMonitorRuntime, percentage_selected

BOARD_ID = "20eae165-5251-40d4-b9a0-0254f4bd1ab3"
BOARD_URL = "https://acme.jobs.personio.de/"
CONFIG = {"slug": "acme", "scraper_type": "skip"}


def fake_binary(tmp_path, payload: dict, *, exit_code: int = 0) -> str:
    path = tmp_path / "personio-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['--slug', 'acme', '--domain', 'de', '--language', "
        "'en', '--backfill-languages', 'de']\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


def payload(jobs: list[dict], *, status: int = 200, error: str | None = None) -> dict:
    return {
        "jobs": jobs,
        "truncated": False,
        "requests": 2,
        "responses": 2,
        "bytes": 300,
        "status": status,
        "final_url": "https://acme.jobs.personio.de/xml?language=en",
        "error": error,
    }


@pytest.mark.asyncio
async def test_go_personio_preserves_rich_fields_and_shared_filter(tmp_path):
    url = "https://acme.jobs.personio.de/job/42"
    rich = {
        "url": url,
        "title": "Engineer",
        "description": "<h3>Role</h3>\n<p>Build</p>",
        "locations": ["Zurich"],
        "employment_type": "full-time",
        "language": "en",
        "localizations": {"en": {"title": "Engineer"}, "de": {"title": "Ingenieur"}},
        "metadata": {"id": "42", "department": "Engineering"},
    }
    runtime = GoPersonioMonitorRuntime(
        fake_binary(tmp_path, payload([rich, {"url": "https://elsewhere.test/job/1"}])),
        board_id=BOARD_ID,
    )
    config = {**CONFIG, "url_allowlist": r"^https://acme\.jobs\.personio\.de/job/[0-9]+$"}
    async with httpx.AsyncClient() as client:
        result = [item async for item in runtime.stream(BOARD_URL, "personio", config, client)]
    assert len(result) == 1
    assert result[0].urls == {url}
    assert result[0].security_filtered_count == 1
    assert result[0].jobs_by_url[url].description == rich["description"]
    assert result[0].jobs_by_url[url].localizations == rich["localizations"]


@pytest.mark.asyncio
async def test_go_personio_emits_authoritative_empty_result(tmp_path):
    runtime = GoPersonioMonitorRuntime(fake_binary(tmp_path, payload([])), board_id=BOARD_ID)
    async with httpx.AsyncClient() as client:
        result = [item async for item in runtime.stream(BOARD_URL, "personio", CONFIG, client)]
    assert len(result) == 1
    assert result[0].urls == set()


@pytest.mark.asyncio
async def test_go_personio_failure_cannot_publish_partial_jobs(tmp_path):
    runtime = GoPersonioMonitorRuntime(
        fake_binary(
            tmp_path,
            payload([{"url": "https://acme.jobs.personio.de/job/42"}], error="XML unavailable"),
            exit_code=1,
        ),
        board_id=BOARD_ID,
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="XML unavailable"):
            async for _ in runtime.stream(BOARD_URL, "personio", CONFIG, client):
                pass


def test_go_personio_default_dark_and_config_guard(monkeypatch):
    monkeypatch.delenv("PERSONIO_GO_PERCENT", raising=False)
    monkeypatch.delenv("PERSONIO_GO_BOARD_IDS", raising=False)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "python"
    monkeypatch.setenv("PERSONIO_GO_BOARD_IDS", BOARD_ID)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "go-personio"
    monkeypatch.delenv("PERSONIO_GO_BOARD_IDS")
    monkeypatch.setenv("PERSONIO_GO_PERCENT", "100")
    selected = {**CONFIG, "recent_discovered_counts": [4, 4, 4]}
    assert percentage_selected(BOARD_ID, BOARD_URL, selected)
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**selected, "language": "EN"})
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**selected, "variant": "custom"})
    assert not percentage_selected(BOARD_ID, "https://other.test/", selected)
    assert not percentage_selected(
        BOARD_ID, BOARD_URL, {**selected, "recent_discovered_counts": [4, 4]}
    )
