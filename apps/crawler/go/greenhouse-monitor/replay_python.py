"""Compare one captured Elastic response through the Python and Go monitors.

Run from apps/crawler with ``uv run python go/greenhouse-monitor/replay_python.py
--body /path/to/captured-response``. Both parsers consume the same local bytes;
the only Python HTTP request is answered by MockTransport.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
from pathlib import Path

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors.greenhouse import discover
from src.runtime.greenhouse_go import inventory_digests

_BOARD_URL = "https://job-boards.greenhouse.io/elastic"
_API_URL = "https://boards-api.greenhouse.io/v1/boards/elastic/jobs?content=true"
_MAX_BODY_BYTES = 64 << 20
_FIELDS = ("url", "title", "description", "locations", "date_posted", "language", "metadata")


async def replay(path: Path) -> dict[str, object]:
    if path.stat().st_size > _MAX_BODY_BYTES:
        raise ValueError("captured Greenhouse response exceeded 64 MiB")
    body = path.read_bytes()
    if len(body) > _MAX_BODY_BYTES:
        raise ValueError("captured Greenhouse response exceeded 64 MiB")
    module = Path(__file__).resolve().parent
    go = subprocess.run(
        ["go", "run", "./cmd/replay"],
        input=body,
        capture_output=True,
        cwd=module,
        timeout=120,
        check=True,
    )
    inventory = json.loads(go.stdout)
    go_jobs = inventory.get("jobs")
    if not isinstance(go_jobs, list) or not isinstance(inventory.get("truncated"), bool):
        raise ValueError("Go replay returned an invalid inventory")

    requests = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests != 1 or request.method != "GET" or str(request.url) != _API_URL:
            raise ValueError("Python Greenhouse monitor requested an unexpected response")
        return httpx.Response(200, content=body, headers={"content-type": "application/json"})

    # The replay must not produce another capture even if the caller has a
    # pilot capture path in its shell environment.
    os.environ.pop("GREENHOUSE_ELASTIC_CAPTURE_PATH", None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        discovered = await discover(
            {"board_url": _BOARD_URL, "metadata": {"token": "elastic", "scraper_type": "skip"}},
            client,
        )
    if requests != 1:
        raise ValueError("Python Greenhouse monitor did not consume exactly one response")
    if isinstance(discovered, MonitorResult):
        raise ValueError("truncated Elastic capture needs a separate partial-inventory replay")
    python_jobs = [{field: getattr(job, field) for field in _FIELDS} for job in discovered]
    url_digest, fields_digest = inventory_digests(discovered)
    equal = go_jobs == python_jobs and inventory["truncated"] is False
    first_difference = next(
        (
            index
            for index, (go_job, python_job) in enumerate(zip(go_jobs, python_jobs, strict=False))
            if go_job != python_job
        ),
        None,
    )
    if first_difference is None and len(go_jobs) != len(python_jobs):
        first_difference = "job_count"
    return {
        "equal": equal,
        "go_jobs": len(go_jobs),
        "python_jobs": len(python_jobs),
        "requests": requests,
        "truncated": inventory["truncated"],
        "url_sha256": url_digest,
        "fields_sha256": fields_digest,
        "first_difference": first_difference,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--body", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(replay(args.body))
    print(json.dumps(result, sort_keys=True))
    if not result["equal"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
