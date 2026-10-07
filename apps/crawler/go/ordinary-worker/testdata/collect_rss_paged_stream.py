"""Freeze actual Python page traversal, batching and failure semantics."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.core.monitors.rss import discover_stream


def feed(
    start: int, count: int, *, invalid: int = 0, broken: bool = False, bad_summary: bool = False
) -> str:
    items = []
    for n in range(start, start + count):
        summary = "Engineer | Full Time | Zurich"
        if bad_summary and n == start + count - 1:
            summary = "Wrong title | Zurich"
        items.append(
            f"<item><link>https://example.com/job/{n}</link><title>Engineer</title><description>{summary}</description></item>"
        )
    items.extend("<item><title>No URL</title></item>" for _ in range(invalid))
    return "<rss><channel>" + "".join(items) + "</channel></rss>" + ("<broken" if broken else "")


async def collect() -> None:
    cases = []
    for name, preset, bodies, size, limit, structured in [
        ("generic-complete", "generic", [feed(0, 2), feed(2, 1)], 2, 3, False),
        (
            "generic-cross-page-batches",
            "generic",
            [feed(0, 150), feed(150, 150), feed(300, 1)],
            150,
            4,
            False,
        ),
        ("generic-empty-tail", "generic", [feed(0, 2), feed(2, 0)], 2, 3, False),
        (
            "generic-counts-invalid-items",
            "generic",
            [feed(0, 1, invalid=1), feed(1, 1)],
            2,
            3,
            False,
        ),
        ("generic-all-invalid-full-page", "generic", [feed(0, 0, invalid=2)], 2, 3, False),
        ("generic-repeated-page", "generic", [feed(0, 200), feed(0, 200)], 200, 3, False),
        (
            "generic-limit-preserves-prefix",
            "generic",
            [feed(0, 150), feed(150, 150)],
            150,
            2,
            False,
        ),
        ("generic-short-final-at-limit", "generic", [feed(0, 150), feed(150, 1)], 150, 2, False),
        (
            "generic-late-xml-crosses-batch",
            "generic",
            [feed(0, 150), feed(150, 100, broken=True)],
            150,
            3,
            False,
        ),
        (
            "summary-late-parser-crosses-batch",
            "generic",
            [feed(0, 150), feed(150, 100, bad_summary=True)],
            150,
            3,
            True,
        ),
        ("summary-complete", "generic", [feed(0, 2), feed(2, 1)], 2, 3, True),
        ("wp-default-pages", "wp_job_manager", [feed(0, 10), feed(10, 1)], 10, 0, False),
        ("wp-repeated-page", "wp_job_manager", [feed(0, 10), feed(0, 10)], 10, 0, False),
    ]:
        endpoint = "https://example.com/feed?feed=job_feed&page=old&duplicate=first&duplicate=second&empty="
        metadata = {"preset": preset, "feed_url": endpoint}
        if preset == "generic":
            metadata["pagination"] = {
                "param_name": "page",
                "start": 2,
                "increment": 2,
                "page_size": size,
                "max_pages": limit,
            }
        if structured:
            metadata["description_mode"] = "title_employment_location"
        requests = []
        batches = []
        error = None

        async def handle(
            request: httpx.Request, *, requests=requests, bodies=bodies
        ) -> httpx.Response:
            requests.append(str(request.url))
            assert len(requests) <= len(bodies)
            return httpx.Response(200, text=bodies[len(requests) - 1])

        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                async for batch in discover_stream(
                    {"board_url": endpoint, "metadata": metadata}, client
                ):
                    batches.append([job.url for job in batch])
        except Exception as exc:
            error = type(exc).__name__
        cases.append(
            {
                "name": name,
                "metadata": metadata,
                "bodies": bodies,
                "requests": requests,
                "batches": batches,
                "error": error,
            }
        )
    Path(__file__).with_name("python_rss_paged_stream.json").write_text(
        json.dumps(cases, indent=2, ensure_ascii=False) + "\n"
    )


asyncio.run(collect())
