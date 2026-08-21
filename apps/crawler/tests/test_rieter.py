"""Regression coverage for Rieter's global and acquired-division boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA = Path(__file__).parents[1] / "data"


def test_rieter_boards_preserve_static_listing_locations_and_barmag_coverage():
    with (DATA / "boards.csv").open(newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "rieter"
        }

    assert set(rows) == {"rieter-barmag-successfactors", "rieter-global-solique"}
    barmag = rows["rieter-barmag-successfactors"]
    assert barmag["monitor_type"] == "rss"
    assert json.loads(barmag["monitor_config"]) == {
        "preset": "successfactors",
        "feed_url": "https://careers.barmag.com/googlefeed.xml",
    }

    solique = rows["rieter-global-solique"]
    monitor = json.loads(solique["monitor_config"])
    scraper = json.loads(solique["scraper_config"])
    assert monitor["render"] is False
    assert monitor["rich_rows"] == {
        "row_selector": ".job-content .job",
        "link_selector": ".job-title a",
        "location_selectors": [".job-location", ".job-country"],
    }
    assert scraper["render"] is False
    assert scraper["enrich"] == ["description"]
    assert "wait" not in scraper
    assert "timeout" not in scraper
    assert "stealth" not in scraper


def test_rieter_company_uses_canonical_wikidata_identity():
    with (DATA / "companies.csv").open(newline="") as handle:
        row = next(row for row in csv.DictReader(handle) if row["slug"] == "rieter")

    assert json.loads(row["extras"]) == {"wikidataId": "Q681782"}
