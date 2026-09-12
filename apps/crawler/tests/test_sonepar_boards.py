"""Configuration contracts for Sonepar's global and Hungarian boards."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitors.inline import discover

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _rows(filename: str, key: str, value: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row[key] == value]


def test_sonepar_metadata_and_assets_are_complete() -> None:
    company = _rows("companies.csv", "slug", "sonepar")[0]
    description = _rows("company_descriptions.csv", "slug", "sonepar")[0]

    assert company["name"] == "Sonepar"
    assert company["industry"] == "5"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "1969"
    assert all(description[locale] for locale in ("en", "de", "fr", "it"))
    asset_root = "https://jobseek-assets.colophon-group.org/companies/sonepar/"
    assert company["logo_url"].startswith(asset_root)
    assert company["icon_url"].startswith(asset_root)


def test_sonepar_boards_use_global_feed_and_pdf_enrichment() -> None:
    boards = {row["board_slug"]: row for row in _rows("boards.csv", "company_slug", "sonepar")}

    assert set(boards) == {"sonepar-careers", "sonepar-hungary"}
    assert json.loads(boards["sonepar-careers"]["monitor_config"]) == {"preset": "successfactors"}
    hungary = boards["sonepar-hungary"]
    assert hungary["monitor_type"] == "inline"
    assert hungary["scraper_type"] == "pdf"
    assert json.loads(hungary["scraper_config"])["enrich"] == ["description"]


@pytest.mark.asyncio
async def test_hungary_config_preserves_pdf_detail_urls() -> None:
    row = _rows("boards.csv", "board_slug", "sonepar-hungary")[0]
    board = {
        "board_url": row["board_url"],
        "metadata": json.loads(row["monitor_config"]),
    }
    html = """
    <div class="accentuated-range__card">
      <h3>Raktáros | Dunaharaszti</h3>
      <div class="accentuated-range__card__link">
        <a href="/resource/blob/123/warehouse-data.pdf">Részletek</a>
      </div>
    </div>
    <div class="accentuated-range__card">
      <h3>Termékfelelős | Energiaelosztás és automatizálás</h3>
      <div class="accentuated-range__card__link">
        <a href="/resource/blob/456/product-data.pdf">Részletek</a>
      </div>
    </div>
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))

    async with httpx.AsyncClient(transport=transport) as client:
        jobs = await discover(board, client)

    assert [(job.title, job.locations, job.url) for job in jobs] == [
        (
            "Raktáros",
            ["Hungary"],
            "https://www.sonepar.hu/resource/blob/123/warehouse-data.pdf",
        ),
        (
            "Termékfelelős",
            ["Hungary"],
            "https://www.sonepar.hu/resource/blob/456/product-data.pdf",
        ),
    ]
