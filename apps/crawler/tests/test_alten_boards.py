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
                "alten-spain",
                "alten-switzerland",
            }
        }

    assert set(rows) == {
        "alten-belgium",
        "alten-finland",
        "alten-italy",
        "alten-netherlands",
        "alten-portugal",
        "alten-spain",
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


def test_alten_spain_uses_live_inventory_and_optional_upstream_location() -> None:
    boards_path = Path(__file__).parents[1] / "data" / "boards.csv"
    with boards_path.open(encoding="utf-8", newline="") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == "alten-spain")

    monitor_config = json.loads(row["monitor_config"])
    inventory_guard = monitor_config["actions"][-1]["script"]
    assert "page advertises" in inventory_guard
    assert "n<90" not in inventory_guard

    scraper_config = json.loads(row["scraper_config"])
    assert "missing job date" in scraper_config["actions"][0]["script"]
    location_step = next(
        step for step in scraper_config["steps"] if step.get("field") == "locations"
    )
    assert location_step["optional"] is True


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
