"""Stable provider contracts for Bureau Veritas job boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_bureau_veritas_board_inventory_and_proxy_boundaries_are_exact() -> None:
    with _BOARDS.open(newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "bureau-veritas"
        }

    assert set(rows) == {
        "bureau-veritas-canada-labs",
        "bureau-veritas-global",
        "bureau-veritas-japan",
        "bureau-veritas-taiwan",
        "bureau-veritas-taiwan-cps",
    }
    assert (
        rows["bureau-veritas-canada-labs"]["monitor_type"],
        rows["bureau-veritas-canada-labs"]["scraper_type"],
    ) == ("jazzhr", "jazzhr")
    assert json.loads(rows["bureau-veritas-global"]["monitor_config"]) == {
        "preset": "successfactors",
        "feed_url": "https://careers.bureauveritas.com/googlefeed.xml",
    }
    assert (
        rows["bureau-veritas-japan"]["monitor_type"],
        rows["bureau-veritas-japan"]["scraper_type"],
    ) == ("hrmos", "json-ld")

    for slug, token in {
        "bureau-veritas-taiwan": "auzu36g",
        "bureau-veritas-taiwan-cps": "a3upryg",
    }.items():
        row = rows[slug]
        assert json.loads(row["monitor_config"]) == {"token": token, "proxy": True}
        assert row["scraper_type"] == "api_sniffer"
        scraper = json.loads(row["scraper_config"])
        assert set(scraper) == {
            "api_url",
            "url_pattern",
            "json_path",
            "request_headers",
            "fields",
            "enrich",
        }
        assert scraper["api_url"] == "https://www.104.com.tw/job/ajax/content/{id}"
        assert scraper["json_path"] == (
            "data | [@][?switch == 'on' && header.custUrl == `"
            + json.dumps(row["board_url"])
            + "` && jobDetail.jobDescription] | [0]"
        )
        assert scraper["request_headers"] == {
            "Referer": "https://www.104.com.tw/",
            "Accept": "application/json",
        }
        assert scraper["enrich"] == ["description"]
        assert scraper["fields"] == {
            "title": "header.jobName",
            "description": [
                "=<h2>工作內容</h2>",
                "jobDetail.jobDescription",
                "=<h2>其他條件</h2>",
                "condition.other",
                "=<h2>公司福利</h2>",
                "welfare.welfare",
            ],
            "locations": {
                "concat": ["jobDetail.addressRegion", "jobDetail.addressDetail"],
                "separator": "",
            },
        }
