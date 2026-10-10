"""Freeze original RMK fields, requests and all-or-nothing inventory outcomes."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.core.monitors.rss import _discover_sf_rmk  # noqa: E402

BOARD = "https://example.com/Fixture/jobs"
META = {"preset": "successfactors", "variant": "rmk", "brand": "Fixture"}
BOOTSTRAP = (
    "<script>var app={brand:'Fixture',locale:'en_US',"
    "headers:{'X-CSRF-Token':'fixture-csrf'}};</script>"
)


def row(n):
    return {
        "response": {
            "brandUrl": "Fixture",
            "id": str(n),
            "unifiedStandardTitle": f" Engineer {n} ",
            "unifiedUrlTitle": "R&amp;D Zürich%20/-._~",
            "filter1": [" Zurich ", "", 4],
            "filter4": [" Full-time ", "Part-time"],
            "filter2": [" Research "],
            "businessUnit_obj": [" Systems "],
            "division_obj": [" Europe "],
            "currency": [" CHF "],
            "unifiedStandardStart": " 2026-10-10 ",
        }
    }


def payload(total, rows):
    return json.dumps({"totalJobs": total, "jobSearchResult": rows}, ensure_ascii=False)


async def main():
    eleven = [row(i) for i in range(1, 12)]
    conflict = copy.deepcopy(eleven[0])
    conflict["response"]["unifiedStandardTitle"] = "Different"
    cases = [
        ("zero", [BOOTSTRAP, payload(0, [])]),
        ("rich-single", [BOOTSTRAP, payload(1, [row(1)])]),
        ("eleven", [BOOTSTRAP, payload(11, eleven[:10]), payload(11, eleven[10:])]),
        ("repeated", [BOOTSTRAP, payload(11, eleven[:10]), payload(11, [eleven[0]])]),
        ("conflicting-repeat", [BOOTSTRAP, payload(11, eleven[:10]), payload(11, [conflict])]),
        ("changed-total", [BOOTSTRAP, payload(11, eleven[:10]), payload(12, eleven[10:])]),
        ("short-tail", [BOOTSTRAP, payload(11, eleven[:10]), payload(11, [])]),
        ("invalid-tail", [BOOTSTRAP, payload(11, eleven[:10]), "not-json"]),
        ("boolean-total", [BOOTSTRAP, payload(True, [])]),
        ("missing-bootstrap", ["<html></html>"]),
        ("wrong-bootstrap-brand", [BOOTSTRAP.replace("brand:'Fixture'", "brand:'Other'")]),
        (
            "wrong-row-brand",
            [BOOTSTRAP, payload(1, [{"response": dict(row(1)["response"], brandUrl="Other")}])],
        ),
    ]
    for name, value in [
        ("fallback-empty-list", []),
        ("fallback-zero", 0),
        ("fallback-false", False),
        ("fallback-empty", ""),
    ]:
        r = row(1)
        r["response"].update(unifiedUrlTitle=value, urlTitle="Fallback Title")
        cases.append((name, [BOOTSTRAP, payload(1, [r])]))
    output = []
    for name, responses in cases:
        requests = []

        async def handle(request, responses=responses, requests=requests):
            at = len(requests)
            requests.append(
                {
                    "method": request.method,
                    "url": str(request.url),
                    "body": request.content.decode(),
                    "headers": {
                        k: v
                        for k, v in request.headers.items()
                        if k in {"referer", "content-type", "x-csrf-token"}
                    },
                }
            )
            return httpx.Response(200, text=responses[at], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            try:
                jobs, truncated = await _discover_sf_rmk(
                    {"board_url": BOARD, "metadata": META}, client
                )
                error = False
            except ValueError:
                jobs, truncated, error = [], False, True
        output.append(
            {
                "name": name,
                "board_url": BOARD,
                "metadata": META,
                "responses": responses,
                "requests": requests,
                "jobs": [asdict(j) for j in jobs],
                "truncated": truncated,
                "error": error,
            }
        )
    Path(__file__).with_name("python_successfactors_rmk.json").write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"Frozen {len(output)} original RMK contracts with synthetic data")


asyncio.run(main())
