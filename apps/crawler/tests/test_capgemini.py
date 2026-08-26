"""Stable provider-identity contracts for Capgemini boards."""

from __future__ import annotations

import json

from src.core.monitor import MonitorResult, _apply_url_allowlist, _apply_url_transform
from src.shared.constants import get_data_dir
from src.shared.csv_io import read_csv


def _rows() -> dict[str, dict[str, str]]:
    _, rows = read_csv(get_data_dir() / "boards.csv")
    return {row["board_slug"]: row for row in rows if row["company_slug"] == "capgemini"}


def _config(slug: str) -> dict:
    return json.loads(_rows()[slug]["monitor_config"] or "{}")


def _canonicalize(slug: str, urls: set[str]) -> MonitorResult:
    config = _config(slug)
    result = _apply_url_allowlist(MonitorResult(urls=urls), config)
    return _apply_url_transform(result, config)


def test_capgemini_uses_five_non_overlapping_provider_boards() -> None:
    assert set(_rows()) == {
        "capgemini-cambridge-consultants",
        "capgemini-frog",
        "capgemini-global",
        "capgemini-government-solutions",
        "capgemini-purpose",
    }


def test_cambridge_consultants_uses_stable_greenhouse_provider_ids() -> None:
    row = _rows()["capgemini-cambridge-consultants"]

    assert row["board_url"] == ("https://job-boards.eu.greenhouse.io/cambridgeconsultantslimited")
    assert row["monitor_type"] == "greenhouse"
    assert _config("capgemini-cambridge-consultants") == {"token": "cambridgeconsultantslimited"}
    assert row["scraper_type"] == "skip"


def test_global_title_and_tracking_aliases_collapse_to_posting_id() -> None:
    aliases = {
        (
            "https://careers.capgemini.com/job/Amsterdam-Old-Title/1390944733/"
            "?feedId=388633&utm_source=CareerSite&tcsource=apply"
        ),
        "https://careers.capgemini.com/job/Changed-Title/1390944733/",
    }

    result = _canonicalize("capgemini-global", aliases)

    assert result.urls == {"https://careers.capgemini.com/job/-/1390944733/"}
    assert result.security_filtered_count == 0


def test_global_distinct_provider_posting_ids_remain_distinct() -> None:
    urls = {
        "https://careers.capgemini.com/job/Role-One/1390944733/",
        "https://careers.capgemini.com/job/Role-Two/1390944734/",
    }

    assert _canonicalize("capgemini-global", urls).urls == {
        "https://careers.capgemini.com/job/-/1390944733/",
        "https://careers.capgemini.com/job/-/1390944734/",
    }


def test_global_greenhouse_identity_is_already_stable() -> None:
    url = "https://job-boards.eu.greenhouse.io/capgeminideutschlandgmbh/jobs/4100659101"

    assert _canonicalize("capgemini-global", {url}).urls == {url}


def test_global_identity_contract_drops_untrusted_apply_host() -> None:
    result = _canonicalize(
        "capgemini-global",
        {"https://evil.example/job/Injected/1390944733/"},
    )

    assert result.urls == set()
    assert result.security_filtered_count == 1


def test_frog_title_and_location_suffix_churn_collapses_to_page_id() -> None:
    aliases = {
        ("https://www.frog.co/careers/jobs/699c95a6939f64fc32ab74e7-london-brand-422152"),
        (
            "https://www.frog.co/careers/jobs/"
            "699c95a6939f64fc32ab74e7-changed-location-changed-title"
        ),
    }

    assert _canonicalize("capgemini-frog", aliases).urls == {
        "https://www.frog.co/careers/jobs/699c95a6939f64fc32ab74e7"
    }
