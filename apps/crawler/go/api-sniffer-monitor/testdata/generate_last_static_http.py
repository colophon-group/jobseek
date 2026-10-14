"""Freeze request traces and fields from the three original HTTP scrapers."""

from __future__ import annotations

import asyncio
import json
import runpy
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.scrapers import onlyfy, phuketall, tupu360
from src.shared import http_retry

root = Path(__file__).resolve().parents[3]
fixtures = {
    name: runpy.run_path(str(root / "tests" / filename))
    for name, filename in {
        "onlyfy": "test_onlyfy.py",
        "phuketall": "test_phuketall_scraper.py",
        "tupu360": "test_tupu360_scraper.py",
    }.items()
}
sources = {
    "onlyfy": "https://example.onlyfy.jobs/de/job/abc123",
    "phuketall": "https://www.phuketall.com/jobs/057289-213282-phuket/213282.html",
    "tupu360": fixtures["tupu360"]["URL"],
}
bodies = {
    "onlyfy": fixtures["onlyfy"]["ONLYFY_CANDIDATE"],
    "phuketall": fixtures["phuketall"]["HTML"],
    "tupu360": fixtures["tupu360"]["HTML"],
}


async def main():
    traces = []

    async def capture(provider, name, statuses, pages=None, locations=None, config=None):
        config = config or {}
        pages = pages or [bodies[provider]]
        locations = locations or [""]
        calls = []
        waits = []

        async def sleep(delay):
            waits.append(delay)

        http_retry.asyncio.sleep = sleep
        http_retry.fetch_text_page_with_retry.__kwdefaults__["sleep"] = sleep

        def handle(request):
            i = len(calls)
            status = statuses[min(i, len(statuses) - 1)]
            body = pages[min(i, len(pages) - 1)]
            location = locations[min(i, len(locations) - 1)]
            calls.append(
                dict(
                    method=request.method,
                    url=str(request.url),
                    status=status,
                    body=body,
                    location=location,
                )
            )
            if status == 0:
                raise httpx.ConnectError("fixture", request=request)
            return httpx.Response(
                status, text=body, headers={"location": location}, request=request
            )

        content = None
        failed = False
        module = {"onlyfy": onlyfy, "tupu360": tupu360, "phuketall": phuketall}[provider]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            try:
                content = asdict(await module.scrape(sources[provider], config, client))
            except Exception:
                failed = True
        traces.append(
            dict(
                provider=provider,
                name=name,
                source=sources[provider],
                config=config,
                requests=calls,
                waits=len(waits),
                content=content,
                failed=failed,
            )
        )

    for provider in sources:
        for statuses in (
            [200],
            [201],
            [204],
            [400],
            [403],
            [404],
            [410],
            [429, 200],
            [503, 200],
            [500, 500, 500],
            [0, 200],
        ):
            await capture(provider, "-".join(map(str, statuses)), statuses)
        await capture(provider, "empty-exhaustion", [200], [""])
        await capture(provider, "empty-recovery", [200], ["", bodies[provider]])
        await capture(provider, "oversized", [200], ["x" * ((2 << 20) + 1)])
    await capture(
        "onlyfy",
        "shell-source-listing",
        [200],
        [
            fixtures["onlyfy"]["ONLYFY_SHELL"],
            fixtures["onlyfy"]["ONLYFY_CANDIDATE"],
            fixtures["onlyfy"]["_rsc_listing_html"](),
        ],
        config={"language": "de"},
    )
    await capture(
        "onlyfy",
        "substantive-shell-listing",
        [200],
        [fixtures["onlyfy"]["ONLYFY_SUBSTANTIVE_SHELL"], fixtures["onlyfy"]["_rsc_listing_html"]()],
    )
    await capture(
        "onlyfy",
        "source-404-listing",
        [200, 404, 200],
        ["", "", fixtures["onlyfy"]["_rsc_listing_html"]()],
    )
    for allow in (False, True):
        await capture(
            "tupu360",
            f"listing-location-{allow}",
            [200],
            [fixtures["tupu360"]["CUSTOM_HOST_HTML"]],
            config={"listing_enrichment": allow},
        )
    await capture(
        "tupu360",
        "foreign-position",
        [200],
        [bodies["tupu360"].replace(fixtures["tupu360"]["POSITION_ID"], "0" * 24)],
    )
    for name, statuses, locations in (
        ("trusted-redirect", [302, 200], [sources["phuketall"].replace("/jobs/", "/en/jobs/"), ""]),
        ("redirect-limit", [302], [sources["phuketall"]]),
        ("foreign-redirect", [302], ["https://foreign.example/job"]),
        (
            "different-job-redirect",
            [302],
            [sources["phuketall"].replace("213282-phuket", "213283-phuket")],
        ),
        ("missing-location", [302], [""]),
        (
            "transient-redirect-recovery",
            [503, 302, 503, 200],
            ["", sources["phuketall"].replace("/jobs/", "/en/jobs/"), "", ""],
        ),
    ):
        await capture("phuketall", name, statuses, locations=locations)
    await capture(
        "phuketall",
        "foreign-document",
        [200],
        [bodies["phuketall"].replace("057289-213282", "057289-999999")],
    )
    # Store oversized payloads by length, retaining the exact traced outcome.
    for trace in traces:
        for request in trace["requests"]:
            if len(request["body"]) > 2 << 20:
                request["body_size"] = len(request.pop("body"))
    target = Path(__file__).with_name("python_last_static_http.json")
    target.write_text(
        json.dumps(
            dict(sources=sources, requests=traces), ensure_ascii=False, sort_keys=True, indent=2
        )
        + "\n"
    )
    print(f"wrote {len(traces)} original HTTP traces")


asyncio.run(main())
