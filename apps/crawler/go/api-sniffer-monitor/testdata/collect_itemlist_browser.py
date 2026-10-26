"""Freeze the configured expression in real offline Chromium and Python discovery."""

from __future__ import annotations

import asyncio
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
        r"const ItemListBrowserExpression = (\"[^\n]+\")",
        (HERE.parent / "itemlist_browser.go").read_text(),
    )[1]
)
METADATA = {
    "source": "browser",
    "browser_expression": EXPRESSION,
    "path": "jobs",
    "url_template": "{url}",
}


def script(value, kind="application/ld+json"):
    return f'<script type="{kind}">' + json.dumps(value, ensure_ascii=False) + "</script>"


def listing(items):
    return script({"@type": "ItemList", "itemListElement": items})


def job(name="Engineer", **extra):
    return {"url": "https://example.com/jobs/one", "name": name, **extra}


CASES = [
    ("one-job", listing([job()])),
    ("talent-pool-filter", listing([job("POOL DE TALENTOS"), job()])),
    ("explicit-empty", listing([])),
    ("first-list", listing([]) + listing([job()])),
    ("organization-first", script({"@type": "Organization"}) + listing([job()])),
    ("template-ignored", "<template>" + listing([]) + "</template>" + listing([job()])),
    (
        "case-sensitive-script-type",
        script({"@type": "ItemList", "itemListElement": [job()]}, "Application/LD+JSON"),
    ),
    ("first-valid-before-null", listing([job()]) + script(None)),
    ("null-before-valid", script(None) + listing([job()])),
    (
        "later-malformed-script",
        listing([job()]) + '<script type="application/ld+json">bad</script>',
    ),
    ("missing-list", script({"@type": "Organization"})),
    ("missing-elements", script({"@type": "ItemList"})),
    ("null-row", listing([job(), None])),
    ("missing-name", listing([{"url": "https://example.com/jobs/one"}])),
    ("array-type", script({"@type": ["ItemList"], "itemListElement": []})),
    ("number-rounding", listing([job(id=9007199254740993)])),
    ("unicode-name", listing([job("Ingeniería — 工程师")])),
    ("non-unicode-regex-casefold", listing([job("pool de talentoſ")])),
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
                            chunks.append(
                                sorted(
                                    [
                                        {"url": job} if isinstance(job, str) else asdict(job)
                                        for job in chunk
                                    ],
                                    key=lambda row: row["url"],
                                )
                            )
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
    (HERE / "python_itemlist_browser.json").write_text(
        json.dumps(
            {"expression": EXPRESSION, "metadata": METADATA, "cases": output},
            indent=2,
            ensure_ascii=False,
            default=str,
        )
        + "\n"
    )


asyncio.run(main())
