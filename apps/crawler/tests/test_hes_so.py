"""Regression coverage for the HES-SO Rectorat board configuration."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.core.scrapers.dom import parse_html

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BOARD_SLUG = "hes-so-rectorat"


def _fallback_config() -> dict:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == BOARD_SLUG)
    return json.loads(row["scraper_config"])["fallback"]["config"]


def test_dom_fallback_preserves_accented_multi_city_location() -> None:
    result = parse_html(
        """
        <main>
          <h1>Chargé·e de mission</h1>
          <ul><li class="location">Delémont ou Lausanne, CH</li></ul>
          <p>Contribuez aux missions du Rectorat.</p>
        </main>
        """,
        _fallback_config(),
    )

    assert result.locations == ["Delémont ou Lausanne, CH"]
