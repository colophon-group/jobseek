"""Stable provider-identity contracts for Merck KGaA's locale boards."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import httpx
import pytest

from src.core.monitor import MonitorResult, _apply_url_transform, monitor_one

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_EXPECTED_BOARD_SLUGS = {
    "merck-br-pt",
    "merck-cn-zh",
    "merck-de-de",
    "merck-es-es",
    "merck-fr-fr",
    "merck-global-en",
    "merck-it-it",
    "merck-jp-ja",
    "merck-kr-ko",
    "merck-tw-zh",
    "merck-us-en",
}
_CANONICAL_TRANSFORM = {
    "find": (
        r"(?i)^https://(?:careers\.merckgroup\.com/[^/?#]+/[^/?#]+|"
        r"careers\.emdgroup\.com/us/en)/job/(\d+)(?:/[^?#]*)?(?:[?#].*)?$"
    ),
    "replace": r"https://careers.emdgroup.com/us/en/job/\1",
}


def _board_rows() -> dict[str, dict[str, str]]:
    with _BOARDS_PATH.open(newline="") as handle:
        return {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "merck"
        }


def _monitor_config(row: dict[str, str]) -> dict:
    return json.loads(row["monitor_config"] or "{}")


def test_every_merck_provider_board_uses_one_stable_identity_transform():
    rows = _board_rows()

    assert set(rows) == _EXPECTED_BOARD_SLUGS
    assert all(
        _monitor_config(row).get("url_transform") == _CANONICAL_TRANSFORM for row in rows.values()
    )


@pytest.mark.parametrize(
    "source_url",
    [
        "https://careers.merckgroup.com/de/de/job/300535/Clinical-Study-Planning-Manager",
        "https://careers.merckgroup.com/fr/fr/job/300535/Responsable-des-etudes",
        "https://careers.merckgroup.com/it/it/job/300535/Pianificazione-studi",
        "https://careers.merckgroup.com/global/en/job/300535/Changed-title-slug",
        "https://careers.merckgroup.com/jp/ja/job/300535/",
        "https://careers.emdgroup.com/us/en/job/300535?source=locale#apply",
    ],
)
def test_merck_locale_and_title_variants_collapse_to_provider_id(source_url):
    assert (
        re.sub(
            _CANONICAL_TRANSFORM["find"],
            _CANONICAL_TRANSFORM["replace"],
            source_url,
        )
        == "https://careers.emdgroup.com/us/en/job/300535"
    )


@pytest.mark.parametrize(
    "source_url",
    [
        "https://jobs.merck.com/us/en/job/300535/Unrelated-MSD-role",
        "https://careers.merckgroup.com/de/de/job/not-numeric/Role",
        "https://careers.example.com/de/de/job/300535/Role",
    ],
)
def test_merck_transform_does_not_capture_unrelated_or_invalid_provider_urls(source_url):
    assert (
        re.sub(
            _CANONICAL_TRANSFORM["find"],
            _CANONICAL_TRANSFORM["replace"],
            source_url,
        )
        == source_url
    )


def test_dispatcher_transform_deduplicates_locale_and_title_variants():
    variants = {
        "https://careers.merckgroup.com/de/de/job/300535/First-title",
        "https://careers.merckgroup.com/global/en/job/300535/Second-title",
        "https://careers.emdgroup.com/us/en/job/300535",
    }

    transformed = _apply_url_transform(
        MonitorResult(urls=variants),
        {"url_transform": _CANONICAL_TRANSFORM},
    )

    assert transformed.urls == {"https://careers.emdgroup.com/us/en/job/300535"}


@pytest.mark.asyncio
async def test_merck_sitemap_runtime_collapses_duplicate_provider_identity():
    row = _board_rows()["merck-de-de"]
    config = _monitor_config(row)
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://careers.merckgroup.com/de/de/job/300535/First-title</loc></url>
      <url><loc>https://careers.merckgroup.com/de/de/job/300535/Changed-title</loc></url>
    </urlset>
    """
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            text=xml,
            headers={"content-type": "application/xml"},
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await monitor_one(
            row["board_url"],
            row["monitor_type"],
            config,
            client,
        )

    assert result.urls == {"https://careers.emdgroup.com/us/en/job/300535"}


def test_unrelated_msd_board_is_not_given_merck_kgaa_canonicalization():
    with _BOARDS_PATH.open(newline="") as handle:
        msd = next(
            row for row in csv.DictReader(handle) if row["board_slug"] == "msd-north-america"
        )

    assert "url_transform" not in _monitor_config(msd)
