"""Freeze actual Eightfold PCSX/watermark parsers and detail HTTP behavior."""

from __future__ import annotations

import asyncio
import json
import runpy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import httpx

from src.core.monitors import _pcsx, _watermark
from src.core.monitors import eightfold as monitor
from src.core.scrapers import eightfold

HERE = Path(__file__).parent
fixture = runpy.run_path(str(HERE.parents[2] / "tests" / "test_eightfold_scraper.py"))
URL = "https://citi.eightfold.ai/careers/job/859033176537-engineer?domain=citi.com"
API = "https://citi.eightfold.ai/api/apply/v2/jobs/859033176537?domain=citi.com"
NOW = datetime(2026, 10, 6, 3, 0, 0, 120000, tzinfo=UTC)
POSITION = {
    "name": "canonical",
    "posting_name": "Software Engineer",
    "job_description": "<p>Build services.</p>",
    "locations": [" London ", "Paris"],
    "t_create": 1769040000,
    "ats_job_id": "123",
    "department": "Engineering",
    "business_unit": "Products",
    "display_job_id": "REF123",
}
PCSX = {
    "name": "Engineer",
    "positionUrl": "/careers/job/859033176537",
    "standardizedLocations": ["London, UK"],
    "workLocationOption": "HYBRID",
    "postedTs": 1769040000,
    "department": "Engineering",
    "atsJobId": "123",
}
out = {"pcsx": [], "detail": [], "watermark": [], "routes": [], "http": [], "monitor": []}
for name, raw in [
    ("complete", POSITION),
    ("empty", {}),
    ("name-fallback", {**POSITION, "posting_name": ""}),
    ("scalar-location", {"location": " London "}),
    ("empty-locations-fallback", {"locations": [], "location": "Paris"}),
    ("location-coercion", {"locations": [None, False, 0, True, 1, " ", {"name": "Paris"}]}),
    *[
        (f"timestamp-{i}", {"t_create": value})
        for i, value in enumerate(
            [None, 0, -1, True, False, 1769040000.9, "1769040000", "bad", 1e20]
        )
    ],
    ("structured-metadata", {"department": {"name": "Research"}, "ats_job_id": 123}),
]:
    out["detail"].append(
        {"name": name, "raw": raw, "expected": asdict(eightfold._parse_position_api(raw))}
    )
for name, raw in [
    ("complete", PCSX),
    ("empty", {}),
    *[
        (f"locations-{i}", {**PCSX, "standardizedLocations": value})
        for i, value in enumerate(
            [None, [], '["London","Paris"]', '"London"', "broken", ["Paris", 1, None]]
        )
    ],
    *[
        (f"timestamp-{i}", {**PCSX, "postedTs": value})
        for i, value in enumerate([None, 0, -1, True, "1769040000", "bad", 1769040000.9, 1e20])
    ],
    ("empty-metadata", {**PCSX, "department": "", "atsJobId": None}),
    ("numeric-title", {**PCSX, "name": 123}),
]:
    out["pcsx"].append(
        {
            "name": name,
            "raw": raw,
            "url": URL,
            "expected": asdict(_pcsx.pcsx_to_discovered(raw, URL)),
        }
    )
for name, raw in [
    ("first", {}),
    ("malformed-state", {"pcsx_watermark": []}),
    ("invalid-max", {"pcsx_watermark": {"max_ts": "bad"}}),
    *[
        (
            f"age-{i}",
            {
                "pcsx_watermark": {
                    "max_ts": 123,
                    "last_full_at": value,
                    "last_incremental_at": "2026-10-05T03:00:00.120000+00:00",
                    "enabled": True,
                    "extra": {"host": "citi.eightfold.ai", "domain": "citi"},
                }
            },
        )
        for i, value in enumerate(
            [None, "bad", "2026-09-29T03:00:00.120000Z", "2026-10-05T03:00:00.120000+00:00"]
        )
    ],
    ("manual", {"pcsx_watermark": {"auto_full_crawl": False, "enabled": True}}),
    ("disabled", {"pcsx_watermark": {"enabled": False, "interval_days": 3}}),
    ("bool-interval", {"pcsx_watermark": {"interval_days": True}}),
    ("float-interval", {"pcsx_watermark": {"interval_days": 2.0}}),
    ("negative-max", {"pcsx_watermark": {"max_ts": -1}}),
]:
    row = {"name": name, "metadata": raw, "now": NOW.isoformat()}
    try:
        wm = _watermark.read(raw, "pcsx_watermark")
        row.update(patch=_watermark.to_metadata_patch(wm), needs_full=wm.needs_full_crawl(now=NOW))
    except (ValueError, TypeError):
        row["error"] = True
    out["watermark"].append(row)
