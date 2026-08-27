"""Stable board contracts for Implenia issue #7175."""

from __future__ import annotations

import json

from src.shared.constants import get_data_dir
from src.shared.csv_io import read_csv
from src.workspace._compat import auto_scraper_type


def _rows() -> dict[str, dict[str, str]]:
    _, rows = read_csv(get_data_dir() / "boards.csv")
    return {row["board_slug"]: row for row in rows if row["company_slug"] == "implenia"}


def test_implenia_global_feed_excludes_separate_wincasa_identity() -> None:
    row = _rows()["implenia-global"]
    config = json.loads(row["monitor_config"])

    assert row["monitor_type"] == "rss"
    assert row["scraper_type"] == "skip"
    assert config["preset"] == "successfactors"
    assert config["feed_url"] == "https://jobs.implenia.com/googlefeed.xml"
    assert config["variant"] == "feed"
    assert config["resolve_job_invite_identity"] is True
    assert config["job_filter"] == {"exclude": "(?i)wincasa"}
    assert config["url_allowlist"] == (
        r"^https://jobs\.implenia\.com/job/[^/?#]+/"
        r"[1-9]\d{0,11}-[a-z]{2}_[A-Z]{2}/$"
    )
    assert config["url_transform"] == {
        "find": (
            r"^https://jobs\.implenia\.com/job/[^/?#]+/"
            r"([1-9]\d{0,11})-[a-z]{2}_[A-Z]{2}/$"
        ),
        "replace": r"https://jobs.implenia.com/job-invite/\1/",
        "collision_policy": "prefer_source_pattern",
        "collision_preferred_source_patterns": [
            "-de_DE/$",
            "-fr_FR/$",
            "-it_IT/$",
            "-en_GB/$",
            "-en_US/$",
            "-sv_SE/$",
            "-nb_NO/$",
        ],
        "collision_canonical_identity_regex": (
            r"^https://jobs\.implenia\.com/job-invite/([1-9]\d{0,11})/$"
        ),
        "collision_identity_metadata_key": "job_invite_id",
        "collision_stream_buffer_limit": 1000,
    }


def test_implenia_yousty_board_is_paginated_and_employer_scoped() -> None:
    row = _rows()["implenia-apprenticeships-ch"]
    monitor_config = json.loads(row["monitor_config"])
    scraper_config = json.loads(row["scraper_config"])

    assert row["monitor_type"] == "dom"
    assert row["scraper_type"] == "json-ld"
    assert monitor_config["yousty_organization"] == "2623187-implenia-schweiz-ag"
    assert monitor_config["require_jsonld_jobposting"] is True
    assert monitor_config["pagination"] == {
        "url_template": (
            "https://www.yousty.ch/de-CH/lehrstellen/?locale=de-CH&"
            "organization_ids%5B%5D=2623187-implenia-schweiz-ag&page={page}"
        ),
        "start": 1,
        "max_pages": 100,
    }
    assert "implenia\\-schweiz\\-ag" in monitor_config["url_filter"]
    assert monitor_config["url_allowlist"] == monitor_config["url_filter"]
    assert monitor_config["url_transform"] == {
        "find": (
            r"^https://www\.yousty\.ch/de-CH/lehrstellen/profile/"
            r"([1-9]\d{0,11})-[^/?#]+-implenia\-schweiz\-ag/?(?:[?#].*)?$"
        ),
        "replace": r"https://www.yousty.ch/de-CH/lehrstellen/profile/\1",
    }
    assert scraper_config == {"defaults": {"job_location_type": "onsite"}}
    assert auto_scraper_type("dom", monitor_config) == ("json-ld", None)
