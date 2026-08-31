"""Board, fallback, metadata, and asset URL contracts for Arcadis."""

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


def test_arcadis_keeps_complete_discovery_and_dps_fallbacks() -> None:
    rows = {row["board_slug"]: row for row in _rows("boards.csv", "company_slug", "arcadis")}

    assert set(rows) == {"arcadis-careers", "arcadis-dps", "arcadis-kua-dc"}
    global_board = rows["arcadis-careers"]
    assert (global_board["monitor_type"], global_board["scraper_type"]) == (
        "eightfold",
        "eightfold",
    )
    assert json.loads(global_board["monitor_config"]) == {"url_filter": "/careers/job/"}
    assert json.loads(global_board["scraper_config"]) == {"enrich": ["description"]}

    dps = rows["arcadis-dps"]
    assert (dps["monitor_type"], dps["scraper_type"]) == ("api_sniffer", "json-ld")
    monitor_config = json.loads(dps["monitor_config"])
    assert monitor_config["api_url"] == ("https://www.dpsgroupglobal.com/jm-ajax/get_listings/")
    assert monitor_config["pagination"]["page_size"] == 100
    defaults = json.loads(dps["scraper_config"])["defaults_by_url"]
    assert len(defaults) == 5
    assert defaults["https://www.dpsgroupglobal.com/job/structural-designer/"] == {
        "locations": ["Albany, New York, United States"]
    }


async def test_arcadis_dps_jsonld_fills_exact_locationless_posting() -> None:
    dps = _rows("boards.csv", "board_slug", "arcadis-dps")[0]
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


def test_arcadis_metadata_descriptions_and_uploaded_assets() -> None:
    company = _rows("companies.csv", "slug", "arcadis")[0]
    assert company["name"] == "Arcadis"
    assert company["website"] == "https://www.arcadis.com"
    assert company["logo_type"] == "wordmark+icon"
    assert company["industry"] == "12"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "1888"

    description = _rows("company_descriptions.csv", "slug", "arcadis")[0]
    assert all(description[locale] for locale in ("en", "de", "fr", "it"))
    assert company["logo_url"].startswith(
        "https://jobseek-assets.colophon-group.org/companies/arcadis/logo-"
    )
    assert company["icon_url"].startswith(
        "https://jobseek-assets.colophon-group.org/companies/arcadis/icon-"
    )
