"""Freeze configured recovery output from the original Python implementation."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import httpx

from src.core.scrapers.jsonld import _scrape_workday_fallback


async def main() -> None:
    cases = []
    for mode in (
        "exact",
        "duplicate",
        "ambiguous",
        "wrong-id",
        "malformed-rows",
        "missing-rows",
        "no-id",
        "empty-title",
    ):
        path = "/job/NY/Engineer_REQ100"
        rows: object = [None, 5, {"externalPath": path}]
        if mode == "duplicate":
            rows = [{"externalPath": path}, {"externalPath": path}]
        elif mode == "ambiguous":
            rows = [{"externalPath": path}, {"externalPath": "/job/LA/Engineer_REQ100"}]
        elif mode == "wrong-id":
            rows = [{"externalPath": "/job/NY/Engineer_REQ101"}]
        elif mode == "malformed-rows":
            rows = {}
        search = {} if mode == "missing-rows" else {"jobPostings": rows}
        detail = {
            "jobPostingInfo": {
                "title": "" if mode == "empty-title" else "Engineer",
                "jobDescription": "<p>Build systems.</p>",
                "location": "US-NY-NEW YORK",
                "timeType": "Full time",
                "remoteType": "Hybrid",
                "startDate": "2026-10-14",
                "jobReqId": "REQ100",
            }
        }
        requests = []

        def handler(request, search=search, detail=detail, requests=requests):
            requests.append({"method": request.method, "url": str(request.url)})
            return httpx.Response(200, json=search if request.method == "POST" else detail)

        source = "https://careers.phenom.example/job/REQ100/engineer"
        if mode == "no-id":
            source = "https://careers.phenom.example/careers"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await _scrape_workday_fallback(
                source,
                {
                    "company": "fixture",
                    "wd_instance": "wd5",
                    "site": "External",
                    "proxy": False,
                },
                client,
            )
        cases.append(
            {
                "name": mode,
                "source": source,
                "search": search,
                "detail": detail,
                "requests": requests,
                "expected": dataclasses.asdict(content) if content is not None else None,
            }
        )
    Path(__file__).with_name("python_recovery.json").write_text(
        json.dumps(cases, indent=2, ensure_ascii=False) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
