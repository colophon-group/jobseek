from __future__ import annotations

import csv
import json
from pathlib import Path

DATA_DIR = Path(__file__).parents[1] / "data"


def _fribourg_rows() -> list[dict[str, str]]:
    with (DATA_DIR / "boards.csv").open(encoding="utf-8", newline="") as handle:
        return [
            row for row in csv.DictReader(handle) if row["company_slug"] == "university-of-fribourg"
        ]


def test_university_of_fribourg_sources_are_unique_and_complete():
    rows = _fribourg_rows()

    assert len(rows) == 13
    assert len({row["board_slug"] for row in rows}) == 13
    assert len({row["board_url"] for row in rows}) == 13


def test_university_of_fribourg_central_feed_has_stable_exact_identity_contract():
    rows = _fribourg_rows()
    central = [row for row in rows if row["board_slug"] == "university-of-fribourg-careers"]

    assert len(central) == 1
    row = central[0]
    assert row["board_url"] == "https://www.unifr.ch/sp/fr/postes-vacants.html"
    assert row["monitor_type"] == "inline"
    assert row["scraper_type"] == "skip"

    config = json.loads(row["monitor_config"])
    detail_api = config["detail_api"]
    assert config["require_zero_proof"] is True
    assert detail_api["id_field"] == "detail_id"
    assert detail_api["item_selector"] == "ul.list-group.list > li.list-group-item"
    assert detail_api["item_identity_attribute"] == "id"
    assert detail_api["item_identity_regex"] == r"^(\d+)$"
    assert set(detail_api["fields"]) == {
        "title",
        "description",
        "date_posted",
        "valid_through",
    }
    assert set(detail_api["required_fields"]) == {
        "title",
        "description",
        "valid_through",
    }
    assert "url" not in detail_api["fields"]
    assert all(step["optional"] is True for step in config["steps"])


def test_university_of_fribourg_has_no_central_locale_mirror():
    central_urls = [
        row["board_url"]
        for row in _fribourg_rows()
        if "/sp/" in row["board_url"] and "postes-vacants" in row["board_url"]
    ]

    assert central_urls == ["https://www.unifr.ch/sp/fr/postes-vacants.html"]


def test_university_of_fribourg_department_mirrors_are_excluded():
    configs = {
        row["board_slug"]: json.loads(row["monitor_config"] or "{}") for row in _fribourg_rows()
    }

    assert configs["university-of-fribourg-physics"]["exclude_title_regex"] == (
        "^Open Postdoc / PhD position"
    )
    assert configs["university-of-fribourg-ses"]["exclude_titles"] == [
        "Professorship in Applied Microeconomics (Open Rank)"
    ]
