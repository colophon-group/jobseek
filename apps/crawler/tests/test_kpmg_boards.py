"""KPMG company and board inventory contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def test_kpmg_metadata_is_complete() -> None:
    with (DATA_DIR / "companies.csv").open(newline="", encoding="utf-8") as handle:
        company = next(row for row in csv.DictReader(handle) if row["slug"] == "kpmg")

    assert company["logo_type"] == "wordmark"
    assert company["industry"] == "12"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "1987"

    with (DATA_DIR / "company_descriptions.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        descriptions = next(row for row in csv.DictReader(handle) if row["slug"] == "kpmg")

    assert all(descriptions[locale] for locale in ("en", "de", "fr", "it"))


def test_kpmg_global_services_uses_verified_oracle_board() -> None:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "kpmg"]

    board = next(row for row in rows if row["board_slug"] == "kpmg-inventory-careers")
    assert board["board_url"] == (
        "https://ejgk.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_3"
    )
    assert board["monitor_type"] == board["scraper_type"] == "oracle_hcm"
    assert json.loads(board["monitor_config"]) == {
        "host": "ejgk.fa.em2.oraclecloud.com",
        "site": "CX_3",
        "page_shortfall_tolerance": 1,
        "total_count_tolerance": 1,
    }
    assert json.loads(board["scraper_config"]) == {"enrich": ["description"]}
