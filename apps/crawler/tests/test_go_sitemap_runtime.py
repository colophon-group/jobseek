"""The selected Go sitemap keeps the shared URL postprocessing contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.processing.board import _monitor_runtime_for_board
from src.runtime.sitemap_go import GoSitemapMonitorRuntime, eligible

BOARD_URL = "https://jobs.example.com/careers"
SITEMAP_URL = "https://jobs.example.com/sitemap.xml"
CONFIG = {"sitemap_url": SITEMAP_URL, "url_filter": r"/job/", "scraper_type": "json-ld"}


def test_route_is_default_off_and_rejects_unsupported_configuration(monkeypatch):
    monkeypatch.delenv("SITEMAP_GO_BOARD_IDS", raising=False)
    assert (
        _monitor_runtime_for_board(
            "board-a", None, monitor_type="sitemap", board_url=BOARD_URL, monitor_config=CONFIG
        ).implementation
        == "python"
    )
    monkeypatch.setenv("SITEMAP_GO_BOARD_IDS", " board-a, ")
    assert (
        _monitor_runtime_for_board(
            "board-a", None, monitor_type="sitemap", board_url=BOARD_URL, monitor_config=CONFIG
        ).implementation
        == "go-sitemap"
    )
    with pytest.raises(ValueError, match="supported configuration"):
        _monitor_runtime_for_board(
            "board-a",
            None,
            monitor_type="sitemap",
            board_url=BOARD_URL,
            monitor_config={**CONFIG, "proxy": True},
        )
    assert not eligible(BOARD_URL, "sitemap", {**CONFIG, "xml_attempts": 5})
    assert not eligible(
        BOARD_URL, "sitemap", {**CONFIG, "sitemap_url": "http://jobs.example.com/sitemap.xml"}
    )
    assert not eligible(
        BOARD_URL, "sitemap", {**CONFIG, "sitemap_url": "https://evil.example/sitemap.xml"}
    )


@pytest.mark.asyncio
async def test_runtime_applies_python_url_filter_after_go_extraction(tmp_path: Path):
    binary = tmp_path / "sitemap-live-fake"
    payload = {
        "urls": [
            "https://jobs.example.com/job/1",
            "https://jobs.example.com/about",
        ],
        "truncated": False,
        "requests": 1,
        "responses": 1,
        "wire_attempts": 1,
        "response_bytes": 230,
    }
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"assert sys.argv[1:] == ['--sitemap-url', {SITEMAP_URL!r}]\n"
        f"print(json.dumps({payload!r}))\n"
    )
    binary.chmod(0o755)
    runtime = GoSitemapMonitorRuntime(board_id="board-a", binary=str(binary))
    results = [result async for result in runtime.stream(BOARD_URL, "sitemap", CONFIG, None)]
    assert len(results) == 1
    assert results[0].urls == {"https://jobs.example.com/job/1"}
    assert results[0].filtered_count == 1
    assert not results[0].truncated


@pytest.mark.asyncio
async def test_runtime_rejects_inconsistent_request_accounting(tmp_path: Path):
    binary = tmp_path / "sitemap-live-fake"
    binary.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        'print(json.dumps({"urls": [], "truncated": False, "requests": 1, '
        '"responses": 2, "wire_attempts": 1, "response_bytes": 0}))\n'
    )
    binary.chmod(0o755)
    runtime = GoSitemapMonitorRuntime(board_id="board-a", binary=str(binary))
    with pytest.raises(ValueError, match="request accounting"):
        async for _ in runtime.stream(BOARD_URL, "sitemap", CONFIG, None):
            pass
