"""Capture the unchanged Python streaming API contract on offline origins."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors import amazon


def job(identity, country="USA"):
    return {
        "job_path": f"/en/jobs/{identity}/engineer",
        "title": "Software Engineer",
        "description": "<p>Build reliable systems</p>",
        "basic_qualifications": "<p>Experience</p>",
        "preferred_qualifications": "<p>Teamwork</p>",
        "normalized_location": "Zurich, Switzerland",
        "country_code": country,
        "id_icims": identity,
        "job_schedule_type": "Full-Time",
        "posted_date": "March  9, 2026",
        "salary": "$151,300/year ... $261,500/year",
    }


async def capture(name, metadata):
    requests = []
    exchanges = []

    def respond(request):
        q = request.url.params
        country = q.get("country")
        category = q.get("category[]")
        offset = int(q.get("offset", 0))
        if str(request.url).split("?")[0] == amazon._CATEGORIES_URL:
            status = 200
            body = '<a href="/job-categories/software-development">Software</a>'
        else:
            status = 200
            if name == "empty":
                total = 0
                rows = []
            elif name == "simple":
                total = 3
                rows = [job(i) for i in range(3)]
            elif name == "late-failure":
                total = 101
                rows = [job(i) for i in range(100)] if offset == 0 else []
                if offset:
                    status = 503
            elif name == "country-partitions":
                if not country:
                    total = 10000
                    rows = [job(offset, "CHE" if offset % 200 else "USA")]
                else:
                    total = 2
                    rows = [
                        job(10000 + i + ({"CHE": 0, "USA": 10}[country]), country) for i in range(2)
                    ]
            else:
                total = 10000 if not category or name == "country-category-cap" else 2
                rows = [job(offset)] if total == 10000 else [job(20000 + i) for i in range(2)]
            body = json.dumps({"hits": total, "jobs": rows})
        requests.append({"url": str(request.url), "method": request.method})
        exchanges.append({"url": str(request.url), "status": status, "body": body})
        return httpx.Response(status, text=body)

    chunks = []
    partial = False
    failed = False
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        try:
            async for batch in amazon.discover_stream(
                {"board_url": "https://www.amazon.jobs/en/search", "metadata": metadata}, client
            ):
                if isinstance(batch, MonitorResult):
                    partial |= batch.truncated
                else:
                    chunks.append([asdict(x) for x in batch])
        except Exception:
            failed = True
    return {
        "name": name,
        "metadata": metadata,
        "exchanges": exchanges,
        "chunks": chunks,
        "original_truncated": partial,
        "original_error": failed,
        "native_error": name == "late-failure",
        "native_truncated": name in {"late-failure", "country-category-cap"},
    }


async def main():
    cases = []
    for name, md in [
        ("empty", {}),
        ("simple", {}),
        ("late-failure", {}),
        ("country-partitions", {}),
        ("category-partitions", {"country": "USA"}),
        ("country-category-cap", {"country": "USA", "category": "software-development"}),
    ]:
        cases.append(await capture(name, md))
    parsing = []
    for raw in [
        job(1),
        job(2)
        | {
            "normalized_location": None,
            "location": "London",
            "posted_date": "bad date",
            "salary": "91,000.00 - 136,500.00 USD annually",
        },
        job(3) | {"salary": "$20/hr ... $30/hr"},
        job(4) | {"salary": "$20/mo ... $30/mo"},
        job(5)
        | {
            "salary": None,
            "description": None,
            "basic_qualifications": None,
            "preferred_qualifications": None,
        },
        {},
    ]:
        parsed = amazon._parse_job(raw)
        parsing.append({"raw": raw, "job": asdict(parsed) if parsed else None})
    Path(__file__).with_name("python_amazon_stream.json").write_text(
        json.dumps({"cases": cases, "parsing": parsing}, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(main())
