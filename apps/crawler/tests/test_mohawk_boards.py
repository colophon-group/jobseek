from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors.api_sniffer import discover
from src.lightpanda.census import build_manifest

BOARDS_CSV = Path(__file__).parents[1] / "data" / "boards.csv"


def _config(alias: str) -> dict:
    with BOARDS_CSV.open(newline="") as handle:
        row = next(
            row
            for row in csv.DictReader(handle)
            if row["board_slug"] == f"mohawk-industries-{alias}"
        )
    return json.loads(row["monitor_config"])


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_tail", [False, True])
async def test_unilin_requests_complete_advertised_inventory_and_protects_missing_tail(
    missing_tail,
):
    config = _config("careers-unilin")
    requested_limits = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requested_limits.append(body["pageSize"])
        # The monitor intentionally tolerates one changing record. A missing
        # two-record tail must still prevent authoritative delisting.
        count = min(body["pageSize"], 26 if missing_tail else 28)
        rows = [
            {
                "title": f"Maintenance Technician {i}",
                "url": f"/en/jobs/maintenance-technician_{i}_en-gb",
                "locationAndDivision": "Ross On Wye - Insulation",
                "description": "<p>Maintain machinery and resolve electrical faults.</p>",
            }
            for i in range(count)
        ]
        return httpx.Response(200, json={"list": rows, "listerParams": {"totalCount": 28}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(
            {"board_url": "https://jobs.unilin.com/en/jobs", "metadata": config}, client
        )

    assert requested_limits == [12, 28]
    if missing_tail:
        assert isinstance(result, MonitorResult)
        assert result.truncated
        assert len(result.urls) == 26
    else:
        assert isinstance(result, list)
        assert len(result) == 28
        assert all(job.locations == ["Ross On Wye"] for job in result)
        assert all(job.description.startswith("<p>") for job in result)


def test_census_accepts_nextdata_talent_pool_filter():
    # The runtime supports title filtering independently of the provider's
    # employment category; a real internship can be categorized as a talent pool.
    config = _config("careers-br")
    assert config["job_filter"] == {"field": "title", "exclude": "(?i)banco de talentos"}
    build_manifest(BOARDS_CSV)
