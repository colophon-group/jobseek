"""Regenerate original Python pagination references without network access."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict
from pathlib import Path

import httpx
import structlog

from src.core.monitors import api_sniffer

structlog.configure(logger_factory=structlog.ReturnLoggerFactory())
HERE = Path(__file__).parent
BASE = {
    "api_url": "https://example.com/api?company=example",
    "method": "POST",
    "json_path": "jobs",
    "total_path": "total",
    "url_template": "https://example.com/jobs/{id}",
    "fields": {"title": "title"},
    "pagination": {
        "style": "offset",
        "location": "body",
        "increment": 9,
        "param_name": "offset",
        "start_value": 0,
        "max_pages": 3,
    },
}


async def case(name):
    md = copy.deepcopy(BASE)
    if name.startswith("empty-string"):
        md["post_data"] = ""
    total = 20 if name.endswith("incomplete") else 1
    requests = []
    responses = []

    def handle(request):
        requests.append(
            {"method": request.method, "url": str(request.url), "body": request.content.decode()}
        )
        data = {"total": total, "jobs": [{"id": "one", "title": "Engineer"}]}
        responses.append(data)
        assert len(requests) <= 3
        return httpx.Response(200, json=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result = await api_sniffer.discover(
            {"board_url": "https://example.com/careers", "metadata": md}, client
        )
    if isinstance(result, (list, set)):
        jobs, truncated = result, False
    else:
        jobs = list((result.jobs_by_url or {}).values())
        truncated = result.truncated
    return {
        "name": name,
        "metadata": md,
        "requests": requests,
        "responses": responses,
        "jobs": [asdict(job) for job in jobs],
        "truncated": bool(truncated),
    }


async def main():
    rows = [
        await case(name)
        for name in (
            "absent-complete",
            "absent-incomplete",
            "empty-string-complete",
            "empty-string-incomplete",
        )
    ]
    (HERE / "python_absent_body_pagination.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False) + "\n"
    )


asyncio.run(main())
