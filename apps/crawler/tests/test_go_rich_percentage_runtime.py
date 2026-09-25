"""Deterministic rich ATS handoff without moving queue due scores."""

from __future__ import annotations

import hashlib

from src.processing.board import _go_rich_percentage_selected, _monitor_runtime_for_board


def test_percentage_is_dark_by_default_and_requires_strict_config(monkeypatch):
    board_id = "84bdbbe1-9b6d-49d3-b3e8-f51913f278e9"
    url = "https://job-boards.greenhouse.io/lucidbots"
    config = {"token": "lucidbots", "scraper_type": "skip"}
    monkeypatch.delenv("GREENHOUSE_GO_PERCENT", raising=False)
    assert (
        _monitor_runtime_for_board(
            board_id, None, monitor_type="greenhouse", board_url=url, monitor_config=config
        ).implementation
        == "python"
    )

    monkeypatch.setenv("GREENHOUSE_GO_PERCENT", "100")
    assert (
        _monitor_runtime_for_board(
            board_id, None, monitor_type="greenhouse", board_url=url, monitor_config=config
        ).implementation
        == "go-greenhouse"
    )
    for changed_url, changed_config in (
        ("https://example.org/jobs", config),
        ("https://job-boards.greenhouse.io:bad/lucidbots", config),
        (url, {**config, "proxy": True}),
        (url, {**config, "scraper_type": "json-ld"}),
        (url, {**config, "token": "../escape"}),
    ):
        assert (
            _monitor_runtime_for_board(
                board_id,
                None,
                monitor_type="greenhouse",
                board_url=changed_url,
                monitor_config=changed_config,
            ).implementation
            == "python"
        )


def test_percentage_bucket_is_stable_and_malformed_setting_stays_python(monkeypatch):
    url = "https://jobs.ashbyhq.com/forerunner"
    config = {"token": "forerunner", "scraper_type": "skip"}
    for bad in ("", "01", "101", "-1", "1.5", "true"):
        monkeypatch.setenv("ASHBY_GO_PERCENT", bad)
        assert not _go_rich_percentage_selected("board-a", "ashby", url, config)
    monkeypatch.setenv("ASHBY_GO_PERCENT", "1")
    selected = [
        f"board-{index}"
        for index in range(1000)
        if _go_rich_percentage_selected(f"board-{index}", "ashby", url, config)
    ]
    assert 1 <= len(selected) <= 30
    first = selected[0]
    bucket = int.from_bytes(hashlib.sha256(first.encode()).digest()[:8], "big") % 10_000
    assert bucket < 100
    assert _go_rich_percentage_selected(first, "ashby", url, config)


def test_lever_eu_requires_matching_region(monkeypatch):
    monkeypatch.setenv("LEVER_GO_PERCENT", "100")
    board_id = "3bba23a0-5e4c-4928-9173-0d59ec0d44b8"
    url = "https://jobs.eu.lever.co/quantinuum"
    config = {"token": "quantinuum", "region": "eu", "scraper_type": "skip"}
    assert (
        _monitor_runtime_for_board(
            board_id, None, monitor_type="lever", board_url=url, monitor_config=config
        ).implementation
        == "go-lever"
    )
    assert (
        _monitor_runtime_for_board(
            board_id,
            None,
            monitor_type="lever",
            board_url=url,
            monitor_config={**config, "region": ""},
        ).implementation
        == "python"
    )
