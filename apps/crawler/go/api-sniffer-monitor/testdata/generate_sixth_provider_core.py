"""Freeze existing Python Manatal/HRMOS parsing for native parity checks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.monitors import hrmos as hrmos_monitor
from src.core.monitors import manatal as manatal_monitor
from src.core.monitors.hrmos import (
    _is_explicit_empty_listing,
    _page_metadata,
    _parse_listing,
)
from src.core.monitors.manatal import _parse_job

rows = [
    {
        "hash": "one",
        "id": 12,
        "position_name": "Engineer",
        "description": "<p>Build</p>",
        "location_display": "  Zürich  ",
    },
    {"hash": "two", "city": " Zürich ", "state": "ZH", "country": "Switzerland"},
    {"hash": "three", "id": 0, "city": 0, "state": False, "country": 12},
    {"hash": "four", "location_display": " ", "city": " "},
    {"hash": 42, "position_name": ["one", "two"]},
    {"hash": "five", "id": False, "position_name": 0, "description": False},
    {"hash": ""},
    {"hash": None},
    {"id": 1},
]
manatal = []
for row in rows:
    job = _parse_job(row, "tenant")
    expected = None if job is None else asdict(job)
    if expected:
        expected = {
            key: expected[key] for key in ("url", "title", "description", "locations", "metadata")
        }
    manatal.append({"row": row, "expected": expected})

pages = [
    (
        '<div id="jsi-joblist">全 2 件中 2 件</div>'
        '<a href="/pages/tenant/jobs/A_1?source=a">one</a>'
        '<a href="https://hrmos.co/pages/tenant/jobs/B-2/#x">two</a>'
    ),
    (
        '<div id="jsi-joblist">全 1,200 件中 100 件</div>'
        '<span class="current">2</span>'
        '<a href="?page=12">last</a>'
        '<a href="/pages/tenant/jobs/One">one</a>'
    ),
    (
        '<div id="jsi-joblist">全 1 件中 1 件</div>'
        '<a href="/pages/TENANT/jobs/One">one</a>'
        '<a href="/pages/other/jobs/Two">foreign</a>'
        '<a href="/pages/tenant/jobs/One?q=2">duplicate</a>'
    ),
    '<div id="jsi-joblist">全 0 件中 0 件</div>',
    '<div class="sg-unavailable-notifier other">none</div>',
    '<div id="jsi-joblist">全 0 件中 0 件</div><div class="sg-unavailable-notifier">none</div>',
    '<div id="jsi-joblist">missing count</div>',
]
hrmos = []
for page in pages:
    empty = _is_explicit_empty_listing(page)
    expected = {"empty": empty, "urls": sorted(_parse_listing(page, "tenant"))}
    if not empty:
        try:
            total, displayed, current, linked = _page_metadata(page)
            expected.update(
                total=total, displayed=displayed, current=current or 0, linked_max=linked
            )
        except ValueError:
            expected = {"error": True}
    hrmos.append({"body": page, "expected": expected})


async def inventories():
    out = []
    for provider in ("manatal", "hrmos"):
        for scenario in ("complete", "changed_count", "duplicate", "incomplete", "empty"):
            source = (
                "https://www.careers-page.com/tenant"
                if provider == "manatal"
                else "https://hrmos.co/pages/tenant/jobs"
            )
            responses = {}
            requests = []

            def respond(
                request,
                scenario=scenario,
                provider=provider,
                responses=responses,
                requests=requests,
            ):
                page = int(request.url.params.get("page", "1"))
                total = 0 if scenario == "empty" else 2
                if scenario == "changed_count" and page == 2:
                    total = 3
                ident = "one" if page == 1 or scenario == "duplicate" else "two"
                if provider == "manatal":
                    rows = [] if scenario == "empty" else [{"hash": ident, "position_name": ident}]
                    if scenario == "incomplete" and page == 2:
                        rows = []
                    body = json.dumps(
                        {"count": total, "results": rows, "next": page == 1 and total > 0}
                    )
                elif scenario == "empty":
                    body = '<div class="sg-unavailable-notifier">none</div>'
                else:
                    body = (
                        f'<div id="jsi-joblist">全 {total} 件中 1 件</div>'
                        f'<span class="current">{page}</span>'
                    )
                    if scenario != "incomplete" or page == 1:
                        body += f'<a href="/pages/tenant/jobs/{ident}">one</a>'
                normalized = str(request.url)
                responses[normalized] = body
                requests.append(normalized)
                return httpx.Response(200, text=body)

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                try:
                    result = await (
                        manatal_monitor if provider == "manatal" else hrmos_monitor
                    ).discover({"board_url": source, "metadata": {}}, client)
                    truncated = bool(getattr(result, "truncated", False))
                    values = result.urls if hasattr(result, "urls") else result
                    urls = (
                        sorted(job.url for job in values)
                        if provider == "manatal" and not hasattr(result, "urls")
                        else sorted(values)
                    )
                    expected = {"error": False, "truncated": truncated, "urls": urls}
                except ValueError:
                    expected = {"error": True}
            out.append(
                {
                    "provider": provider,
                    "scenario": scenario,
                    "pages": responses,
                    "requests": requests,
                    "expected": expected,
                }
            )
    return out


Path(__file__).with_name("python_sixth_provider_core.json").write_text(
    json.dumps(
        {"manatal": manatal, "hrmos": hrmos, "inventories": asyncio.run(inventories())},
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
