"""Board contracts for Alloheim and its Katharinenhof subsidiary."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_alloheim_uses_complementary_primary_and_katharinenhof_boards() -> None:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "alloheim"]

    assert [row["board_slug"] for row in rows] == [
        "alloheim-careers",
        "alloheim-katharinenhof",
    ]

    primary, katharinenhof = rows
    assert primary["monitor_type"] == "sitemap"
    assert primary["scraper_type"] == "json-ld"
    assert json.loads(primary["monitor_config"]) == {
        "sitemap_url": "https://alloheim.career.softgarden.de/sitemap.xml",
        "url_filter": r"career\.softgarden\.de/.+",
    }

    assert katharinenhof["monitor_type"] == "softgarden"
    assert katharinenhof["scraper_type"] == "json-ld"
    assert json.loads(katharinenhof["monitor_config"]) == {"slug": "katharinenhofbetriebsgmbh"}
