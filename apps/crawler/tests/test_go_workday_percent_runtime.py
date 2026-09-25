"""Stable Workday cohorts preserve the Go adapter's narrow eligibility."""

from __future__ import annotations

from src.processing.board import _monitor_runtime_for_board
from src.runtime.workday_go import percentage_selected

BOARD_ID = "20eae165-5251-40d4-b9a0-0254f4bd1ab3"
URL = "https://swisslife.wd3.myworkdayjobs.com/CH"
CONFIG = {
    "company": "swisslife",
    "wd_instance": "wd3",
    "site": "CH",
    "all_sites": False,
    "scraper_type": "workday",
    "recent_discovered_counts": [131, 131, 131],
}


def test_percentage_is_dark_by_default_and_uses_stable_board_id(monkeypatch):
    monkeypatch.delenv("WORKDAY_GO_PERCENT", raising=False)
    assert not percentage_selected(BOARD_ID, URL, CONFIG)
    monkeypatch.setenv("WORKDAY_GO_PERCENT", "5")
    assert percentage_selected(BOARD_ID, URL, CONFIG)
    assert (
        _monitor_runtime_for_board(
            BOARD_ID,
            None,
            monitor_type="workday",
            board_url=URL,
            monitor_config=CONFIG,
        ).implementation
        == "go-workday"
    )
    assert not percentage_selected("another-board", URL, CONFIG)


def test_percentage_requires_adapter_compatible_recent_config(monkeypatch):
    monkeypatch.setenv("WORKDAY_GO_PERCENT", "100")
    assert percentage_selected(BOARD_ID, URL, CONFIG)
    for bad_url, bad_config in (
        (URL.replace("https:", "http:"), CONFIG),
        (URL, {**CONFIG, "all_sites": True}),
        (URL, {**CONFIG, "company": "other"}),
        (URL, {**CONFIG, "proxy": True}),
        (URL, {**CONFIG, "recent_discovered_counts": [1, 2]}),
        (URL, {**CONFIG, "recent_discovered_counts": [1, True, 2]}),
        (URL, {**CONFIG, "recent_discovered_counts": [1, 2, 1900]}),
    ):
        assert not percentage_selected(BOARD_ID, bad_url, bad_config)


def test_percentage_setting_must_be_canonical(monkeypatch):
    for invalid in ("", "01", "101", "-1", "5.0", " 5", "five"):
        monkeypatch.setenv("WORKDAY_GO_PERCENT", invalid)
        assert not percentage_selected(BOARD_ID, URL, CONFIG)
