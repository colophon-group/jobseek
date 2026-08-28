"""Board contracts for Quest Diagnostics and its acquired companies."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_quest_diagnostics_uses_three_complementary_boards() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "quest-diagnostics"]

    assert [row["board_slug"] for row in rows] == [
        "quest-diagnostics-careers",
        "quest-diagnostics-careers-blueprint-genetics",
        "quest-diagnostics-careers-lifelabs",
    ]

    primary, blueprint, lifelabs = rows
    assert primary["monitor_type"] == primary["scraper_type"] == "oracle_hcm"
    assert json.loads(primary["scraper_config"]) == {"enrich": ["description"]}

    assert blueprint["monitor_type"] == "rss"
    assert blueprint["scraper_type"] == "skip"
    assert json.loads(blueprint["monitor_config"]) == {
        "preset": "teamtailor",
        "feed_url": "https://careers.blueprintgenetics.com/jobs.rss",
    }

    assert lifelabs["monitor_type"] == "dayforce"
    assert lifelabs["scraper_type"] == "skip"
    assert json.loads(lifelabs["monitor_config"]) == {
        "tenant": "lifelabs",
        "portal": "CANDIDATEPORTAL",
        "offset_overlap": 10,
    }
