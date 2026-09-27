"""Freeze endpoints from an operator-provided read-only registry export."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

from src.core.monitors import pinpoint, recruitee


async def main():
    rows = json.loads(Path(sys.argv[1]).read_text())
    for provider, module in [("recruitee", recruitee), ("pinpoint", pinpoint)]:
        out = []
        for r in rows:
            if r["crawler_type"] != provider:
                continue
            config = {
                k: v
                for k, v in (r["metadata"] or {}).items()
                if k in ("slug", "api_base", "scraper_type")
            }
            urls = []

            def respond(request, urls=urls):
                urls.append(str(request.url))
                return httpx.Response(200, json={"offers": [], "data": []}, request=request)

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                await module.discover({"board_url": r["board_url"], "metadata": config}, client)
            assert len(urls) == 1
            out.append({"board_url": r["board_url"], "config": config, "endpoint": urls[0]})
        (
            Path(__file__).parents[2] / f"{provider}-monitor/testdata/python_endpoints.json"
        ).write_text(json.dumps(out, indent=2) + "\n")
        print(provider, len(out))


asyncio.run(main())
