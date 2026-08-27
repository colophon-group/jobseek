"""Reviewed board contracts for Swiss Medical Network."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from src.core.monitors import is_rich_monitor
from src.core.monitors.dom import _prospective_probe_config, dom_discover

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_ZOFINGEN_URL = "https://jobs.spitalzofingen.ch/"
_UUID = "10e7e137-4419-438e-a20f-20d1997456e5"


def _rows() -> dict[str, dict[str, str]]:
    with _BOARDS_PATH.open(newline="") as handle:
        return {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "swiss-medical-network"
        }


def _config(row: dict[str, str], field: str) -> dict:
    return json.loads(row[field] or "{}")


def _own_rep_html(*, title_slug: str = "pflegefachperson", total: int = 1) -> str:
    rows = ""
    if total:
        rows = f"""
        <li>
          <a class="job" href="/offene-stellen/{title_slug}/{_UUID}">
            <h2><span>Dipl. Pflegefachperson</span></h2>
          </a>
        </li>
        """
    return f"""
    <html><head>
      <link rel="stylesheet" href="/careercenter/1003118/assets/css/company.css">
    </head><body class="ownRep">
      <form id="careercenter-form">
        <input id="offset" value="0">
        <input id="limit" value="50">
      </form>
      <div class="chips"><a class="reset active"><span>Jobs</span><span>{total}</span></a></div>
      <ul class="jobsList">{rows}</ul>
    </body></html>
    """


def test_company_has_exactly_two_nonduplicated_provider_boards():
    rows = _rows()

    assert set(rows) == {
        "swiss-medical-network-smartrecruiters",
        "swiss-medical-network-spital-zofingen",
    }


def test_smartrecruiters_board_enables_exact_rich_identity_contract():
    row = _rows()["swiss-medical-network-smartrecruiters"]
    config = _config(row, "monitor_config")

    assert row["board_url"] == "https://careers.smartrecruiters.com/SwissMedicalNetwork1"
    assert row["monitor_type"] == "smartrecruiters"
    assert row["scraper_type"] == "skip"
    assert config == {
        "token": "SwissMedicalNetwork1",
        "canonical_identity": "job-location-v1",
    }
    assert is_rich_monitor(row["monitor_type"], config) is True


def test_zofingen_board_uses_uuid_identity_exact_total_zero_and_expiry_suppression():
    row = _rows()["swiss-medical-network-spital-zofingen"]
    config = _config(row, "monitor_config")
    scraper = _config(row, "scraper_config")

    assert row["monitor_type"] == "dom"
    assert config["prospective_board"] == "1003118"
    assert config["prospective_canonical_path"] == "/offene-stellen/job/"
    assert config["rich_rows"]["total_selector"] == (".chips a.reset.active > span:last-child")
    assert config["empty_states"][0]["exact_text"] == "0"
    assert config["empty_states"][0]["forbidden_link_selector"] == ".jobsList a.job[href]"
    assert row["scraper_type"] == "json-ld"
    assert scraper == {"enrich": ["description"], "ignore_valid_through": True}


def test_own_rep_probe_returns_static_exact_provider_contract():
    config = _prospective_probe_config(_own_rep_html(), _ZOFINGEN_URL)

    assert config is not None
    assert config["prospective_board"] == "1003118"
    assert config["urls"] == 1
    assert config["prospective_canonical_path"] == "/offene-stellen/job/"
    assert config["rich_rows"] == {
        "row_selector": ".jobsList li",
        "link_selector": "a.job[href]",
        "title_selector": "a.job h2 > span:first-child",
        "total_selector": ".chips a.reset.active > span:last-child",
        "allow_missing_locations": True,
    }


async def test_own_rep_title_slug_churn_keeps_the_same_uuid_source_url():
    config = _prospective_probe_config(_own_rep_html(), _ZOFINGEN_URL)
    assert config is not None

    async def run(html: str):
        with patch(
            "src.shared.http_retry.fetch_text_page_with_retry",
            AsyncMock(return_value=html),
        ):
            return await dom_discover(
                {"board_url": _ZOFINGEN_URL, "metadata": config},
                AsyncMock(),
            )

    before = await run(_own_rep_html(title_slug="pflegefachperson"))
    after = await run(_own_rep_html(title_slug="nouveau-titre-francais"))

    expected = f"https://jobs.spitalzofingen.ch/offene-stellen/job/{_UUID}"
    assert [job.url for job in before] == [expected]
    assert [job.url for job in after] == [expected]


def test_own_rep_probe_rejects_partial_advertised_inventory():
    partial = _own_rep_html(total=1).replace("<li>", "<template>").replace("</li>", "</template>")

    assert _prospective_probe_config(partial, _ZOFINGEN_URL) is None


async def test_own_rep_runtime_rejects_wrong_provider_medium():
    config = _prospective_probe_config(_own_rep_html(), _ZOFINGEN_URL)
    assert config is not None
    wrong = _own_rep_html().replace("careercenter/1003118/", "careercenter/9999999/")

    with (
        patch(
            "src.shared.http_retry.fetch_text_page_with_retry",
            AsyncMock(return_value=wrong),
        ),
        pytest.raises(ValueError, match="medium does not match listing assets"),
    ):
        await dom_discover(
            {"board_url": _ZOFINGEN_URL, "metadata": config},
            AsyncMock(),
        )


async def test_own_rep_authoritative_zero_is_provider_bound_and_contradiction_free():
    config = _prospective_probe_config(_own_rep_html(), _ZOFINGEN_URL)
    assert config is not None
    zero = _own_rep_html(total=0)

    with patch(
        "src.shared.http_retry.fetch_text_page_with_retry",
        AsyncMock(return_value=zero),
    ):
        assert (
            await dom_discover(
                {"board_url": _ZOFINGEN_URL, "metadata": config},
                AsyncMock(),
            )
            == []
        )

    contradiction = zero.replace(
        '<ul class="jobsList">',
        f'<ul class="jobsList"><li><a class="job" href="/offene-stellen/x/{_UUID}">X</a></li>',
    )
    with (
        patch(
            "src.shared.http_retry.fetch_text_page_with_retry",
            AsyncMock(return_value=contradiction),
        ),
        pytest.raises(ValueError),
    ):
        await dom_discover(
            {"board_url": _ZOFINGEN_URL, "metadata": config},
            AsyncMock(),
        )
