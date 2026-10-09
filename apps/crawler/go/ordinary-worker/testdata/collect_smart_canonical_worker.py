"""Freeze the original monitor and CPU localization boundary for native workers."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from src.core.monitors import smartrecruiters
from src.processing.cpu import _build_locales, _build_titles
from src.shared.html_normalize import normalize_description_html

ROOT = Path(__file__).resolve().parents[3]
NAMES = {
    "canonical-bilingual-locations-fallback",
    "localized-job-id",
    "localized-template",
    "canonical-missing-uuid",
    "duplicate-language",
    "double-default",
    "coordinate-1.٢٣",
    "coordinate-NaN",
}


async def main():
    cases = []
    corpus = json.loads(
        (ROOT / "go/smartrecruiters-monitor/testdata/python_inventory.json").read_text()
    )
    for ref in corpus:
        if ref["name"] not in NAMES:
            continue
        requests = {}
        positions = {}

        async def transport(request, ref=ref, requests=requests, positions=positions):
            endpoint = str(request.url)
            assert request.method == "GET" and endpoint in ref["responses"]
            requests[endpoint] = requests.get(endpoint, 0) + 1
            offset = positions.get(endpoint, 0)
            positions[endpoint] = offset + 1
            values = ref["responses"][endpoint]
            return httpx.Response(200, json=copy.deepcopy(values[min(offset, len(values) - 1)]))

        metadata = {**ref["metadata"], "scraper_type": "skip"}
        expected = {"jobs": [], "truncated": False, "error": False}
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            with patch.object(smartrecruiters.asyncio, "sleep", new=AsyncMock()):
                try:
                    result = await smartrecruiters.discover(
                        {"board_url": ref["board_url"], "metadata": metadata}, client
                    )
                except Exception:
                    if not ref.get("error"):
                        raise
                    expected["error"] = True
                else:
                    jobs = (
                        list(result.jobs_by_url.values())
                        if hasattr(result, "jobs_by_url")
                        else result
                    )
                    expected["truncated"] = getattr(result, "truncated", False)
                    for job in jobs:
                        fields = dataclasses.asdict(job)
                        fields["description"] = normalize_description_html(job.description)
                        fields["titles"] = _build_titles(job.title, job.localizations)
                        fields["locales"] = _build_locales(job.language, job.localizations)
                        expected["jobs"].append(fields)
        cases.append(
            {
                "name": ref["name"],
                "board_url": ref["board_url"],
                "metadata": metadata,
                "responses": ref["responses"],
                "requests": requests,
                "expected": expected,
            }
        )
    assert len(cases) == len(NAMES)
    Path(__file__).with_name("python_smart_canonical_worker.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(cases)} original canonical monitor/CPU boundaries")


asyncio.run(main())
