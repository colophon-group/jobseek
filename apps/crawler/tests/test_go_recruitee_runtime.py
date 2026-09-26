"""Selected Recruitee inventory is produced exclusively by the Go runtime."""

from __future__ import annotations

import pytest

from src.core.monitors import BoardGoneError
from src.processing.board import _monitor_runtime_for_board
from src.runtime.recruitee_go import GoRecruiteeMonitorRuntime, percentage_selected

BOARD_ID = "20eae165-5251-40d4-b9a0-0254f4bd1ab3"
BOARD_URL = "https://acme.recruitee.com"
CONFIG = {"slug": "acme", "api_base": BOARD_URL, "scraper_type": "skip"}


def fake_binary(tmp_path, payload: dict, *, exit_code: int = 0) -> str:
    path = tmp_path / "recruitee-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['--tenant', 'acme']\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


@pytest.mark.asyncio
async def test_go_recruitee_emits_rich_job(tmp_path):
    url = f"{BOARD_URL}/o/engineer"
    runtime = GoRecruiteeMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "jobs": [{"url": url, "title": "Engineer", "description": "<p>Build</p>"}],
                "truncated": False,
                "requests": 1,
                "responses": 1,
                "bytes": 100,
                "status": 200,
                "final_url": f"{BOARD_URL}/api/offers",
            },
        ),
        board_id=BOARD_ID,
    )
    result = [item async for item in runtime.stream(BOARD_URL, "recruitee", CONFIG, None)]
    assert len(result) == 1
    assert result[0].urls == {url}
    assert result[0].jobs_by_url[url].description == "<p>Build</p>"


@pytest.mark.asyncio
async def test_go_recruitee_maps_retired_tenant(tmp_path):
    runtime = GoRecruiteeMonitorRuntime(
        fake_binary(
            tmp_path,
            {
                "jobs": [],
                "truncated": False,
                "requests": 1,
                "responses": 1,
                "bytes": 4,
                "status": 404,
                "final_url": f"{BOARD_URL}/api/offers",
                "error_kind": "gone",
                "error": "Recruitee tenant returned HTTP 404",
            },
            exit_code=1,
        ),
        board_id=BOARD_ID,
    )
    with pytest.raises(BoardGoneError):
        async for _ in runtime.stream(BOARD_URL, "recruitee", CONFIG, None):
            pass


def test_dark_default_and_strict_percent(monkeypatch):
    monkeypatch.delenv("RECRUITEE_GO_PERCENT", raising=False)
    monkeypatch.delenv("RECRUITEE_GO_BOARD_IDS", raising=False)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "python"
    monkeypatch.setenv("RECRUITEE_GO_BOARD_IDS", BOARD_ID)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "go-recruitee"
    monkeypatch.delenv("RECRUITEE_GO_BOARD_IDS")
    config = {**CONFIG, "recent_discovered_counts": [4, 4, 4]}
    monkeypatch.setenv("RECRUITEE_GO_PERCENT", "100")
    assert percentage_selected(BOARD_ID, BOARD_URL, config)
    assert not percentage_selected(
        BOARD_ID, BOARD_URL, {**config, "api_base": "https://other.test"}
    )
    assert not percentage_selected(BOARD_ID, BOARD_URL, {**config, "scraper_type": "json-ld"})
    assert not percentage_selected(
        BOARD_ID, BOARD_URL, {**config, "recent_discovered_counts": [4, 4]}
    )
