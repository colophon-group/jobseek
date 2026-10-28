from __future__ import annotations

import csv
import json
from pathlib import Path


def test_alten_boards_keep_monitor_proxy_and_qualified_detail_transports() -> None:
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
        if row["board_slug"] in {"alten-spain", "alten-finland"}:
            assert scraper_config["proxy"] is False
            assert {
                "stealth",
                "headless",
                "channel",
                "persistent_context",
                "transport_attempts",
            }.isdisjoint(scraper_config)
        else:
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
    assert monitor_config["drop_threshold"] == 0.5
    assert monitor_config["delist_threshold"] == 1

    scraper_config = json.loads(row["scraper_config"])
    assert "missing job date" in scraper_config["actions"][0]["script"]
    location_step = next(
        step for step in scraper_config["steps"] if step.get("field") == "locations"
    )
    assert location_step["optional"] is True


def test_sage_http_route_retains_the_complete_api_projection() -> None:
    boards_path = Path(__file__).parents[1] / "data" / "boards.csv"
    with boards_path.open(encoding="utf-8", newline="") as handle:
        row = next(
            row for row in csv.DictReader(handle) if row["board_slug"] == "sage-careers-search"
        )

    monitor_config = json.loads(row["monitor_config"])
    assert row["monitor_type"] == "api_sniffer"
    assert row["scraper_type"] == "skip"
    assert not {"browser", "stealth", "proxy"}.intersection(monitor_config)
    assert monitor_config.get("transport_attempts", 3) == 3
    assert monitor_config["api_url"] == (
        "https://www.sage.com/api/sagedotcom/CareerSearch/GetCareerSearchData/"
    )
    assert monitor_config["json_path"] == "vacancies.Records"
    assert monitor_config["url_field"] == "Url"
    assert monitor_config["fields"] == {
        "title": "Name",
        "description": "Description",
        "locations": "OfficeLocation",
        "date_posted": "ActiveDate",
        "metadata.department": "Function",
        "metadata.country": "Country",
    }


def test_hd_centre_retains_monitor_proxy_and_complete_direct_detail_range() -> None:
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
    assert scraper_config["render"] is True
    assert scraper_config["proxy"] is False
    assert {"stealth", "transport_attempts"}.isdisjoint(scraper_config)
    assert scraper_config["include_header_content"] is True
    description = next(step for step in scraper_config["steps"] if step["field"] == "description")
    assert description["text"] == "Share"
    assert description["offset"] == 1
    assert description["to_end"] is True
    assert "stop" not in description
