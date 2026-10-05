"""Freeze the configured expression in real offline Chromium and Python discovery."""

from __future__ import annotations

import asyncio
import html
import json
import re
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx
from playwright.async_api import async_playwright

from src.core.monitors.nextdata import discover_stream

HERE = Path(__file__).parent
EXPRESSION = json.loads(
    re.search(
        r"const FloridaCourtsBrowserExpression = (\"[^\n]+\")",
        (HERE.parent / "flcourts_browser.go").read_text(),
    )[1]
)
FIELDS = {
    "title": "position_title",
    "locations": "job_location",
    "description": {"concat": ["job_description.html5", "=Education", "education.html5"]},
    "metadata.closing_date": "closing_date",
    "metadata.position_number": "position_number",
}
METADATA = {
    "source": "browser",
    "browser_expression": EXPRESSION,
    "path": "items",
    "url_template": "{url}",
    "fields": FIELDS,
}


def record(link="/Services/Human-Resources/employment/Jobs/engineer", **fields):
    return {
        "location": {"url": link},
        "content": {
            "fields": {
                "position_title": "Senior Software Engineer",
                "job_location": ["Zurich", "Remote"],
                "job_description": {"html5": "<p>Build & maintain our platform.</p>"},
                "education": {"html5": "<p>Relevant experience.</p>"},
                "position_number": 9007199254740993,
                **fields,
            }
        },
    }


def listing(items):
    payload = html.escape(json.dumps({"items": items}), quote=True)
    return f'<div class="other JobListings_ibexa" data-content="{payload}"></div>'


def marker(markup):
    data = {"props": {"pageProps": {"pageData": {"description": {"html5": markup}}}}}
    return "<script type=\"application/json\" id='__NEXT_DATA__'>" + json.dumps(data) + "</script>"


CASES = [
    ("rich-number-rounding", marker(listing([record()]))),
    ("empty-inventory", marker(listing([]))),
    ("first-marker", marker(listing([record()])) + marker(listing([]))),
    ("template-marker", "<template>" + marker("bad") + "</template>" + marker(listing([record()]))),
    ("template-listing", marker("<template>" + listing([]) + "</template>" + listing([record()]))),
    ("first-listing", marker(listing([record()]) + listing([]))),
    ("inert-domparser-noscript", marker("<noscript>" + listing([record()]) + "</noscript>")),
    ("spread-overrides-url", marker(listing([record(url="https://example.com/overridden")]))),
    ("missing-marker", "<p>No data</p>"),
    ("malformed-inner-json", marker('<div class="JobListings_ibexa" data-content="bad"></div>')),
    ("missing-location", marker(listing([{"content": {"fields": {}}}]))),
    ("late-malformed-row", marker(listing([record(), None]))),
]


async def main():
    output = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route("**/*", lambda route: route.abort())
        page = await context.new_page()
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ) as client:
            for name, source in CASES:
                await page.set_content(source)
                result, failed = None, False
                try:
                    result = json.loads(await page.evaluate("JSON.stringify(" + EXPRESSION + ")"))
                except Exception:
                    failed = True

                async def evaluate(*args, failed=failed, result=result, **kwargs):
                    if failed:
                        raise RuntimeError("actual browser expression failed")
                    return result

                chunks, failure = [], False
                with patch("src.core.monitors.nextdata._evaluate_browser_data", evaluate):
                    try:
                        async for chunk in discover_stream(
                            {"board_url": "https://example.com/careers", "metadata": METADATA},
                            client,
                        ):
                            chunks.append([asdict(job) for job in chunk])
                    except Exception:
                        failure = True
                output.append(
                    {
                        "name": name,
                        "html": source,
                        "browser_result": result,
                        "error": failure,
                        "chunks": chunks,
                    }
                )
        await context.close()
        await browser.close()
    (HERE / "python_flcourts_browser.json").write_text(
        json.dumps(
            {"expression": EXPRESSION, "metadata": METADATA, "cases": output},
            indent=2,
            ensure_ascii=False,
            default=str,
        )
        + "\n"
    )


asyncio.run(main())
