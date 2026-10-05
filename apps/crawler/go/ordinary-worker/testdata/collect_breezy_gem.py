"""Freeze the actual Breezy and Gem monitors using in-memory public responses."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.enum_normalize import _JOB_LOCATION_TYPE_MAP, normalize_job_location_type
from src.core.monitors.breezy import discover as breezy
from src.core.monitors.gem import discover as gem

cases = [
    (
        "breezy-relative",
        "breezy",
        [
            {"url": "/p/101-engineer"},
            {"friendly_id": "102-manager"},
            {"url": " /p/101-engineer "},
            None,
            {"url": ""},
        ],
    ),
    ("breezy-external", "breezy", [{"url": "https://example.com/jobs/103#apply"}]),
    ("breezy-invalid-envelope", "breezy", {"jobs": []}),
    ("breezy-empty", "breezy", []),
    (
        "gem-rich",
        "gem",
        [
            {
                "absolute_url": "https://jobs.gem.com/fixture/101",
                "title": "Engineer",
                "content": "<p>Build useful things.</p>",
                "offices": [
                    {"location": {"name": "Zurich"}},
                    {"name": "Remote"},
                    {"location": {"name": "Zurich"}},
                ],
                "employment_type": "full_time",
                "location_type": "remote",
                "departments": [{"name": "Engineering"}, {"name": "Product"}],
                "first_published_at": "2026-10-01T10:00:00Z",
            }
        ],
    ),
    (
        "gem-location-fallback",
        "gem",
        [
            {
                "absolute_url": "https://jobs.gem.com/fixture/102",
                "title": "Manager",
                "location": {"name": "Basel"},
                "location_type": "Onsite (5 Days per Week)",
            },
            {
                "absolute_url": "https://jobs.gem.com/fixture/103",
                "location": "Paris",
                "location_type": "unknown",
            },
            {"title": "Missing URL"},
        ],
    ),
    (
        "gem-falsy-fields",
        "gem",
        [{"absolute_url": value} for value in [None, "", False, 0, 0.0, [], {}]]
        + [
            {"absolute_url": f"https://jobs.gem.com/fixture/falsy-{i}", "employment_type": value}
            for i, value in enumerate([None, "", False, 0, 0.0, [], {}])
        ],
    ),
    ("gem-non-list", "gem", {"error": "Unavailable"}),
    ("gem-empty", "gem", []),
]


async def main():
    out = []
    for name, provider, payload in cases:
        endpoint = (
            "https://fixture.breezy.hr/json"
            if provider == "breezy"
            else "https://api.gem.com/job_board/v0/fixture/job_posts/"
        )
        requests = []

        def respond(request, requests=requests, endpoint=endpoint, payload=payload):
            requests.append({"method": request.method, "url": str(request.url)})
            assert str(request.url) == endpoint
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            try:
                result = await (breezy if provider == "breezy" else gem)(
                    {
                        "board_url": "https://fixture.breezy.hr"
                        if provider == "breezy"
                        else "https://jobs.gem.com/fixture",
                        "metadata": {},
                    },
                    client,
                )
                expected = {
                    "error": False,
                    "urls": sorted(result) if provider == "breezy" else None,
                    "jobs": [asdict(x) for x in result] if provider == "gem" else None,
                }
            except Exception:
                expected = {"error": True}
        out.append(
            {
                "name": name,
                "provider": provider,
                "endpoint": endpoint,
                "payload": payload,
                "requests": requests,
                "expected": expected,
            }
        )
    target = Path(__file__).with_name("python_breezy_gem.json")
    target.write_text(
        json.dumps(
            {
                "cases": out,
                "location_types": [
                    {"raw": s, "expected": normalize_job_location_type(s)}
                    for s in [*_JOB_LOCATION_TYPE_MAP, "Onsite (5 Days per Week)", "unknown", ""]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


asyncio.run(main())
