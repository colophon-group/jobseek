from __future__ import annotations

import csv
import json
from pathlib import Path

from src.core.monitors.unifr import _ACCORDION_SOURCES

DATA_DIR = Path(__file__).parents[1] / "data"
FIXTURE_DIR = Path(__file__).parent / "fixtures/unifr"


def _fribourg_rows() -> list[dict[str, str]]:
    with (DATA_DIR / "boards.csv").open(encoding="utf-8", newline="") as handle:
        return [
            row for row in csv.DictReader(handle) if row["company_slug"] == "university-of-fribourg"
        ]


def test_university_of_fribourg_uses_only_authoritative_source_contracts():
    rows = _fribourg_rows()
    assert [row["board_slug"] for row in rows] == [
        "university-of-fribourg-ami",
        "university-of-fribourg-biology",
        "university-of-fribourg-careers",
        "university-of-fribourg-chemistry",
        "university-of-fribourg-geo",
        "university-of-fribourg-law",
        "university-of-fribourg-physics",
        "university-of-fribourg-regional-school-service",
        "university-of-fribourg-ses",
    ]
    assert all(row["monitor_type"] == "unifr" for row in rows)
    assert len({row["board_url"] for row in rows}) == len(rows)
    assert {json.loads(row["monitor_config"])["source"] for row in rows} == {
        "ami",
        "biology",
        "central",
        "chemistry",
        "geosciences",
        "law",
        "physics",
        "regional-school-service",
        "ses",
    }


def test_university_of_fribourg_removes_known_duplicate_and_unsafe_sources():
    slugs = {row["board_slug"] for row in _fribourg_rows()}
    assert not any("cryosphere" in slug for slug in slugs)
    assert "university-of-fribourg-history" not in slugs
    assert "university-of-fribourg-social-work" not in slugs
    assert "university-of-fribourg-theology" not in slugs


def test_university_of_fribourg_contracts_match_captured_live_inventory():
    inventory = json.loads(
        (FIXTURE_DIR / "live-inventory-2026-08-26.json").read_text(encoding="utf-8")
    )
    central = inventory["central"]
    assert len(central["fr"]) == 12
    assert len(central["de"]) == 13
    assert set(central["union"]) == set(central["fr"]) | set(central["de"])
    assert len(central["union"]) == 18
    assert len(set(central["fr"]) & set(central["de"])) == 7

    for source_name, expected_ids in inventory["accordion"].items():
        assert _ACCORDION_SOURCES[source_name].expected_ids == frozenset(expected_ids)
    for source_name, duplicates in inventory["central_duplicates"].items():
        assert _ACCORDION_SOURCES[source_name].excluded_central_ids == duplicates

    assert inventory["expired"] == {
        "ami": ["styleguide-3-2", "styleguide-3-3"],
        "geosciences": ["styleguide-2-1"],
    }
    assert inventory["link_counts"] == {"law": 4, "regional-school-service": 2}


def test_university_of_fribourg_images_are_complete_without_pending_assets():
    with (DATA_DIR / "companies.csv").open(encoding="utf-8", newline="") as handle:
        company = next(
            row for row in csv.DictReader(handle) if row["slug"] == "university-of-fribourg"
        )
    assert company["logo_url"] == "https://cdn.unifr.ch/uf/v2.4.5/gfx/logo.png"
    assert company["icon_url"] == "https://cdn.unifr.ch/sharedconfig/favicon/favicon.ico"
    assert not (DATA_DIR / "images/university-of-fribourg").exists()
