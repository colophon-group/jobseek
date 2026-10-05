"""Freeze configured HTTP discovery against the existing Python implementation."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import httpx

from src.core.monitors.api_sniffer import _discover_http

BOARD = "https://careers.example.test/careers/"
API = "https://api.example.test/jobs"
FIELDS = {
    "title": "title",
    "description": "description",
    "locations": "locations[].name",
    "employment_type": {"path": "remote", "map": {"True": "full-time"}},
    "qualifications": "qualifications",
    "metadata.id": "id",
}
cases = []


def config(**kwargs):
    return {"api_url": API, "json_path": "jobs", "url_field": "url", "fields": FIELDS, **kwargs}


def item(i):
    return {
        "id": i,
        "title": f"Engineer {i}",
        "url": f"/jobs/{i}",
        "description": [f"<p>Role {i}</p>", "<p>Details</p>"],
        "locations": [{"name": "Zurich"}],
        "remote": True,
        "qualifications": ["Python", "Go"],
    }


async def run(name, metadata, pages):
    calls = []

    def handler(request):
        query = parse_qs(urlparse(str(request.url)).query, keep_blank_values=True)
        body = request.content.decode()
        calls.append(
            {
                "method": request.method,
                "url": str(request.url),
                "body": body,
                "headers": {
                    k: v for k, v in request.headers.items() if k in {"x-test", "content-type"}
                },
            }
        )
        values = {}
        if body:
            try:
                values = json.loads(body)
            except json.JSONDecodeError:
                values = parse_qs(body, keep_blank_values=True)
        page = int(
            query.get("page", query.get("offset", [values.get("page", values.get("offset", 0))]))[0]
        )
        size = int(query.get("limit", [values.get("limit", 0)])[0])
        selector = (page, size)
        status, data = pages.get(selector, pages.get((page, None), (200, {"jobs": []})))
        return httpx.Response(status, json=data)

    row = {"name": name, "board_url": BOARD, "metadata": metadata}
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        try:
            result = await _discover_http({"board_url": BOARD}, client, metadata)
            truncated = getattr(result, "truncated", False)
            jobs = result
            if hasattr(result, "jobs_by_url"):
                jobs = (
                    list(result.jobs_by_url.values())
                    if result.jobs_by_url is not None
                    else result.urls
                )
            if isinstance(jobs, set):
                out = [
                    {
                        "url": url,
                        "title": None,
                        "description": None,
                        "locations": None,
                        "employment_type": None,
                        "job_location_type": None,
                        "date_posted": None,
                        "metadata": {},
                        "extras": {},
                    }
                    for url in sorted(jobs)
                ]
            else:
                out = []
                for job in jobs:
                    data = dataclasses.asdict(job)
                    out.append(
                        {
                            k: (data.get(k) or {} if k in {"metadata", "extras"} else data.get(k))
                            for k in [
                                "url",
                                "title",
                                "description",
                                "locations",
                                "employment_type",
                                "job_location_type",
                                "date_posted",
                                "metadata",
                                "extras",
                            ]
                        }
                    )
            row.update(expected=out, truncated=truncated)
        except Exception as exc:
            row.update(error=True, error_class=type(exc).__name__)
    row["calls"] = calls
    row["responses"] = [
        {"page": page, "size": size, "status": status, "data": data}
        for (page, size), (status, data) in pages.items()
    ]
    cases.append(row)


async def main():
    await run("single-rich", config(), {(0, None): (200, {"jobs": [item(1), item(2)]})})
    await run("empty", config(), {(0, None): (200, {"jobs": []})})
    await run(
        "root-lookup",
        config(
            fields={
                "title": "title",
                "metadata.team": {"lookup_from": "lookup", "key_from": "department"},
            }
        ),
        {
            (0, None): (
                200,
                {"jobs": [dict(item(1), department=12)], "lookup": {"12": "Engineering"}},
            )
        },
    )
    await run(
        "multi-description",
        config(fields={"title": "title", "description": ["description", "=Tail"]}),
        {(0, None): (200, {"jobs": [item(1)]})},
    )
    await run(
        "nested-url",
        config(url_field="links.url"),
        {(0, None): (200, {"jobs": [dict(item(1), links={"url": "/other/1"})]})},
    )
    await run(
        "template-alias",
        config(
            url_template="https://careers.example.test/job/{external}",
            url_template_fields={"external": "id"},
        ),
        {(0, None): (200, {"jobs": [item(1)]})},
    )
    await run(
        "url-only",
        config(fields={}, url_template="/jobs/{id}"),
        {(0, None): (200, {"jobs": [item(1), item(2)]})},
    )
    await run(
        "duplicate",
        config(),
        {(0, None): (200, {"jobs": [item(1), dict(item(1), title="Updated")]})},
    )
    await run(
        "cap-keeps-all", config(max_items=1), {(0, None): (200, {"jobs": [item(1), item(2)]})}
    )
    await run(
        "advertised-gap",
        config(total_path="total"),
        {(0, None): (200, {"jobs": [item(1), item(2)], "total": 5})},
    )
    await run(
        "one-total-drift",
        config(total_path="total"),
        {(0, None): (200, {"jobs": [item(1), item(2)], "total": 3})},
    )
    pg = {"param_name": "page", "start_value": 0, "increment": 1, "max_pages": 4}
    await run(
        "page-pagination",
        config(pagination=pg),
        {
            (0, None): (200, {"jobs": [item(1), item(2)], "total": 4}),
            (1, None): (200, {"jobs": [item(3), item(4)]}),
        },
    )
    await run(
        "later-transient-fails-whole",
        config(pagination=pg),
        {(0, None): (200, {"jobs": [item(1), item(2)], "total": 4}), (1, None): (503, {})},
    )
    await run(
        "two-empty-pages",
        config(pagination=pg),
        {
            (0, None): (200, {"jobs": [item(1), item(2)]}),
            (1, None): (200, {"jobs": []}),
            (2, None): (200, {"jobs": []}),
        },
    )
    await run(
        "offset-pagination",
        config(pagination={**pg, "param_name": "offset", "style": "offset", "increment": 2}),
        {
            (0, None): (200, {"jobs": [item(1), item(2)], "total": 4}),
            (2, None): (200, {"jobs": [item(3), item(4)]}),
        },
    )
    await run(
        "post-pagination",
        config(
            method="POST",
            post_data={"page": 0},
            request_headers={"X-Test": "fixture"},
            pagination={**pg, "location": "body"},
        ),
        {
            (0, None): (200, {"jobs": [item(1), item(2)], "total": 4}),
            (1, None): (200, {"jobs": [item(3), item(4)]}),
        },
    )
    await run(
        "size-probe",
        config(
            params={"limit": 2},
            pagination={**pg, "param_name": "offset", "style": "offset", "increment": 2},
        ),
        {
            (0, 2): (200, {"jobs": [item(1), item(2)], "total": 6}),
            (0, 100): (200, {"jobs": [item(i) for i in range(1, 7)], "total": 6}),
        },
    )
    await run(
        "failed-size-probe",
        config(
            params={"limit": 2},
            pagination={**pg, "param_name": "offset", "style": "offset", "increment": 2},
        ),
        {
            (0, 2): (200, {"jobs": [item(1), item(2)], "total": 4}),
            (0, 100): (503, {}),
            (2, 2): (200, {"jobs": [item(3), item(4)]}),
        },
    )
    await run(
        "cumulative-limit",
        config(
            params={"limit": 2},
            pagination={**pg, "param_name": "limit", "style": "cumulative_limit"},
        ),
        {
            (0, 2): (200, {"jobs": [item(1), item(2)], "total": 4}),
            (0, 4): (200, {"jobs": [item(i) for i in range(1, 5)], "total": 4}),
        },
    )
    await run(
        "json-values",
        config(json_path_values=True),
        {(0, None): (200, {"jobs": {"1": item(1), "2": item(2)}})},
    )
    Path(__file__).with_name("python_inventory.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(cases)} Python HTTP discovery cases")


asyncio.run(main())
