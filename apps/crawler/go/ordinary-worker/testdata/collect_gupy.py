"""Freeze actual Gupy NextData inventory and request semantics offline."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.core.monitors.gupy import discover


def page(jobs, **overrides):
    props = {"subdomain": "fixture", "careerPage": {}, "jobs": jobs, **overrides}
    return (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"props": {"pageProps": props}}, ensure_ascii=False)
        + "</script>"
    )


cases = [
    ("precise-ids", page([{"id": 90071992547409931234}, {"id": "123"}, {"id": "1٢"}]), {}),
    ("duplicates", page([{"id": 123}, {"id": "123"}]), {}),
    (
        "invalid-items",
        page(
            [
                None,
                42,
                {},
                {"id": False},
                {"id": 0},
                {"id": -1},
                {"id": 1.0},
                {"id": "01"},
                {"id": "1" * 21},
                {"id": 12},
            ]
        ),
        {},
    ),
    ("empty", page([]), {}),
    ("wrong-tenant", page([], subdomain="other"), {}),
    ("missing-career-page", page([], careerPage=None), {}),
    ("invalid-jobs", page({"id": 123}), {}),
    ("missing-nextdata", "<html>Not a career page</html>", {}),
    (
        "nonlegacy-marker",
        page([]).replace(
            '<script id="__NEXT_DATA__" type="application/json">',
            '<script type="application/json" id="__NEXT_DATA__">',
        ),
        {},
    ),
    (
        "reserved",
        "not NextData",
        {"TDM-Reservation": "1", "TDM-Policy": "https://example.com/policy"},
    ),
]


async def main():
    output = []
    for name, body, headers in cases:
        requests = []

        def respond(request, requests=requests, body=body, headers=headers):
            requests.append({"method": request.method, "url": str(request.url)})
            return httpx.Response(200, text=body, headers=headers)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            try:
                result = await discover(
                    {"board_url": "https://fixture.gupy.io/", "metadata": {}}, client
                )
                urls = result if isinstance(result, set) else result.urls
                expected = {
                    "error": False,
                    "urls": sorted(urls),
                    "truncated": getattr(result, "truncated", False),
                }
            except Exception as exc:
                expected = {"error": True, "kind": type(exc).__name__}
        output.append(
            {
                "name": name,
                "body": body,
                "headers": headers,
                "requests": requests,
                "expected": expected,
            }
        )
    Path(__file__).with_name("python_gupy.json").write_text(
        json.dumps({"cases": output}, indent=2, ensure_ascii=False) + "\n"
    )


asyncio.run(main())
