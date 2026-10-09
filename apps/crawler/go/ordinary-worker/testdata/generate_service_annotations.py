"""Freeze current shared-monitor annotation behavior from the original monitors."""

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

from src.core.monitors import api_sniffer, dom, sitemap
from src.shared.api_sniff import Exchange

ROOT = Path(__file__).resolve().parents[3]
SLUGS = {
    "citadel-securities-global",
    "mutable-tactics-careers-ef",
    "uber-careers",
    "unitree-robotics-careers",
    "cyberbit-rangeforce-bamboohr",
    "molecubes-careers",
    "sophia-genetics-careers",
}
spec = importlib.util.spec_from_file_location(
    "original_api_tests", ROOT / "tests/test_api_sniffer_monitor.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


async def run(board, body, browser):
    exchanges = []

    async def handler(request):
        exchanges.append(
            dict(method=request.method, url=str(request.url), body=(await request.aread()).decode())
        )
        return httpx.Response(
            200,
            text=body,
            headers={
                "content-type": "application/json"
                if board["provider"] == "api_sniffer"
                else "text/xml"
                if board["provider"] == "sitemap"
                else "text/html"
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        if browser:
            page = AsyncMock()
            page.evaluate = AsyncMock(
                return_value={"headers": {}, "text": json.dumps({"data": {"results": []}})}
            )
            pw = module._make_mock_pw(page)
            captured = Exchange(
                method="POST",
                url=board["metadata"]["api_url"],
                request_headers={},
                post_data=None,
                status=200,
                body=json.loads(body),
                content_type="application/json",
                phase="load",
            )
            with (
                patch.object(api_sniffer, "capture_exchanges", AsyncMock(return_value=[captured])),
                patch("asyncio.sleep", AsyncMock()),
            ):
                result = await api_sniffer.discover(board, client, pw=pw)
            exchanges = [
                dict(method="POST", url=board["metadata"]["api_url"], body="", browser_capture=True)
            ]
        elif board["provider"] == "api_sniffer":
            result = await api_sniffer.discover(board, client)
        elif board["provider"] == "dom":
            result = await dom.dom_discover(board, client)
        else:
            result, new_sitemap = await sitemap.discover(board, client)
            assert new_sitemap is None
    jobs = (
        [dict(url=value) for value in sorted(result)]
        if isinstance(result, set)
        else [asdict(job) for job in result]
    )
    return jobs, exchanges


async def main():
    out = []
    with (ROOT / "data/boards.csv").open() as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row["board_slug"] not in SLUGS:
            continue
        md = json.loads(row["monitor_config"] or "{}")
        md["scraper_type"] = row["scraper_type"]
        if row["scraper_config"]:
            md["scraper_config"] = json.loads(row["scraper_config"])
        board = dict(provider=row["monitor_type"], board_url=row["board_url"], metadata=md)
        slug = row["board_slug"]
        if slug == "unitree-robotics-careers":
            body = json.dumps(
                {
                    "data": {
                        "count": 1,
                        "items": [
                            {
                                "id": 123,
                                "title": "Engineer",
                                "cityId": "unknown",
                                "postTime": "2026-10-09",
                                "duty": "<p>Build robots</p>",
                                "ability": "<p>Go</p>",
                            }
                        ],
                    }
                }
            )
        elif slug == "cyberbit-rangeforce-bamboohr":
            body = json.dumps(
                {
                    "result": [
                        {
                            "id": 123,
                            "jobOpeningName": "Engineer",
                            "employmentStatusLabel": "Full-Time",
                            "departmentLabel": "Security",
                        }
                    ]
                }
            )
        elif slug == "uber-careers":
            body = json.dumps({"data": {"results": [{"id": "123", "title": "Engineer"}]}})
        elif board["provider"] == "dom":
            body = '<html><a href="/jobs/engineer">Engineer</a></html>'
        else:
            url = {
                "citadel-securities-global": "https://www.citadelsecurities.com/careers/details/123",
                "mutable-tactics-careers-ef": "https://portfolio.joinef.com/companies/mutable-tactics-2/jobs/123",
                "sophia-genetics-careers": "https://careers.sophiagenetics.com/jobs/123",
            }[slug]
            body = f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{url}</loc></url></urlset>'
        browser = md.get("browser") is True
        jobs, exchanges = await run(board, body, browser)
        other = copy.deepcopy(board)
        other["metadata"].pop("defaults", None)
        other["metadata"].pop("rescrape_policy", None)
        previous, previous_exchanges = await run(other, body, browser)
        assert jobs and jobs == previous and exchanges == previous_exchanges, slug
        out.append(
            dict(
                name=slug,
                board=board,
                response=body,
                expected=jobs,
                browser=browser,
                exchanges=exchanges,
            )
        )
    assert len(out) == 7
    Path(__file__).with_name("python_service_annotations.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(main())
