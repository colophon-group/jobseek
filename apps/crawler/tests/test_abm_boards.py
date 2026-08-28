"""ABM Industries board inventory contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_abm_uses_the_verified_parent_and_subsidiary_boards() -> None:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "abm"]

    assert [row["board_slug"] for row in rows] == [
        "abm-able",
        "abm-careers",
        "abm-quality-uptime",
        "abm-ravenvolt",
        "abm-uk",
        "abm-wgnstar",
    ]
    by_slug = {row["board_slug"]: row for row in rows}

    assert by_slug["abm-able"]["board_url"] == "https://jobs.lever.co/ableserve"
    assert by_slug["abm-able"]["monitor_type"] == "lever"
    assert by_slug["abm-able"]["scraper_type"] == "skip"

    assert by_slug["abm-careers"]["board_url"] == (
        "https://eiqg.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
    )
    assert by_slug["abm-careers"]["monitor_type"] == "oracle_hcm"
    assert by_slug["abm-careers"]["scraper_type"] == "oracle_hcm"

    quality_uptime = by_slug["abm-quality-uptime"]
    assert quality_uptime["monitor_type"] == "adp"
    assert quality_uptime["scraper_type"] == "adp"
    assert json.loads(quality_uptime["monitor_config"]) == {
        "cid": "01ab0a1f-f230-4968-bc60-122d853cb61d",
        "cc_id": "19000101_000001",
        "locale": "en_US",
    }
    assert json.loads(quality_uptime["scraper_config"])["title_location_pattern"] == (
        r"\s+-\s+(?P<location>.+)$"
    )

    assert by_slug["abm-ravenvolt"]["board_url"] == ("https://ravenvolt.applytojob.com/apply/")
    assert by_slug["abm-ravenvolt"]["monitor_type"] == "jazzhr"
    assert by_slug["abm-ravenvolt"]["scraper_type"] == "jazzhr"

    abm_uk = by_slug["abm-uk"]
    assert abm_uk["board_url"] == "https://apply.workable.com/abm-careers/"
    assert abm_uk["monitor_type"] == "workable"
    assert abm_uk["scraper_type"] == "workable"
    assert json.loads(abm_uk["monitor_config"]) == {"token": "abm-careers", "proxy": True}
    assert json.loads(abm_uk["scraper_config"]) == {"token": "abm-careers", "proxy": True}

    assert by_slug["abm-wgnstar"]["board_url"] == "https://wgnstar.applytojob.com/apply/"
    assert by_slug["abm-wgnstar"]["monitor_type"] == "jazzhr"
    assert by_slug["abm-wgnstar"]["scraper_type"] == "jazzhr"
