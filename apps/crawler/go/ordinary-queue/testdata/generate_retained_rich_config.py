"""Freeze original request and enrichment decisions for six current variants."""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitors import ashby, lever, recruitee, smartrecruiters  # noqa: E402
from src.processing.scrape import _effective_board_enrich  # noqa: E402

slugs = {
    "nord-security-main",
    "volta-medical-careers",
    "floryn-careers",
    "improvado-careers-sr",
    "kaiser-permanente-smartrecruiters",
    "tech-mahindra-careers-smartrecruiters",
}
rows = [
    r
    for r in csv.DictReader((Path(__file__).resolve().parents[3] / "data/boards.csv").open())
    if r["board_slug"] in slugs
]
assert len(rows) == 6


async def main():
    cases = []
    for row in rows:
        provider = row["monitor_type"]
        metadata = json.loads(row["monitor_config"] or "{}")
        metadata["scraper_type"] = row["scraper_type"]
        if row["scraper_config"]:
            metadata["scraper_config"] = json.loads(row["scraper_config"])
        requests = []

        def serve(request, requests=requests, provider=provider):
            requests.append(dict(method=request.method, url=str(request.url)))
            payload = (
                dict(jobs=[])
                if provider == "ashby"
                else []
                if provider == "lever"
                else dict(offers=[])
                if provider == "recruitee"
                else dict(content=[], totalFound=0)
            )
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            result = await dict(
                ashby=ashby, lever=lever, recruitee=recruitee, smartrecruiters=smartrecruiters
            )[provider].discover(dict(board_url=row["board_url"], metadata=metadata), client)
        assert len(result) == 0
        cases.append(
            dict(
                slug=row["board_slug"],
                provider=provider,
                board_url=row["board_url"],
                metadata=metadata,
                requests=requests,
                enrich=_effective_board_enrich(metadata, provider),
            )
        )
    Path(__file__).with_name("python_retained_rich_config.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(cases)} current original request/enrichment decisions")


if __name__ == "__main__":
    asyncio.run(main())
