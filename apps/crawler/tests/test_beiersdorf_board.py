from __future__ import annotations

import csv
import json
from pathlib import Path


def test_beiersdorf_brazil_rotates_blocked_static_proxy_exits() -> None:
    boards_path = Path(__file__).parents[1] / "data" / "boards.csv"
    with boards_path.open(encoding="utf-8", newline="") as handle:
        row = next(
            row for row in csv.DictReader(handle) if row["board_slug"] == "beiersdorf-brazil-vagas"
        )

    monitor_config = json.loads(row["monitor_config"])
    scraper_config = json.loads(row["scraper_config"])
    assert monitor_config["proxy"] is True
    assert monitor_config["pagination"]["transient_403"] is True
    assert scraper_config["proxy"] is True
    assert scraper_config["transport_attempts"] == 5
