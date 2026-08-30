"""Board contracts for the ALDI SOUTH / HOFER global careers footprint."""

from __future__ import annotations

import csv
import json

from src.shared.constants import DATA_DIR


def _boards() -> dict[str, dict[str, str]]:
    with (DATA_DIR / "boards.csv").open(encoding="utf-8", newline="") as handle:
        return {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "aldi-hofer"
        }


def test_aldi_hofer_keeps_every_distinct_regional_board() -> None:
    assert set(_boards()) == {
        "aldi-hofer-careers",
        "aldi-hofer-careers-au",
        "aldi-hofer-careers-cn",
        "aldi-hofer-careers-de",
        "aldi-hofer-careers-hk",
        "aldi-hofer-careers-ie",
        "aldi-hofer-careers-uk",
        "aldi-hofer-careers-us",
    }


def test_waf_gated_uk_and_ireland_use_proxy_routed_earcu_feeds() -> None:
    boards = _boards()
    feed_hosts = {
        "ie": "careers.aldirecruitment.ie",
        "uk": "careers.aldirecruitment.co.uk",
    }
    for country, host in feed_hosts.items():
        board = boards[f"aldi-hofer-careers-{country}"]
        config = json.loads(board["monitor_config"])
        assert board["monitor_type"] == "earcu"
        assert board["scraper_type"] == "skip"
        assert config == {
            "feed_url": (f"https://{host}/vacancies/allvacancies/"),
            "proxy": True,
        }


def test_regional_board_filters_preserve_only_public_job_pages() -> None:
    boards = _boards()

    australia = json.loads(boards["aldi-hofer-careers-au"]["monitor_config"])
    assert australia["url_filter"] == "/job/"
    assert boards["aldi-hofer-careers-au"]["scraper_type"] == "json-ld"

    china = json.loads(boards["aldi-hofer-careers-cn"]["monitor_config"])
    assert china["url_filter"] == "/joinus/[0-9]+/?$"
    assert china["url_transform"] == {
        "find": r"https://website-cms\.internal\.aldi\.cn",
        "replace": "https://www.aldi.com.cn",
    }


def test_shared_and_german_successfactors_feeds_are_distinct() -> None:
    boards = _boards()
    shared = json.loads(boards["aldi-hofer-careers"]["monitor_config"])
    germany = json.loads(boards["aldi-hofer-careers-de"]["monitor_config"])

    assert shared["feed_url"] == "https://jobs.aldi-hofer.com/googlefeed.xml"
    assert germany["feed_url"] == "https://jobs.aldi-sued.de/googlefeed.xml"
    assert shared["feed_url"] != germany["feed_url"]
