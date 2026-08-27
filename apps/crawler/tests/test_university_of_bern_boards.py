"""University of Bern board identity and inventory contracts."""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import httpx
import pytest

from src.core.monitor import monitor_one


def _bern_rows() -> list[dict[str, str]]:
    boards_path = Path(__file__).resolve().parents[1] / "data" / "boards.csv"
    with boards_path.open(newline="", encoding="utf-8") as handle:
        return [
            row for row in csv.DictReader(handle) if row["company_slug"] == "university-of-bern"
        ]


def _config() -> dict:
    return json.loads(_bern_rows()[0]["monitor_config"])


async def _monitor(payload: object):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        return await monitor_one(
            "https://jobs.unibe.ch/",
            "api_sniffer",
            _config(),
            client,
        )


def test_university_of_bern_uses_only_authoritative_central_inventory():
    rows = _bern_rows()

    assert [row["board_slug"] for row in rows] == ["university-of-bern-prospective"]
    assert rows[0]["monitor_type"] == "api_sniffer"
    assert rows[0]["scraper_type"] == "skip"


@pytest.mark.asyncio
async def test_university_of_bern_identity_survives_title_and_locale_changes():
    viewkey = "6f811874-a6d0-48f5-9d6b-57c369861d2a"
    result = await _monitor(
        {
            "jobs": [
                {"viewkey": viewkey, "title": "Deutscher Titel", "language": "de"},
                {"viewkey": viewkey, "title": "English title", "language": "en"},
            ],
            "total": 2,
        }
    )

    canonical_url = f"https://jobs.unibe.ch/offene-stellen/_/{viewkey}"
    assert result.urls == {canonical_url}
    assert result.jobs_by_url is not None
    assert set(result.jobs_by_url) == {canonical_url}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"jobs": [], "total": False},
        {"jobs": [{"title": "Missing identity"}], "total": 1},
        {
            "jobs": [
                {
                    "viewkey": "6F811874-A6D0-48F5-9D6B-57C369861D2A",
                    "title": "Uppercase alias",
                }
            ],
            "total": 1,
        },
    ],
)
async def test_university_of_bern_rejects_unproved_empty_or_invalid_identity(payload):
    with pytest.raises(ValueError):
        await _monitor(payload)


def test_university_of_bern_config_is_fail_closed():
    config = _config()
    lowercase_uuid = "6f811874-a6d0-48f5-9d6b-57c369861d2a"

    assert config["empty_response"] == {"jobs": [], "total": 0}
    assert config["item_filter"]["dedupe_by"] == ["viewkey"]
    assert re.fullmatch(config["item_filter"]["require_regex"]["viewkey"], lowercase_uuid)
    assert not re.fullmatch(
        config["item_filter"]["require_regex"]["viewkey"], lowercase_uuid.upper()
    )
    assert re.fullmatch(
        config["url_allowlist"],
        f"https://jobs.unibe.ch/offene-stellen/_/{lowercase_uuid}",
    )
