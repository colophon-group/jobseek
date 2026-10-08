"""Freeze selector-list inventory through original Python DOM HTTP discovery."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import httpx

from src.core.monitors.dom import _dom_discover_once
from src.processing.board import _prepare_discovered_sources

BOARD = "https://example.com/careers"
HTML = (
    '<h3 class="elementor-post__title"><a class="role" href="/jobs/1">One</a></h3>'
    '<h4 class="entry-title"><a class="role" href="/jobs/2">Two</a></h4>'
    '<a href="/other">Other</a>'
)
JOB = '<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer"}</script>'


async def main():
    cases = []
    for name, selector, verify in [
        (
            "wordpress-two-layouts",
            "h3.elementor-post__title a[href], h4.entry-title a[href]",
            False,
        ),
        ("reversed-group-order", "h4.entry-title a[href], h3.elementor-post__title a[href]", False),
        ("overlapping-group", "a.role[href], h3.elementor-post__title a[href]", False),
        ("group-with-no-matches", "a.missing, h4.missing a", False),
        ("attribute-group", 'a[href="/jobs/1"], a[href="/jobs/2"]', False),
        ("grouped-jsonld-verification", "h3 a[href], h4 a[href]", True),
    ]:
        metadata = {"link_selector": selector, "url_filter": "/jobs/"}
        if verify:
            metadata["require_jsonld_jobposting"] = True
        calls = []

        def handler(request, *, calls=calls):
            calls.append(str(request.url))
            return httpx.Response(
                200,
                text=HTML if request.url.path == "/careers" else JOB,
                headers={"content-type": "text/html; charset=utf-8"},
            )

        row = {
            "name": name,
            "board_url": BOARD,
            "metadata": metadata,
            "listing": HTML,
            "details": {path: {"status": 200, "body": JOB} for path in ["/jobs/1", "/jobs/2"]},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            found = await _dom_discover_once({"board_url": BOARD, "metadata": metadata}, client)
            row["expected"] = sorted(found)
            processed, drops, _ = _prepare_discovered_sources(
                SimpleNamespace(urls=sorted(found), jobs_by_url=None), BOARD, set()
            )
            row["processing_expected"] = sorted(item[0] for item in processed)
            row["drop_reasons"] = drops
            row["calls"] = calls
        cases.append(row)
    Path(__file__).with_name("python_selector_groups.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(cases)} original Python grouped-selector HTTP inventories")


if __name__ == "__main__":
    asyncio.run(main())
