"""Reviewed board contracts for Leidos and its operating companies."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def _rows() -> dict[str, dict[str, str]]:
    with _BOARDS.open(newline="") as handle:
        return {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "leidos"
        }


def test_leidos_board_inventory_is_exact() -> None:
    rows = _rows()

    assert set(rows) == {"leidos-biomed", "leidos-careers", "leidos-qtc"}
    assert rows["leidos-careers"] == {
        "company_slug": "leidos",
        "board_slug": "leidos-careers",
        "board_url": "https://leidos.wd5.myworkdayjobs.com/external",
        "monitor_type": "workday",
        "monitor_config": "",
        "scraper_type": "workday",
        "scraper_config": "",
    }


def test_leidos_biomed_refreshes_the_public_cornerstone_bootstrap() -> None:
    row = _rows()["leidos-biomed"]
    config = json.loads(row["monitor_config"])

    assert row["monitor_type"] == "cornerstone"
    assert row["scraper_type"] == "skip"
    assert config == {
        "tenant": "leidosbiomed",
        "site_id": 4,
        "corp": "leidosbiomed",
        "domain": "csodfed.com",
    }


def test_leidos_qtc_uses_the_verified_ukg_tenant() -> None:
    row = _rows()["leidos-qtc"]
    config = json.loads(row["monitor_config"])
    scraper = json.loads(row["scraper_config"])

    assert row["monitor_type"] == "ukg"
    assert config == {
        "host": "recruiting2.ultipro.com",
        "tenant": "QTC1000QTC",
        "board_id": "401507f4-5ffa-4c84-b89c-0ebfae8d9292",
        "listing_url": row["board_url"],
    }
    assert row["scraper_type"] == "embedded"
    assert scraper["pattern"] == r"new\s+US\.Opportunity\.CandidateOpportunityDetail\s*\("
    assert scraper["enrich"] == ["description"]
