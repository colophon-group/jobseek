from __future__ import annotations

import csv
import json
from pathlib import Path


def test_alten_cloudflare_boards_use_stealth_for_monitor_and_scraper() -> None:
    boards_path = Path(__file__).parents[1] / "data" / "boards.csv"
    with boards_path.open(encoding="utf-8", newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["board_slug"] in {"alten-italy", "alten-portugal"}
        }

    assert set(rows) == {"alten-italy", "alten-portugal"}
    for row in rows.values():
        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])
        assert monitor_config["render"] is True
        assert monitor_config["stealth"] is True
        assert scraper_config["render"] is True
        assert scraper_config["stealth"] is True
