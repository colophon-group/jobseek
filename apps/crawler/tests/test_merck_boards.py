"""Current Merck boards and legacy provider-identity helper contracts."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import httpx
import pytest

from src.core.monitor import (
    MonitorResult,
    _apply_url_allowlist,
    _apply_url_transform,
    monitor_one,
)

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_EXPECTED_BOARD_SLUGS = {
    "merck-careers",
    "merck-springworks",
}
_CANONICAL_ALLOWLIST = (
    r"(?i)^https://(?:careers\.merckgroup\.com/"
    r"(?:br/pt|cn/zh|de/de|es/es|fr/fr|global/en|it/it|jp/ja|kr/ko|tw/zh)/"
    r"job/\d+(?:/[^/?#]*)?|careers\.emdgroup\.com/us/en/job/\d+)$"
)
_CANONICAL_TRANSFORM = {
    "find": (
        r"(?i)^https://(?:careers\.merckgroup\.com/"
        r"(?:br/pt|cn/zh|de/de|es/es|fr/fr|global/en|it/it|jp/ja|kr/ko|tw/zh)|"
        r"careers\.emdgroup\.com/us/en)/job/(\d+)(?:/[^/?#]*)?$"
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


def _apply_identity_contract(source_urls: set[str]) -> MonitorResult:
    filtered = _apply_url_allowlist(
        MonitorResult(urls=source_urls),
        {"url_allowlist": _CANONICAL_ALLOWLIST},
    )
    return _apply_url_transform(
        filtered,
        {"url_transform": _CANONICAL_TRANSFORM},
    )


def test_current_merck_boards_replace_the_retired_locale_sources():
    rows = _board_rows()

    assert set(rows) == _EXPECTED_BOARD_SLUGS
    careers = rows["merck-careers"]
    assert (careers["monitor_type"], careers["scraper_type"]) == ("rss", "skip")
    assert _monitor_config(careers) == {"preset": "successfactors"}

    springworks = rows["merck-springworks"]
    assert (springworks["monitor_type"], springworks["scraper_type"]) == (
        "greenhouse",
        "skip",
    )
    assert _monitor_config(springworks) == {"token": "springsworkstherapeutics"}

    for row in rows.values():
        config = _monitor_config(row)
        assert "identity_migration" not in config
        assert "url_allowlist" not in config
        assert "url_transform" not in config


@pytest.mark.parametrize(
    "source_url",
    [
        "https://careers.merckgroup.com/de/de/job/300535/Clinical-Study-Planning-Manager",
        "https://careers.merckgroup.com/fr/fr/job/300535/Responsable-des-etudes",
        "https://careers.merckgroup.com/it/it/job/300535/Pianificazione-studi",
        "https://careers.merckgroup.com/global/en/job/300535/Changed-title-slug",
        "https://careers.merckgroup.com/jp/ja/job/300535/",
        "https://careers.emdgroup.com/us/en/job/300535",
    ],
)
def test_allowed_merck_provider_urls_collapse_to_provider_id(source_url):
    assert re.fullmatch(_CANONICAL_ALLOWLIST, source_url)
    result = _apply_identity_contract({source_url})
    assert result.urls == {"https://careers.emdgroup.com/us/en/job/300535"}
    assert result.security_filtered_count == 0


@pytest.mark.parametrize(
    "source_url",
    [
        "https://jobs.merck.com/us/en/job/300535/Unrelated-MSD-role",
        "https://careers.merckgroup.com/de/de/job/not-numeric/Role",
        "https://careers.example.com/de/de/job/300535/Role",
        "https://evil.example/de/de/job/300535/Role",
        "https://careers.merckgroup.com/us/en/job/300535/Unapproved-locale",
        "https://careers.merckgroup.com/de/fr/job/300535/Mismatched-locale",
        "https://careers.merckgroup.com/de/de/jobs/300535/Wrong-path",
        "https://careers.merckgroup.com/de/de/job/300535/Role/extra",
        "https://careers.merckgroup.com/de/de/job/300535/Role?source=locale",
        "https://careers.merckgroup.com/de/de/job/300535/Role#apply",
        "https://careers.emdgroup.com/us/en/job/300535/Unexpected-title",
        "https://careers.emdgroup.com/us/en/job/300535?source=locale",
        "https://careers.emdgroup.com/us/en/job/300535/",
        "http://careers.emdgroup.com/us/en/job/300535",
        "https://careers.emdgroup.com:443/us/en/job/300535",
    ],
)
def test_merck_identity_contract_drops_unrelated_or_malformed_urls(source_url):
    assert not re.fullmatch(_CANONICAL_ALLOWLIST, source_url)
    result = _apply_identity_contract({source_url})
    assert result.urls == set()
    assert result.security_filtered_count == 1


@pytest.mark.parametrize("invalid_allowlist", ["", None, 42, "("])
def test_provider_allowlist_configuration_fails_closed(invalid_allowlist):
    result = _apply_url_allowlist(
        MonitorResult(urls={"https://careers.emdgroup.com/us/en/job/300535"}),
        {"url_allowlist": invalid_allowlist},
    )

    assert result.urls == set()
    assert result.security_filtered_count == 1


def test_dispatcher_transform_deduplicates_locale_and_title_variants():
    variants = {
        "https://careers.merckgroup.com/de/de/job/300535/First-title",
        "https://careers.merckgroup.com/global/en/job/300535/Second-title",
        "https://careers.emdgroup.com/us/en/job/300535",
    }

    transformed = _apply_identity_contract(variants)

    assert transformed.urls == {"https://careers.emdgroup.com/us/en/job/300535"}


@pytest.mark.asyncio
async def test_merck_sitemap_runtime_collapses_duplicate_provider_identity():
    board_url = "https://careers.merckgroup.com/de/de/search-results"
    config = {
        "url": f"{board_url}/sitemap.xml",
        "url_filter": r"/job/\d+",
        "url_allowlist": _CANONICAL_ALLOWLIST,
        "url_transform": _CANONICAL_TRANSFORM,
    }
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://careers.merckgroup.com/de/de/job/300535/First-title</loc></url>
      <url><loc>https://careers.merckgroup.com/de/de/job/300535/Changed-title</loc></url>
      <url><loc>https://evil.example/de/de/job/300535/Injected</loc></url>
      <url><loc>https://jobs.merck.com/us/en/job/300535/Unrelated-MSD-role</loc></url>
      <url><loc>https://careers.merckgroup.com/us/en/job/300535/Bad-locale</loc></url>
      <url><loc>https://careers.merckgroup.com/de/de/job/300535/Role?source=bad</loc></url>
      <url><loc>https://careers.merckgroup.com/de/de/c/engineering-jobs</loc></url>
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
            board_url,
            "sitemap",
            config,
            client,
        )

    assert result.urls == {"https://careers.emdgroup.com/us/en/job/300535"}
    assert result.filtered_count == 1
    assert result.security_filtered_count == 4


def test_unrelated_msd_board_is_not_given_merck_kgaa_canonicalization():
    with _BOARDS_PATH.open(newline="") as handle:
        msd = next(row for row in csv.DictReader(handle) if row["board_slug"] == "msd-careers")

    config = _monitor_config(msd)
    assert "url_filter" not in config
    assert "url_allowlist" not in config
    assert "url_transform" not in config
    assert "identity_migration" not in config
