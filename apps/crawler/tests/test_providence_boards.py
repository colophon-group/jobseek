"""Stable provider contracts for Providence ministries."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_providence_board_inventory_is_exact() -> None:
    with _BOARDS.open(newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "providence"
        }

    assert set(rows) == {
        "providence-careers",
        "providence-high-school-careers",
        "providence-university-careers",
    }

    primary = rows["providence-careers"]
    assert primary["board_url"] == (
        "https://evac.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
    )
    assert (primary["monitor_type"], primary["scraper_type"]) == (
        "oracle_hcm",
        "oracle_hcm",
    )

    ministries = {
        "providence-high-school-careers": (
            "ae35d627-5989-4889-beb3-2622e60a5256",
            "19000101_000003",
        ),
        "providence-university-careers": (
            "7b7a7621-2d08-46ed-a694-79735466f015",
            "19000101_000001",
        ),
    }
    for slug, (cid, cc_id) in ministries.items():
        row = rows[slug]
        assert (row["monitor_type"], row["scraper_type"]) == ("adp", "adp")
        assert json.loads(row["monitor_config"]) == {
            "cid": cid,
            "cc_id": cc_id,
            "locale": "en_US",
        }
        assert "description" in json.loads(row["scraper_config"])["enrich"]
