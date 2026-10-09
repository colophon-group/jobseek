"""Freeze actual current DOM alternate fetch and API legacy/slug contracts."""

from __future__ import annotations

import asyncio
import copy
import csv
import importlib.util
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from src.core.monitors import api_sniffer, dom
from src.shared.api_sniff import Exchange

ROOT = Path(__file__).resolve().parents[3]
SLUGS = {
    "adidas-korea-retail",
    "arcelormittal-south-africa",
    "bae-systems-prismatic",
    "carrefour-france-careers-fr-match",
    "cox-careers-eu",
    "nexthop-ai-careers",
    "roboa-careers",
    "salesforce-careers",
    "securitas-france",
    "sinopec-careers-ca",
    "softbank-career",
    "u-blox-careers",
    "vertiv-data-racks",
    "vertiv-data-racks-general",
}
spec = importlib.util.spec_from_file_location(
    "original_api_tests", ROOT / "tests/test_api_sniffer_monitor.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


async def main():
    out = []
    for row in csv.DictReader((ROOT / "data/boards.csv").open()):
        if row["board_slug"] not in SLUGS:
            continue
        md = json.loads(row["monitor_config"])
        md["scraper_type"] = row["scraper_type"]
        if row["scraper_config"]:
            md["scraper_config"] = json.loads(row["scraper_config"])
        if row["board_slug"] == "u-blox-careers":
            md["params"]["X-Algolia-API-Key"] = "synthetic-public-key"
            md["params"]["X-Algolia-Application-Id"] = "SYNTHETIC"
        if row["board_slug"] == "cox-careers-eu":
            md["post_data"] = "fixture_token=synthetic"
        board = dict(provider=row["monitor_type"], board_url=row["board_url"], metadata=md)
        browser = md.get("browser", False)
        title = "Ingénieur R&D + Operations"
        item = dict(
            id="101",
            title=title,
            location={"city": "Calgary"},
            jobOpeningName=title,
            employmentStatusLabel="Contract",
            positions_name=title,
            other_areas=["Tokyo"],
            type="1",
            link="/recruit/career/101",
            name=title,
            locations_legacy=["Zurich"],
            percentage="Full Time",
            department="Engineering",
            link_https="https://www.u-blox.com/en/job/101",
            addressKey="abc101",
            jobLocations=[{"placeName": "Seoul"}],
            employmentType="full_time",
            status="open",
            jobGroup={"title": "Retail"},
            jobTask={"title": "Operations"},
            Job_Posting_Title=title,
            Job_Requisition_Ref_ID="JR101",
            Job_Description="<p>Build reliable systems.</p>",
            Job_Requisition_Primary_Location="Zurich",
            Time_Type="Full Time",
            External_Job_Posting_Start_Date="2026-10-01",
            Job_Family_Group="Engineering",
            JobTitle=title,
            VacancyTitle="Cloud",
            Title="Operations",
            JobDescription="<p>Build reliable systems.</p>",
            Location="London",
            ApplyLink="https://coxautoinc.current-vacancies.com/job/101",
        )
        if md.get("json_path") == "items":
            item["location"] = "Paris"
        if board["provider"] == "api_sniffer":
            payload = {md["json_path"]: [item]}
            if md.get("total_path"):
                payload[md["total_path"]] = 1
            body = json.dumps(payload, ensure_ascii=False)
        else:
            hrefs = {
                "arcelormittal-south-africa": (
                    "applicant/index.php?controller=Listings&amp;method=view&amp;id=101"
                ),
                "bae-systems-prismatic": "https://www.prismaticltd.co.uk/career/engineer/",
                "roboa-careers": "/career/jobs/engineer",
                "vertiv-data-racks": "/welder/",
            }
            href = hrefs.get(row["board_slug"], "/jobs/ignored")
            body = (
                f'<html><body><h1>Careers</h1><a href="{href}">Engineer</a>'
                f'<a href="{row["board_url"]}">Official careers</a></body></html>'
            )
        exchanges = []

        async def handler(request, exchanges=exchanges, body=body, board=board):
            exchanges.append(
                dict(
                    method=request.method,
                    url=str(request.url),
                    body=(await request.aread()).decode(),
                )
            )
            return httpx.Response(
                200,
                text=body,
                headers={
                    "content-type": "application/json"
                    if board["provider"] == "api_sniffer"
                    else "text/html"
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            if browser:
                page = AsyncMock()
                page.evaluate = AsyncMock(
                    return_value={"headers": {}, "text": json.dumps({md["json_path"]: []})}
                )
                captured = Exchange(
                    method=md.get("method", "GET"),
                    url=md["api_url"],
                    request_headers={},
                    post_data=md.get("post_data"),
                    status=200,
                    body=json.loads(body),
                    content_type="application/json",
                    phase="load",
                )
                with (
                    patch.object(
                        api_sniffer, "capture_exchanges", AsyncMock(return_value=[captured])
                    ),
                    patch("asyncio.sleep", AsyncMock()),
                ):
                    result = await api_sniffer.discover(
                        board, client, pw=module._make_mock_pw(page)
                    )
                exchanges = [
                    dict(
                        method=md.get("method", "GET"),
                        url=md["api_url"],
                        body="",
                        browser_capture=True,
                    )
                ]
            elif board["provider"] == "api_sniffer":
                result = await api_sniffer.discover(board, client)
            else:
                result = await dom.dom_discover(board, client)
        if isinstance(result, set):
            jobs = [dict(url=u) for u in sorted(result)]
        else:
            jobs = [asdict(j) for j in result]
        assert jobs, (row["board_slug"], "empty original fixture")
        # Root enrich must be extraction-inert under the actual original monitor.
        if "enrich" in md:
            without = copy.deepcopy(board)
            del without["metadata"]["enrich"]
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                if browser:
                    with (
                        patch.object(
                            api_sniffer, "capture_exchanges", AsyncMock(return_value=[captured])
                        ),
                        patch("asyncio.sleep", AsyncMock()),
                    ):
                        other = await api_sniffer.discover(
                            without, client, pw=module._make_mock_pw(page)
                        )
                else:
                    other = await api_sniffer.discover(without, client)
            assert jobs == [asdict(j) for j in other]
            if not browser:
                exchanges = exchanges[: len(exchanges) // 2]
        out.append(
            dict(
                name=row["board_slug"],
                board=board,
                browser=browser,
                response=body,
                expected=jobs,
                exchanges=exchanges,
            )
        )
    assert len(out) == 14
    target = Path(__file__).with_name("python_shared_inventory_options.json")
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    asyncio.run(main())