for source in [
    URL,
    URL.replace("domain=", "Domain="),
    URL.split("?")[0],
    "https://careers.example.com/careers/job/123-title?domain=example.com",
    "https://careers.example.com/careers/job/123-title",
    "https://citi.eightfold.ai/careers",
]:
    out["routes"].append(
        {
            "url": source,
            "id": _pcsx.parse_job_id(source),
            "domain": eightfold._parse_domain(source),
            "pcsx_domain": (_pcsx.extract_host_and_domain([source]) or (None, None))[1],
        }
    )


def response(body, status=200, headers=None):
    return {
        "body": json.dumps(body) if isinstance(body, (dict, list)) else body,
        "status": status,
        "headers": headers or {},
    }


cases = []


def case(
    name,
    html="<html>shell</html>",
    api=POSITION,
    html_status=200,
    api_status=200,
    html_headers=None,
    api_headers=None,
    config=None,
    **flags,
):
    cases.append(
        {
            "name": name,
            "url": URL,
            "config": config or {},
            "pages": {
                URL: response(html, html_status, html_headers),
                API: response(api, api_status, api_headers),
            },
            **flags,
        }
    )


case("jsonld-fastpath", html=fixture["_JSONLD_HTML"])
for reserved in [False, True]:
    target = URL + "&redirected=1"
    case("html-redirect-reserved" if reserved else "html-redirect", native_reserved=reserved)
    cases[-1]["pages"][URL] = response("", 302, {"Location": target})
    cases[-1]["pages"][target] = response(
        fixture["_JSONLD_HTML"], headers={"TDM-Reservation": "1"} if reserved else {}
    )
case("api-redirect-refused", api_status=302, api_headers={"Location": API + "&redirected=1"})
case("api-fallback")
case("html-gone-api-recovers", html_status=404)
case("html-blocked-api-recovers", html_status=403)
case("no-content", api={})
case("api-bad-json", api="bad")
case("api-list", api=[])
case("api-gone", api_status=404)
case("api-201", api_status=201)
case("description-only", api={"job_description": "<p>Recover body</p>"})
case("title-only", api={"name": "Engineer"})
case(
    "missing-title-merge",
    html='<script type="application/ld+json">'
    + json.dumps(
        {
            "@type": "JobPosting",
            "description": "<p>Primary description</p>",
            "identifier": {"value": "primary"},
            "datePosted": "2026-10-01",
            "jobLocation": {"address": {"addressLocality": "London"}},
        }
    )
    + "</script>",
)
case("defaults-stop-fallback", config={"defaults": {"title": "Configured title"}})
case("html-reserved", html_headers={"TDM-Reservation": "1"}, native_reserved=True)
case("api-reserved", api_headers={"TDM-Reservation": "1"}, native_reserved=True)


