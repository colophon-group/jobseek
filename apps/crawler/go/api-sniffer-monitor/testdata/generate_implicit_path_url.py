"""Freeze composed array and URL inference through unchanged Python discovery."""

from __future__ import annotations

import asyncio
import dataclasses
import json
from pathlib import Path

import httpx

from src.core.monitors.api_sniffer import _discover_http


async def main():
    rows = [
        {
            "JOB_TITLE": f"Engineer {i}",
            "ZAKRES_OBOWIAZKOW": "<p>Build reliable systems.</p>",
            "CITY_NAME": "Bratislava",
            "START_DATE": "2026-10-27",
            "CATEGORY5": "EUR 2000 monthly",
            "links": {"canonical": f"https://example.com/jobs/{i}"},
        }
        for i in range(3)
    ]
    metadata = {
        "api_url": "https://example.com/api/jobs",
        "fields": {
            "title": "JOB_TITLE",
            "description": "ZAKRES_OBOWIAZKOW",
            "locations": "CITY_NAME",
            "date_posted": "START_DATE",
            "base_salary": "CATEGORY5",
        },
    }
    cases = []
    for name, payload in [
        ("root", rows),
        ("small-root", rows[:1]),
        ("wrapped", {"data": {"jobs": rows}}),
        ("vacancies-wrapper", {"vacancies": rows}),
        ("empty-root", []),
    ]:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request, payload=payload: httpx.Response(200, json=payload)
            )
        ) as client:
            jobs = await _discover_http(
                {"board_url": "https://example.com/careers"}, client, metadata
            )
        cases.append(
            dict(
                name=name,
                metadata=metadata,
                payload=payload,
                expected=[dataclasses.asdict(j) for j in jobs],
            )
        )
    Path(__file__).with_name("python_implicit_path_url.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
