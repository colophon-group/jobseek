"""Board, fallback, metadata, and asset contracts for Arcadis."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx

from src.core.scrapers.jsonld import scrape

DATA_DIR = Path(__file__).parents[1] / "data"


def _rows(path: str, key: str, value: str) -> list[dict[str, str]]:
    with (DATA_DIR / path).open(newline="") as handle:
        return [row for row in csv.DictReader(handle) if row[key] == value]


def test_arcadis_uses_global_aggregate_and_distinct_dps_board() -> None:
    rows = {row["board_slug"]: row for row in _rows("boards.csv", "company_slug", "arcadis")}

    assert set(rows) == {"arcadis-careers-global", "arcadis-careers-dps"}
    global_board = rows["arcadis-careers-global"]
    assert (global_board["monitor_type"], global_board["scraper_type"]) == (
        "eightfold",
        "eightfold",
    )
    assert json.loads(global_board["monitor_config"]) == {"url_filter": "/careers/job/"}
    assert json.loads(global_board["scraper_config"]) == {"enrich": ["description"]}

    dps = rows["arcadis-careers-dps"]
    assert (dps["monitor_type"], dps["scraper_type"]) == ("sitemap", "json-ld")
    assert json.loads(dps["monitor_config"]) == {
        "sitemap_url": "https://www.dpsgroupglobal.com/sitemap.xml",
        "url_filter": r"^https://www\.dpsgroupglobal\.com/job/[^/?#]+/?$",
    }
    defaults = json.loads(dps["scraper_config"])["defaults_by_url"]
    assert len(defaults) == 5
    assert defaults["https://www.dpsgroupglobal.com/job/structural-designer/"] == {
        "locations": ["Albany, New York, United States"]
    }


async def test_arcadis_dps_jsonld_fills_exact_locationless_posting() -> None:
    dps = _rows("boards.csv", "board_slug", "arcadis-careers-dps")[0]
    config = json.loads(dps["scraper_config"])
    url = "https://www.dpsgroupglobal.com/job/structural-designer/"
    page_html = """<script type="application/ld+json">
    {"@type":"JobPosting","title":"Structural Designer",
     "description":"<p>Design advanced-technology facilities.</p>"}
    </script>"""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=page_html))

    async with httpx.AsyncClient(transport=transport) as client:
        content = await scrape(url, config, client)

    assert content.title == "Structural Designer"
    assert content.description == "<p>Design advanced-technology facilities.</p>"
    assert content.locations == ["Albany, New York, United States"]


def test_arcadis_metadata_descriptions_and_staged_assets() -> None:
    company = _rows("companies.csv", "slug", "arcadis")[0]
    assert company["name"] == "Arcadis"
    assert company["website"] == "https://www.arcadis.com"
    assert company["logo_type"] == "wordmark+icon"
    assert company["industry"] == "12"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "1888"

    description = _rows("company_descriptions.csv", "slug", "arcadis")[0]
    assert all(description[locale] for locale in ("en", "de", "fr", "it"))
    assert (DATA_DIR / "images/arcadis/logo.png").is_file()
    assert (DATA_DIR / "images/arcadis/icon.png").is_file()