async def collect():
    for row in cases:
        requests = []

        def handler(request, *, requests=requests, row=row):
            source = str(request.url)
            requests.append({"method": request.method, "url": source})
            if source not in row["pages"]:
                raise AssertionError("undeclared reference resource")
            value = row["pages"][source]
            return httpx.Response(value["status"], text=value["body"], headers=value["headers"])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            row["expected"] = asdict(await eightfold.scrape(row["url"], row["config"], client))
        row["requests"] = requests
        out["http"].append(row)

    async def no_wait(_):
        return None

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    board = "https://careers.kering.com"
    sitemap = board + "/careers/sitemap.xml"
    sources = [board + f"/careers/job/{key}-engineer?domain=kering" for key in [111, 222, 333]]
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(
            "<url><loc>" + source.replace("&", "&amp;") + "</loc></url>" for source in sources
        )
        + "</urlset>"
    )

    def search(offset=0, num=10):
        return (
            board
            + "/api/pcsx/search?"
            + urlencode(
                {
                    "domain": "kering",
                    "query": "",
                    "location": "",
                    "start": offset,
                    "num": num,
                }
            )
        )

    def positions(rows):
        return response({"data": {"positions": rows}})

    def pos(key, ts=1769040000, **overrides):
        return {**PCSX, "positionUrl": f"/careers/job/{key}", "postedTs": ts, **overrides}

    recent = {
        "max_ts": 1769040000,
        "last_full_at": "2026-10-05T03:00:00.120000+00:00",
        "enabled": True,
    }
    base = {
        sitemap: response(xml),
        search(num=1): positions([pos(111)]),
        search(): positions([pos(111), pos(222)]),
        search(10): positions([]),
    }
    monitor_cases = []

    def add(name, metadata=None, pages=None, **flags):
        monitor_cases.append(
            {
                "name": name,
                "board_url": board,
                "now": NOW.isoformat(),
                "metadata": metadata or {},
                "pages": pages or base,
                **flags,
            }
        )

    add("first-full")
    add("incremental", {"pcsx_watermark": recent})
    add("weekly-full", {"pcsx_watermark": {**recent, "last_full_at": "2026-09-28T03:00:00Z"}})
    add("force-full", {"pcsx_watermark": recent, "pcsx_force_full_crawl": True})
    add("manual-first", {"pcsx_watermark": {"auto_full_crawl": False, "enabled": True}})
    add(
        "manual-forced",
        {"pcsx_watermark": {"auto_full_crawl": False}, "pcsx_force_full_crawl": True},
    )
    add("cached-disabled", {"pcsx_watermark": {**recent, "enabled": False}})
    add(
        "probe-disabled",
        pages={
            sitemap: response(xml),
            search(num=1): response({"message": "PCSX is not enabled for this user."}, 403),
        },
    )
    add(
        "probe-403-transient",
        pages={sitemap: response(xml), search(num=1): response({"message": "Denied"}, 403)},
    )
    add("probe-405-transient", pages={sitemap: response(xml), search(num=1): response({}, 405)})
    add("probe-503-transient", pages={sitemap: response(xml), search(num=1): response({}, 503)})
    add("probe-invalid-json", pages={sitemap: response(xml), search(num=1): response("bad")})
    add("probe-invalid-shape", pages={sitemap: response(xml), search(num=1): response([])})
    add(
        "probe-retries-recover",
        pages={**base, search(num=1): [response({}, 503), positions([pos(111)])]},
    )
    add(
        "fetch-disabled",
        {"pcsx_watermark": recent},
        {
            sitemap: response(xml),
            search(): response({"message": "PCSX is not enabled for this user."}, 403),
        },
    )
    add(
        "fetch-stable-405",
        {"pcsx_watermark": recent},
        {sitemap: response(xml), search(): response({}, 405)},
    )
    add(
        "fetch-transient",
        {"pcsx_watermark": recent},
        {sitemap: response(xml), search(): response({}, 503)},
    )
    add(
        "fetch-prefix-failure",
        {"pcsx_watermark": recent},
        {
            sitemap: response(xml),
            search(): positions([pos(111, 1769040100)]),
            search(10): response({}, 503),
        },
    )
    add(
        "fetch-missing-data",
        {"pcsx_watermark": recent},
        {sitemap: response(xml), search(): response({})},
    )
    add(
        "unmatched-does-not-advance",
        pages={**base, search(): positions([pos(111), pos(999, 1769050000)])},
    )
    add(
        "last-rich-row-wins",
        pages={**base, search(): positions([pos(111, name="First"), pos(111, name="Last")])},
    )
    add(
        "missing-ts",
        {"pcsx_watermark": recent},
        {**base, search(): positions([pos(111, postedTs=None)])},
    )
    add(
        "empty-sitemap",
        pages={sitemap: response('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>')},
    )
    add(
        "sitemap-reserved",
        pages={sitemap: response(xml, headers={"TDM-Reservation": "1"})},
        native_reserved=True,
    )
    add(
        "probe-reserved",
        pages={
            **base,
            search(num=1): response({"data": {"positions": []}}, headers={"TDM-Reservation": "1"}),
        },
        native_reserved=True,
    )
    add(
        "fetch-reserved",
        {"pcsx_watermark": recent},
        {**base, search(): response({"data": {"positions": []}}, headers={"TDM-Reservation": "1"})},
        native_reserved=True,
    )
    # Every old page must be followed by three more old pages. A new page resets
    # that safety counter; missing timestamps also keep pagination running.
    pages = {sitemap: response(xml)}
    for i, ts in enumerate(
        [1769040000, 1769040000, 1769040200, 1769040000, 1769040000, 1769040000, 1769040000]
    ):
        pages[search(i * 10)] = positions([pos(111, ts)])
    add("boundary-jitter-resets-safety", {"pcsx_watermark": recent}, pages)

    for row in monitor_cases:
        requests, counts = [], {}

        def handler(request, *, requests=requests, row=row, counts=counts):
            source = str(request.url)
            requests.append({"method": request.method, "url": source})
            if source not in row["pages"]:
                raise AssertionError(f"undeclared monitor reference resource: {source}")
            value = row["pages"][source]
            if isinstance(value, list):
                index = counts.get(source, 0)
                counts[source] = index + 1
                value = value[min(index, len(value) - 1)]
            return httpx.Response(value["status"], text=value["body"], headers=value["headers"])

        with (
            patch.object(monitor, "datetime", Clock),
            patch.object(_pcsx.asyncio, "sleep", no_wait),
        ):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                try:
                    result = await monitor.discover(
                        {"board_url": board, "metadata": row["metadata"]}, client
                    )
                    row["expected"] = {
                        "urls": sorted(result.urls),
                        "rich": {
                            key: asdict(value) for key, value in (result.jobs_by_url or {}).items()
                        },
                        "patch": result.metadata_updates or None,
                        "hybrid": result.hybrid,
                    }
                except Exception as exc:
                    row["expected"] = {"error": type(exc).__name__}
        row["requests"] = requests
        out["monitor"].append(row)


asyncio.run(collect())
(HERE / "python_eightfold.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: len(value) for key, value in out.items()}))
