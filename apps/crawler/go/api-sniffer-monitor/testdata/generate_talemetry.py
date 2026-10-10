"""Freeze the original Talemetry monitor's complete inventories and failures."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitors import talemetry  # noqa: E402

structlog.configure(logger_factory=structlog.ReturnLoggerFactory())


async def capture(transport: str, mode: str) -> dict:
    root = "https://careers.example.com/search/jobs"
    record = {"name": f"{transport}-{mode}", "board_url": root, "responses": [], "requests": []}
    metadata = {"transport": "jobs_json"} if transport == "json" else {}
    record["metadata"] = metadata
    snapshots = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal snapshots
        page = int(request.url.params.get("page", "1"))
        if page == 1:
            snapshots += 1
        ids = [page * 2 - 1, page * 2] if page < 3 else [5]
        total, current, size = 5, page, 2
        changed = mode == "changed" or mode == "recover" and snapshots == 1
        if changed and page == 2:
            total = 6
        if mode == "duplicate" and page == 2:
            ids = [1, 4]
        if mode == "missing" and page == 2:
            ids = [3]
        if mode == "wrong-page" and page == 2:
            current = 1
        if mode in ("zero", "zero-with-job"):
            total = 0
            ids = [] if mode == "zero" else [1]
        if transport == "json":
            payload = {
                "total_entries": total,
                "per_page": size,
                "current_page": current,
                "entries": [
                    {"id": str(i), "talemetry_job_id": str(i), "permalink": f"role-{i}"}
                    for i in ids
                ],
            }
            if mode == "bad-id" and page == 2:
                payload["entries"][0]["talemetry_job_id"] = "999"
            if mode == "bad-slug" and page == 2:
                payload["entries"][0]["permalink"] = "../../foreign"
            if mode == "float-count":
                payload["total_entries"] = 5.0
            source = json.dumps(payload)
        else:
            start = (page - 1) * size + 1 if total else 0
            end = min(page * size, total)
            if mode == "wrong-page" and page == 2:
                start, end = 1, 2
            source = (
                "<script>window.talemetry={};</script>"
                + f"<p>Viewing {start}-{end} of {total} results</p>"
                + '<div class="jobs-section__list">'
                + "".join(f'<a href="/jobs/{i}-role-{i}?utm=a#x">Role</a>' for i in ids)
                + '<a href="https://foreign.example/jobs/900-foreign">Foreign</a></div>'
                + '<a href="/jobs/800-navigation">Navigation</a>'
            )
            if mode == "unmarked":
                source = source.replace("window.talemetry", "unknown")
        record["requests"].append(
            {
                "url": str(request.url),
                "headers": {
                    k: request.headers[k]
                    for k in talemetry._JOBS_JSON_HEADERS
                    if k in request.headers
                }
                if transport == "json"
                else {},
            }
        )
        record["responses"].append(source)
        return httpx.Response(200, text=source, request=request)

    with patch.object(talemetry.asyncio, "sleep", new=AsyncMock()):
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                record["urls"] = sorted(
                    await talemetry.discover({"board_url": root, "metadata": metadata}, client)
                )
        except (ValueError, RuntimeError, httpx.HTTPError):
            record["error"] = True
    return record


async def main() -> None:
    cases = []
    for transport in ("html", "json"):
        modes = [
            "complete",
            "changed",
            "recover",
            "duplicate",
            "missing",
            "wrong-page",
            "zero",
            "zero-with-job",
        ]
        modes += ["unmarked"] if transport == "html" else ["bad-id", "bad-slug", "float-count"]
        cases.extend([await capture(transport, mode) for mode in modes])
    Path(__file__).with_name("python_talemetry.json").write_text(json.dumps(cases, indent=2) + "\n")
    print(f"Original Talemetry inventories frozen: {len(cases)}")


asyncio.run(main())
