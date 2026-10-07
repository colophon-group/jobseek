"""Freeze the four existing Python providers for native parity checks."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.monitors import hrmos as hrmos_monitor
from src.core.monitors import jobs_ch as jobcloud_monitor
from src.core.monitors import manatal as manatal_monitor
from src.core.monitors import recruiterbox as recruiterbox_monitor
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
        for scenario in (
            "complete",
            "changed_count",
            "duplicate",
            "incomplete",
            "empty",
            "wrong_current",
        ):
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
                        f'<span class="current">{0 if scenario == "wrong_current" else page}</span>'
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
    out.extend(await extension_inventories())
    return out


async def extension_inventories():
    out = []
    for provider in ("recruiterbox", "jobs_ch"):
        scenarios = (
            "complete",
            "empty",
            "duplicate",
            "changed_count",
            "incomplete",
            "later_missing",
            "later_failure",
            "foreign_company",
        )
        scenarios += (
            ("first_missing", "inactive_first", "inactive_later")
            if provider == "recruiterbox"
            else ("uuid_alias", "jobup_alias")
        )
        for scenario in scenarios:
            source = (
                "https://tenant.recruiterbox.com/"
                if provider == "recruiterbox"
                else "https://www.jobs.ch/de/firmen/123-tenant/"
            )
            metadata = {}
            if scenario in {"uuid_alias", "jobup_alias"}:
                host, path, locale = (
                    ("www.jobup.ch", "societes", "fr")
                    if scenario == "jobup_alias"
                    else ("www.jobs.ch", "firmen", "de")
                )
                source = (
                    f"https://{host}/{locale}/{path}/abcdef01-2345-6789-abcd-ef0123456789-tenant/"
                )
                metadata = {"document_company_id": 123}
            pages, statuses, requests = {}, {}, []

            def respond(
                request,
                provider=provider,
                scenario=scenario,
                pages=pages,
                statuses=statuses,
                requests=requests,
            ):
                page = int(
                    request.url.params.get("p" if provider == "recruiterbox" else "page", "1")
                )
                total = 0 if scenario == "empty" else 101
                if scenario == "changed_count" and page == 2:
                    total = 102
                identities = list(range(1, 101)) if page == 1 else [101]
                if scenario == "empty" or scenario == "incomplete" and page == 2:
                    identities = []
                if scenario == "duplicate" and page == 2:
                    identities = [1]
                status = (
                    404
                    if scenario == "later_missing" and page == 2
                    else 500
                    if scenario == "later_failure" and page == 2
                    else 200
                )
                if scenario == "first_missing":
                    status = 404
                if provider == "recruiterbox":
                    body = f"<script>var total_jobs: {total}</script>"
                    body += "".join(f'<a href="/jobs/job{i}/">job</a>' for i in identities)
                    if scenario == "foreign_company" and page == 2:
                        body = f'<script>var total_jobs: {total}</script><a href="https://other.hire.trakstar.com/jobs/job101/">foreign</a>'
                    if scenario == "inactive_first" or scenario == "inactive_later" and page == 2:
                        body = (
                            '<a href="https://recruiterbox.com/inactive-ats">Inactive account</a>'
                            " No longer using Trakstar Hire"
                        )
                else:
                    body = json.dumps(
                        {
                            "documents": [
                                {
                                    "id": f"00000000-0000-0000-0000-{i:012d}",
                                    "company": {
                                        "id": "999"
                                        if scenario == "foreign_company" and page == 2
                                        else "123"
                                    },
                                }
                                for i in identities
                            ],
                            "numPages": 0 if total == 0 else 2,
                            "currentPage": page,
                            "totalHits": total,
                            "rows": 100,
                            "start": (page - 1) * 100,
                        }
                    )
                key = str(request.url)
                pages[key], statuses[key] = body, status
                requests.append(key)
                return httpx.Response(status, text=body)

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                try:
                    result = await (
                        recruiterbox_monitor if provider == "recruiterbox" else jobcloud_monitor
                    ).discover({"board_url": source, "metadata": metadata}, client)
                    values = result.urls if hasattr(result, "urls") else result
                    expected = {
                        "error": False,
                        "truncated": bool(getattr(result, "truncated", False)),
                        "urls": sorted(values),
                    }
                except Exception as exc:
                    from src.core.monitors import BoardGoneError

                    expected = {"error": True, "gone": isinstance(exc, BoardGoneError)}
            out.append(
                {
                    "provider": provider,
                    "scenario": scenario,
                    "source": source,
                    "metadata": metadata,
                    "pages": pages,
                    "statuses": statuses,
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
