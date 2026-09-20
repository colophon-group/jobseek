from __future__ import annotations

import csv
import json
from pathlib import Path


def test_alten_cloudflare_boards_use_stealth_and_proxy_for_monitor_and_scraper() -> None:
    boards_path = Path(__file__).parents[1] / "data" / "boards.csv"
    with boards_path.open(encoding="utf-8", newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["board_slug"]
            in {
                "alten-belgium",
                "alten-finland",
                "alten-italy",
                "alten-netherlands",
                "alten-portugal",
                "alten-switzerland",
            }
        }

    assert set(rows) == {
        "alten-belgium",
        "alten-finland",
        "alten-italy",
        "alten-netherlands",
        "alten-portugal",
        "alten-switzerland",
    }
    for row in rows.values():
        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])
        assert monitor_config["render"] is True
        assert monitor_config["stealth"] is True
        assert monitor_config["proxy"] is True
        assert monitor_config["transport_attempts"] == 3
        assert scraper_config["render"] is True
        assert scraper_config["stealth"] is True
        assert scraper_config["proxy"] is True
        assert scraper_config["transport_attempts"] == 3


def test_sage_browser_replay_uses_stealth_and_proxy() -> None:
    boards_path = Path(__file__).parents[1] / "data" / "boards.csv"
    with boards_path.open(encoding="utf-8", newline="") as handle:
        row = next(
            row for row in csv.DictReader(handle) if row["board_slug"] == "sage-careers-search"
        )

    monitor_config = json.loads(row["monitor_config"])
    assert monitor_config["browser"] is True
    assert monitor_config["stealth"] is True
    assert monitor_config["proxy"] is True
    assert monitor_config["transport_attempts"] == 3


def test_hd_centre_browser_paths_retry_blocked_proxy_exits() -> None:
    boards_path = Path(__file__).parents[1] / "data" / "boards.csv"
    with boards_path.open(encoding="utf-8", newline="") as handle:
        row = next(
            row
            for row in csv.DictReader(handle)
            if row["board_slug"] == "centre-for-humanitarian-dialogue-careers"
        )

    monitor_config = json.loads(row["monitor_config"])
    scraper_config = json.loads(row["scraper_config"])
    assert monitor_config["proxy"] is True
    assert monitor_config["transport_attempts"] == 3
    assert scraper_config["proxy"] is True
    assert scraper_config["transport_attempts"] == 3
