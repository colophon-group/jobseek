"""Stable provider contracts for Sopra Steria group job boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_DATA = Path(__file__).parents[1] / "data"


def test_sopra_steria_board_inventory_is_exact() -> None:
    with (_DATA / "boards.csv").open(newline="") as handle:
        rows = {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "sopra-steria"
        }

    assert set(rows) == {
        "sopra-steria-careers",
        "sopra-steria-careers-uk",
        "sopra-steria-digital-product-simulation",
        "sopra-steria-nexova",
        "sopra-steria-starion",
    }

    global_board = rows["sopra-steria-careers"]
    assert (global_board["monitor_type"], global_board["scraper_type"]) == (
        "sitemap",
        "json-ld",
    )
    assert json.loads(global_board["monitor_config"]) == {
        "sitemap_url": "https://careers.soprasteria.com/sitemap.xml",
        "url_filter": "/job/",
    }

    uk = rows["sopra-steria-careers-uk"]
    assert (uk["monitor_type"], uk["scraper_type"]) == ("sitemap", "embedded")
    assert json.loads(uk["scraper_config"])["variable"] == "phApp.ddo"

    assert rows["sopra-steria-digital-product-simulation"]["scraper_type"] == ("json-ld")
    assert rows["sopra-steria-nexova"]["scraper_type"] == "dom"
    assert rows["sopra-steria-starion"]["monitor_type"] == "sitemap"


def test_sopra_steria_keeps_finalized_assets_out_of_staging() -> None:
    with (_DATA / "companies.csv").open(newline="") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "sopra-steria")

    assert "logo-84362e78f566" in company["logo_url"]
    assert "icon-b5e2c7653e35" in company["icon_url"]
    assert not (_DATA / "images" / "sopra-steria").exists()
