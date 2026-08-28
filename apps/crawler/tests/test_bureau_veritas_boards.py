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
        assert row["scraper_type"] == "dom"
        scraper = json.loads(row["scraper_config"])
        assert scraper["proxy"] is True
        assert scraper["render"] is True
        assert [step["field"] for step in scraper["steps"]] == [
            "title",
            "description",
            "locations",
        ]
