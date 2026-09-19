"""Regression contracts for the 2026-09-19 daily error review."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import httpx
import pytest

from src.core.monitors.dom import dom_discover
from src.shared.constants import get_data_dir
from src.shared.csv_io import read_csv


def _rows() -> dict[str, dict[str, str]]:
    _, rows = read_csv(get_data_dir() / "boards.csv")
    return {row["board_slug"]: row for row in rows}


def _monitor(slug: str) -> dict:
    return json.loads(_rows()[slug]["monitor_config"] or "{}")


def _scraper(slug: str) -> dict:
    return json.loads(_rows()[slug]["scraper_config"] or "{}")


def test_reviewed_empty_and_expiry_contracts() -> None:
    unaids = _monitor("unaids-consulting")
    assert unaids["rich_rows"]["row_selector"] == ("table tbody tr:has(td:nth-child(2) a[href])")
    assert (unaids["empty_selector"], unaids["empty_text"]) == (
        "table tbody tr",
        "- - - -",
    )

    geneva_call = _monitor("geneva-call-careers")
    assert "valid_through_regex" not in geneva_call
    assert "valid_through_format" not in geneva_call
    assert "exclude_expired" not in geneva_call


@pytest.mark.asyncio
async def test_unaids_placeholder_row_is_an_authoritative_empty_inventory() -> None:
    html = """
    <table><tbody><tr><td>-</td><td>-</td><td>-</td><td>-</td></tr></tbody></table>
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    row = _rows()["unaids-consulting"]
    async with httpx.AsyncClient(transport=transport) as client:
        result = await dom_discover(
            {"board_url": row["board_url"], "metadata": json.loads(row["monitor_config"])},
            client,
        )

    assert result == []


def test_reviewed_inventory_and_browser_bounds() -> None:
    epfl = _monitor("epfl-phd-edpy")
    assert any(
        "2026/09/PhD-position-on-Cavity-quantum" in url for url in epfl["rich_rows"]["active_urls"]
    )
    assert _monitor("capgemini-sogeti-us")["oracle_adf_job_ids"]["max_scan"] == 5000
    assert _monitor("mediamarktsaturn-dtb-headquarters")["actions"] == [
        {"action": "wait", "ms": 10000}
    ]


def test_external_world_gymnastics_pdf_is_excluded() -> None:
    pattern = re.compile(
        _monitor("international-gymnastics-federation-jobs")["require_pdf_text"]["exclude"]
    )
    assert pattern.search("TROMSØ TURNFORENING")
    assert pattern.search("TROMSO TURNFORENING")


def test_lucky_strike_boards_share_reviewed_jibe_inventory() -> None:
    rows = [
        row
        for row in _rows().values()
        if row["company_slug"] == "lucky-strike-entertainment" and row["monitor_type"] == "icims"
    ]
    configs = [json.loads(row["monitor_config"]) for row in rows]
    assert {config["jibe_url"] for config in configs} == {"https://careers.luckystrikeent.com/jobs"}
    reviewed_host_sets = {frozenset(config["jibe_job_hosts"]) for config in configs}
    assert len(reviewed_host_sets) == 1
    assert {urlparse(row["board_url"]).hostname for row in rows} == next(iter(reviewed_host_sets))


def test_basel_and_next_identity_fallbacks_are_bounded() -> None:
    identity = _monitor("university-of-basel-main")["application_identity"]
    aliases = identity["source_url_aliases"]
    assert len(aliases) == 10
    assert set(aliases.values()) == {
        "0a75f46d-d267-4ceb-b84d-a7317aa4bb1d",
        "24b1e3af-2b71-4948-9129-027b22feb394",
        "2f2b0671-9412-44d4-9add-418273021591",
        "8d1deb7c-b01b-4f84-a74c-de66a10e1905",
    }
    assert all(aliases[canonical] == canonical for canonical in set(aliases.values()))
    assert re.fullmatch(
        identity["canonical_url_allowlist"],
        "https://biped.sni.unibas.ch/application/form/123/1",
    )
    assert _scraper("next-careers")["host"] == "ekeq.fa.em2.oraclecloud.com"
    assert _scraper("next-careers")["site"] == "CX_3001"


def test_non_owned_university_of_geneva_eas_boards_are_removed() -> None:
    slugs = set(_rows())
    assert "university-of-geneva-astronomy-eas-jobs" not in slugs
    assert "university-of-geneva-astronomy-eas-phd" not in slugs
