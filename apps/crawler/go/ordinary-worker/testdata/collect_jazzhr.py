"""Freeze actual JazzHR requests and inventory using public in-memory responses."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.core.monitors.jazzhr import discover

marker = '<div id="job_listings_wrapper">'
cases = [
    (
        "links",
        200,
        marker + '<a href="/apply/jobs/details/A1?utm=x#apply">A</a>'
        '<a href="/apply/jobs/details/A1/">dup</a><a href="/apply/jobs/details/B-2">B</a>'
        "<script>var html = '<a href=\"/apply/jobs/details/script\">hidden</a>';</script>"
        '<a href="https://other.applytojob.com/apply/jobs/details/foreign">foreign</a>'
        '<a href="https://fixture.applytojob.com:443/apply/jobs/details/port">port</a>'
        '<a href="/apply/jobs/details/%41">escaped</a>'
        '<a href="/apply/jobs/details/extra/path">extra</a>'
        '<a href="/APPLY/jobs/details/upper">upper</a>'
        '<a href="/apply/jobs/details/工程師">unicode</a>'
        '<a href="/apply/jobs/details/X_Y">X</a></div>',
        {},
    ),
    ("empty", 200, marker + "</div>", {}),
    ("missing-marker", 200, '<a href="/apply/jobs/details/A1">A</a>', {}),
    (
        "challenge",
        200,
        marker
        + '<title>Just a moment...</title><div id="cf-chl-widget">Checking your browser</div>',
        {},
    ),
    ("gone404", 404, "missing", {}),
    ("gone410", 410, "missing", {}),
    (
        "reserved",
        200,
        "not a listing",
        {"TDM-Reservation": "1", "TDM-Policy": "https://example.com/policy"},
    ),
]


async def main():
    output = []
    for name, status, body, headers in cases:
        requests = []

        def respond(request, requests=requests, status=status, body=body, headers=headers):
            requests.append({"method": request.method, "url": str(request.url)})
            return httpx.Response(status, text=body, headers=headers)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            try:
                result = await discover(
                    {"board_url": "https://fixture.applytojob.com/apply", "metadata": {}}, client
                )
                expected = {"error": False, "urls": sorted(result)}
            except Exception as exc:
                expected = {"error": True, "kind": type(exc).__name__}
        output.append(
            {
                "name": name,
                "status": status,
                "body": body,
                "headers": headers,
                "requests": requests,
                "expected": expected,
            }
        )
    Path(__file__).with_name("python_jazzhr.json").write_text(
        json.dumps({"cases": output}, indent=2, ensure_ascii=False) + "\n"
    )


asyncio.run(main())
