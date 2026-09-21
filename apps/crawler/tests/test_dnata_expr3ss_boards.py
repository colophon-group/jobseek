from __future__ import annotations

import csv
import json
import re
from pathlib import Path


def test_dnata_australia_expr3ss_boards_use_canonical_headful_proxy_paths() -> None:
    boards_path = Path(__file__).parents[1] / "data" / "boards.csv"
    with boards_path.open(encoding="utf-8", newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["board_slug"]
            in {
                "emirates-group-dnata-au",
                "emirates-group-dnata-catering-au",
            }
        }

    assert set(rows) == {
        "emirates-group-dnata-au",
        "emirates-group-dnata-catering-au",
    }
    expected_scraper_config = {
        "render": True,
        "proxy": True,
        "transport_attempts": 5,
        "channel": "chrome",
        "headless": False,
        "stealth": True,
        "wait": "domcontentloaded",
        "timeout": 30000,
    }
    for row in rows.values():
        host = row["board_url"].split("/", 3)[2]
        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])

        assert monitor_config["render"] is True
        assert monitor_config["proxy"] is True
        assert monitor_config["transport_attempts"] == 5
        assert monitor_config["channel"] == "chrome"
        assert monitor_config["headless"] is False
        assert monitor_config["stealth"] is True
        assert monitor_config["url_filter"].startswith(f"^https://{re.escape(host)}")

        transform = monitor_config["url_transform"]
        expected = f"https://{host}/jobDetailsModern?selectJob=1208&s=250&modern=1"
        aliases = [
            f"https://{host}/jobDetailsModern?selectJob=1208&ppt=deadbeef&modern=1",
            f"https://{host}/jobDetailsModern?selectJob=1208&s=248&modern=1",
            f"https://{host}/jobDetailsModern?selectJob=1208&s=249&modern=1",
            f"https://{host}/jobDetailsModern?selectJob=1208&s=250&modern=1",
        ]
        assert {re.sub(transform["find"], transform["replace"], alias) for alias in aliases} == {
            expected
        }

        assert {
            key: scraper_config[key] for key in expected_scraper_config
        } == expected_scraper_config

    catering_config = json.loads(rows["emirates-group-dnata-catering-au"]["scraper_config"])
    assert catering_config == expected_scraper_config

    main_config = json.loads(rows["emirates-group-dnata-au"]["scraper_config"])
    assert set(main_config) == {*expected_scraper_config, "actions"}
    assert len(main_config["actions"]) == 1
    assert main_config["actions"][0]["action"] == "evaluate"
    detail_adapter = main_config["actions"][0]["script"]
    assert "#job-details-title" in detail_adapter
    assert "#adCopyContainer" in detail_adapter
    assert ".application-sidebar__row span" in detail_adapter
    assert "var\\s+mapData" in detail_adapter
    assert "mapCenter" in detail_adapter
    assert "missing required title or description" in detail_adapter
    assert "missing a supported posted date" in detail_adapter
    assert "script.type = 'application/ld+json'" in detail_adapter
    assert "script.textContent = JSON.stringify(posting)" in detail_adapter
