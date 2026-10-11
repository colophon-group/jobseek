"""Freeze offline Chromium's Yum-style literal and original nextdata output."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx
from playwright.async_api import async_playwright

from src.core.monitors.nextdata import discover_stream

METADATA = {
    "source": "browser",
    "browser_expression": "({jobs: jobList})",
    "path": "jobs",
    "url_template": "{link}",
    "fields": {
        "title": "title",
        "description": "desc",
        "locations": "=Shanghai, China",
        "employment_type": "=full_time",
        "job_location_type": "=onsite",
    },
}
RECORD = r"""{type:"Track",title:'Software Engineer',desc:'<p>Build "reliable" systems. It\'s useful.</p>',link:"https://example.test/jobs/42",}"""
CASES = [
    ("mixed-quotes", "let jobList = [" + RECORD + "];"),
    ("empty", "let jobList = [];"),
    (
        "unicode",
        r"""let jobList = [{type:"Track",title:"\u5de5\u7a0b\u5e08",desc:"<p>\uD83D\uDE00</p>",link:"https://example.test/jobs/43"}];""",
    ),
    (
        "escaped",
        r"""let jobList = [{type:"Track",title:"Line\nTab\t",desc:"<p>Backslash \\ and quote \"</p>",link:"https://example.test/jobs/44"}];""",
    ),
    ("multiple", "let jobList = [" + RECORD + "," + RECORD.replace("/42", "/45") + "];"),
]


async def main():
    output = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        await context.route("**/*", lambda route: route.abort())
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(500))
        ) as client:
            for name, code in CASES:
                page = await context.new_page()
                await page.set_content("<script>" + code + "</script>")
                value = await page.evaluate(METADATA["browser_expression"])

                async def evaluate(*a, value=value, **kw):
                    return value

                jobs = []
                with patch("src.core.monitors.nextdata._evaluate_browser_data", evaluate):
                    async for batch in discover_stream(
                        {"board_url": "https://example.test/careers", "metadata": METADATA}, client
                    ):
                        jobs.extend(asdict(x) for x in batch)
                output.append({"name": name, "script": code, "browser_result": value, "jobs": jobs})
                await page.close()
        await browser.close()
    Path(__file__).with_name("python_yum_china_browser.json").write_text(
        json.dumps({"metadata": METADATA, "cases": output}, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(main())
