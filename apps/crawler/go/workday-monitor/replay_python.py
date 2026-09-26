"""Replay one completed Workday capture through the current Python monitor.

Run from apps/crawler with ``uv run python go/workday-monitor/replay_python.py``.
The MockTransport consumes only captured responses and rejects extra requests.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
from pathlib import Path

import httpx

from src.core.monitors.workday import discover_stream


def read_trace(path: Path) -> tuple[str, list[dict]]:
    records = [json.loads(line) for line in path.read_text().splitlines()]
    if len(records) < 3 or records[0].get("schema") != "jobseek.workday-replay/v1":
        raise ValueError("trace header is missing or unsupported")
    api_url = records[0].get("api_url")
    responses = records[1:-1]
    footer = records[-1]
    if (
        not isinstance(api_url, str)
        or not api_url
        or footer.get("complete") is not True
        or footer.get("requests") != len(responses)
        or footer.get("responses") != len(responses)
    ):
        raise ValueError("trace does not prove a complete cycle")
    for sequence, record in enumerate(responses):
        if (
            record.get("sequence") != sequence
            or record.get("method") != "POST"
            or record.get("url") != api_url
            or record.get("status") != 200
        ):
            raise ValueError(f"trace response {sequence} is unsupported or out of order")
    return api_url, responses


async def replay(path: Path, company: str, instance: str, site: str) -> dict:
    api_url, responses = read_trace(path)
    expected_url = f"https://{company}.{instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs"
    if api_url != expected_url:
        raise ValueError("trace API URL differs from configured Workday site")
    consumed = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal consumed
        if consumed >= len(responses):
            raise ValueError("Python requested more Workday pages than the trace contains")
        record = responses[consumed]
        consumed += 1
        if (
            request.method != "POST"
            or str(request.url) != api_url
            or request.content != base64.b64decode(record["request_body_b64"], validate=True)
        ):
            raise ValueError(f"Python request differs from capture at response {consumed - 1}")
        return httpx.Response(
            200,
            content=base64.b64decode(record["response_body_b64"], validate=True),
            headers={"content-type": record.get("content_type", "application/json")},
        )

    board = {
        "board_url": f"https://{company}.{instance}.myworkdayjobs.com/{site}",
        "metadata": {
            "company": company,
            "wd_instance": instance,
            "site": site,
            "all_sites": False,
        },
    }
    urls: set[str] = set()
    truncated = False
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        async for result in discover_stream(board, client):
            if isinstance(result, set):
                urls.update(result)
            else:
                urls.update(result.urls)
                truncated |= result.truncated
    if consumed != len(responses):
        raise ValueError(f"Python consumed {consumed} of {len(responses)} captured responses")
    canonical = "\n".join(sorted(urls)).encode()
    return {
        "urls": len(urls),
        "requests": consumed,
        "truncated": truncated,
        "url_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--site", required=True)
    args = parser.parse_args()
    result = asyncio.run(replay(args.trace, args.company, args.instance, args.site))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
