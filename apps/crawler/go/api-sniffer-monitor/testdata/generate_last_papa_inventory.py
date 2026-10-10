"""Freeze original complete Papa Johns pagination and atomic failures."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitors import papa_johns  # noqa: E402

structlog.configure(logger_factory=structlog.ReturnLoggerFactory())


def page(number: int, total: int = 14, pages: int = 7, ids: list[int] | None = None) -> str:
    if ids is None:
        ids = [number * 2 - 1, number * 2]
    return (
        f"<p>Found {total} jobs at Papa Johns</p>"
        + "".join(f'<a href="/job/{job}/delivery-driver/">Driver</a>' for job in ids)
        + f'<a href="?page_jobs={pages}">Last</a>'
    )


async def capture(mode: str) -> dict:
    record = {"name": mode, "responses": {}, "requests": []}

    async def handler(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params.get("page_jobs", "1"))
        source = page(number)
        if mode == "changed-total" and number == 3:
            source = page(number, total=15)
        if mode == "changed-pages" and number == 3:
            source = page(number, pages=8)
        if mode == "duplicate-prefix" and number == 7:
            source = page(number, ids=[1, 2])
        if mode == "missing-count" and number == 3:
            source = "<p>Unavailable inventory</p>"
        if mode == "zero":
            source = page(number, total=0, pages=1, ids=[])
        if mode == "zero-with-job":
            source = page(number, total=0, pages=1, ids=[1])
        record["requests"].append(str(request.url))
        record["responses"][str(request.url)] = source
        return httpx.Response(200, text=source, request=request)

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await papa_johns.discover({"board_url": papa_johns.BOARD_URL}, client)
        record["urls"] = sorted(jobs)
    except (ValueError, RuntimeError, httpx.HTTPError):
        record["error"] = True
    record["requests"].sort()
    return record


async def main() -> None:
    modes = [
        "complete",
        "changed-total",
        "changed-pages",
        "duplicate-prefix",
        "missing-count",
        "zero",
        "zero-with-job",
    ]
    cases = [await capture(mode) for mode in modes]
    Path(__file__).with_name("python_last_papa_inventory.json").write_text(
        json.dumps(cases, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"Original complete Papa Johns inventories frozen: {len(cases)}")


asyncio.run(main())
