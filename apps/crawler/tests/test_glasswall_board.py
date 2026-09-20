from __future__ import annotations

import csv
import json
from pathlib import Path


def test_glasswall_browser_paths_retry_blocked_proxy_exits() -> None:
    boards_path = Path(__file__).parents[1] / "data" / "boards.csv"
    with boards_path.open(encoding="utf-8", newline="") as handle:
        row = next(
            row for row in csv.DictReader(handle) if row["board_slug"] == "glasswall-careers"
        )

    monitor_config = json.loads(row["monitor_config"])
    scraper_config = json.loads(row["scraper_config"])
    for config in (monitor_config, scraper_config):
        assert config["render"] is True
        assert config["stealth"] is True
        assert config["proxy"] is True
        assert config["transport_attempts"] == 3
