"""Freeze actual streamed NextData inventory, page requests and failure prefixes."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.monitors.nextdata import discover_stream

BASE = "https://example.com/careers"
FIELDS = {"title": "title", "description": "body", "locations": "city"}
DEFAULT = {"path": "jobs", "url_template": "https://example.com/jobs/{id}", "fields": FIELDS}


def page(jobs, **extra):
    return '<script id="__NEXT_DATA__">' + json.dumps({"jobs": jobs, **extra}) + "</script>"


def row(n):
    return {"id": n, "title": "Engineer", "body": "<p>Build.</p>", "city": "Zurich"}


def response(body, status=200, headers=None):
    return {"body": body, "status": status, "headers": headers or {}}


cases = []


def add(name, metadata, pages, base=BASE):
    cases.append((name, {**DEFAULT, **metadata}, pages, base))


add("rich", {}, {BASE: response(page([row(90071992547409931234)]))})
add("url-only-dedup", {"fields": {}}, {BASE: response(page([row(1), row(1), None]))})
add("missing-lenient", {}, {BASE: response("No embedded data")})
add("missing-strict", {"strict_path": True}, {BASE: response("No embedded data")})
add("wrong-path-lenient", {"path": "missing"}, {BASE: response(page([row(1)]))})
add(
    "title",
    {"expected_page_title": "Fixture & Careers"},
    {BASE: response("<title>Fixture &amp; Careers</title>" + page([row(1)]))},
)
add(
    "wrong-title",
    {"expected_page_title": "Fixture Careers"},
    {BASE: response("<title>Wrong tenant</title>" + page([row(1)]))},
)
add("non200-lenient", {}, {BASE: response("maintenance", 503)})
add("non200-policy", {}, {BASE: response("maintenance", 403, {"TDM-Reservation": "1"})})
add(
    "header-policy",
    {},
    {
        BASE: response(
            "malformed",
            headers={"TDM-Reservation": "1", "TDM-Policy": "https://example.com/policy"},
        )
    },
)
add("meta-policy", {}, {BASE: response('<meta name="tdm-reservation" content="1">')})
paginate = {"pagination": {"path": "pagination", "page_count": "pages"}}
add(
    "pages",
    paginate,
    {
        BASE: response(page([row(1)], pagination={"pages": 3})),
        BASE + "?page=2": response(page([row(2)])),
        BASE + "?page=3": response(page([row(3)])),
    },
)
add(
    "failed-later-prefix",
    paginate,
    {
        BASE: response(page([row(1)], pagination={"pages": 2})),
        BASE + "?page=2": response("missing payload"),
    },
)
add(
    "reserved-later-prefix",
    paginate,
    {
        BASE: response(page([row(1)], pagination={"pages": 2})),
        BASE + "?page=2": response("missing payload", headers={"TDM-Reservation": "1"}),
    },
)
add(
    "retry-required-page",
    paginate,
    {
        BASE: response(page([row(1)], pagination={"pages": 2})),
        BASE + "?page=2": [response("missing payload"), response(page([row(2)]))],
    },
)
add(
    "empty-authoritative",
    {"pagination": {"path": "pagination", "total_records": "total", "page_size": 2}},
    {BASE: response(page([], pagination={"total": 0}))},
)
add("empty-unproven", paginate, {BASE: response(page([], pagination={"pages": 2}))})
add(
    "duplicate-total",
    {"pagination": {"path": "pagination", "total_records": "total", "page_size": 1}},
    {
        BASE: response(page([row(1)], pagination={"total": 2})),
        BASE + "?page=2": response(page([row(1)])),
    },
)
add(
    "offset",
    {
        "source": "phenom_canvas",
        "pagination": {
            "mode": "offset",
            "path": "pagination",
            "total_records": "total",
            "page_size": 1,
            "offset_param": "from",
        },
    },
    {
        BASE: response(
            "phApp.ddo = " + json.dumps({"jobs": [row(1)], "pagination": {"total": 2}}) + ";"
        ),
        BASE + "?from=1": response("phApp.ddo = " + json.dumps({"jobs": [row(2)]}) + ";"),
    },
)
query_base = BASE + "?z=first&lang=en&z=second&empty="
add(
    "query-order-zero-start",
    {"pagination": {"path": "pagination", "page_count": "pages", "start": 0}},
    {
        query_base: response(page([row(1)], pagination={"pages": 2})),
        BASE + "?z=first&z=second&lang=en&empty=&page=1": response(page([row(2)])),
    },
    query_base,
)
add(
    "path-template",
    {
        "pagination": {
            "path": "pagination",
            "page_count": "pages",
            "url_template": "https://example.com/careers/page/{page}",
        }
    },
    {
        BASE: response(page([row(1)], pagination={"pages": 2})),
        BASE + "/page/2": response(page([row(2)])),
    },
)
add(
    "item-inclusion",
    {"include_item_values": {"company": ["Fixture"]}},
    {BASE: response(page([{**row(1), "company": "Fixture"}, {**row(2), "company": "Other"}]))},
)
add(
    "item-requirement",
    {"require_item_values": {"tenant": ["Fixture"]}},
    {BASE: response(page([{**row(1), "tenant": ["Fixture"]}]))},
)
add(
    "wrong-item-requirement",
    {"require_item_values": {"tenant": ["Fixture"]}},
    {BASE: response(page([{**row(1), "tenant": ["Other"]}]))},
)
add(
    "wrong-url-witness",
    {"url_allowlist": r"https://example\.com/other/[0-9]+"},
    {BASE: response(page([row(1)]))},
)
many = {BASE: response(page([row(1)], pagination={"pages": 13}))}
many.update({BASE + f"?page={n}": response(page([row(n)])) for n in range(2, 14)})
add("stream-groups-of-ten", paginate, many)


async def main():
    output = []
    for name, metadata, pages, base in cases:
        requests, chunks, calls = [], [], {}

        def respond(request, pages=pages, requests=requests, calls=calls):
            url = str(request.url)
            requests.append({"method": request.method, "url": url})
            calls[url] = calls.get(url, 0) + 1
            entry = pages.get(url, response("unknown fixture URL", 500))
            if isinstance(entry, list):
                entry = entry[min(calls[url] - 1, len(entry) - 1)]
            return httpx.Response(entry["status"], text=entry["body"], headers=entry["headers"])

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            error = False
            try:
                async for chunk in discover_stream(
                    {"board_url": base, "metadata": metadata}, client
                ):
                    chunks.append(
                        sorted(chunk) if isinstance(chunk, set) else [asdict(job) for job in chunk]
                    )
            except Exception:
                error = True
        output.append(
            {
                "name": name,
                "board_url": base,
                "metadata": metadata,
                "pages": pages,
                "requests": sorted(requests, key=lambda r: (r["url"], r["method"])),
                "expected": {"error": error, "chunks": chunks},
            }
        )
    Path(__file__).with_name("python_nextdata.json").write_text(
        json.dumps({"cases": output}, indent=2, ensure_ascii=False) + "\n"
    )


asyncio.run(main())
