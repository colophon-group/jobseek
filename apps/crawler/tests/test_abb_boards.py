"""ABB board inventory contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_abb_uses_one_primary_inventory_and_verified_subsidiary_boards() -> None:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "abb"]

    assert [row["board_slug"] for row in rows] == [
        "abb-brightloop",
        "abb-emobility",
        "abb-eve",
        "abb-foedisch",
        "abb-premium-power",
        "abb-sensorfact",
        "abb-workday",
    ]
    by_slug = {row["board_slug"]: row for row in rows}

    assert by_slug["abb-brightloop"]["monitor_type"] == "sitemap"
    assert by_slug["abb-brightloop"]["scraper_type"] == "json-ld"
    assert by_slug["abb-emobility"]["monitor_type"] == "rss"
    assert json.loads(by_slug["abb-emobility"]["monitor_config"])["feed_url"] == (
        "https://careers.abb-emobility.com/googlefeed.xml"
    )
    assert by_slug["abb-eve"]["monitor_type"] == "sitemap"
    assert by_slug["abb-eve"]["scraper_type"] == "dom"
    assert by_slug["abb-foedisch"]["monitor_type"] == "inline"
    assert by_slug["abb-foedisch"]["scraper_type"] == "skip"
    assert by_slug["abb-premium-power"]["monitor_type"] == "sitemap"
    assert by_slug["abb-premium-power"]["scraper_type"] == "dom"
    assert by_slug["abb-sensorfact"]["monitor_type"] == "ashby"
    assert by_slug["abb-sensorfact"]["scraper_type"] == "skip"

    primary = by_slug["abb-workday"]
    assert primary["board_url"] == "https://abb.wd3.myworkdayjobs.com/External_Career_Page"
    assert primary["monitor_type"] == "workday"
    assert primary["scraper_type"] == "workday"
    assert json.loads(primary["scraper_config"]) == {"proxy": True}
