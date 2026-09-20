"""Regression coverage for Inova's proxy-routed Oracle inventories."""

from __future__ import annotations

import csv
import json

from src.shared.constants import DATA_DIR


def _rows(filename: str, slug_field: str) -> list[dict[str, str]]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row[slug_field] == "inova"]


def test_inova_metadata_and_assets_are_complete() -> None:
    companies = _rows("companies.csv", "slug")
    descriptions = _rows("company_descriptions.csv", "slug")

    assert len(companies) == 1
    company = companies[0]
    assert company["name"] == "Inova"
    assert company["website"] == "https://www.inova.org"
    assert company["logo_type"] == "wordmark+icon"
    assert company["industry"] == "3"
    assert company["employee_count_range"] == "8"
    assert company["founded_year"] == "1956"
    assert len(descriptions) == 1
    assert all(descriptions[0][locale] for locale in ("en", "de", "fr", "it"))
    staged = all(
        (DATA_DIR / path).is_file() for path in ("images/inova/logo.png", "images/inova/icon.png")
    )
    uploaded = all(
        company[field].startswith("https://jobseek-assets.colophon-group.org/companies/inova/")
        for field in ("logo_url", "icon_url")
    )
    assert staged or uploaded


def test_inova_keeps_both_verified_oracle_sites_proxy_routed() -> None:
    boards = {row["board_slug"]: row for row in _rows("boards.csv", "company_slug")}

    assert set(boards) == {"inova-careers", "inova-heart-vascular"}
    assert boards["inova-careers"]["board_url"].endswith("/sites/CX_1")
    assert boards["inova-heart-vascular"]["board_url"].endswith("/sites/CX_2001")

    for board in boards.values():
        assert board["monitor_type"] == board["scraper_type"] == "oracle_hcm"
        assert json.loads(board["monitor_config"]) == {"proxy": True}
        assert json.loads(board["scraper_config"]) == {
            "enrich": ["description"],
            "proxy": True,
        }
