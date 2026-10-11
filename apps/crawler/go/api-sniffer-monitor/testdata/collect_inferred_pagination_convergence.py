"""Regenerate mock-only original Python references for inferred API totals."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import structlog

r = Path(__file__).resolve().parent
sys.path.insert(0, str(r.parents[2]))
from src.core.monitors import api_sniffer  # noqa: E402

structlog.configure(logger_factory=structlog.ReturnLoggerFactory())
base = {
    "api_url": "https://api.example.test/jobs?page=1&limit=100",
    "json_path": "jobs",
    "total_path": "total",
    "url_field": "url",
    "fields": {"title": "title", "metadata.id": "id"},
    "pagination": {
        "style": "page",
        "param_name": "page",
        "start_value": 1,
        "increment": 1,
        "max_pages": 3,
    },
    "pagination_convergence": {
        "max_passes": 4,
        "required_no_growth_passes": 2,
        "identity_by": ["id"],
        "stable_fields": ["title", "url"],
    },
    "scraper_type": "skip",
}
names = [
    "complete",
    "empty",
    "duplicate_identity",
    "numeric_identity",
    "missing_identity",
    "total_drift",
    "missing_later_total",
    "later_invalid_shape",
    "mixed_later_rows",
    "projection_drift",
    "exhausted_growth",
    "legacy_duplicates",
    "url_match",
    "url_match_conflict",
]


def item(n):
    return {
        "id": str(n),
        "title": "Role " + str(n),
        "url": "https://careers.example.test/jobs/" + str(n),
    }


async def case(name):
    md = copy.deepcopy(base)
    md.pop("total_path")
    if name == "exhausted_growth":
        md["pagination_convergence"]["max_passes"] = 3
    if name == "legacy_duplicates":
        md["pagination_convergence"].pop("identity_by")
        md["pagination_convergence"].pop("stable_fields")
        md["item_filter"] = {"dedupe_by": ["id"]}
    if name.startswith("url_match"):
        md["url_field_match"] = {
            "pattern": r"^https://careers\.example\.test/jobs/(?P<id>[0-9]+)$",
            "fields": {"id": "id"},
        }
    pass_number = 0
    requests = []
    responses = []

    def handler(request):
        nonlocal pass_number
        page = int(request.url.params["page"])
        if page == 1:
            pass_number += 1
        rows = [item(n) for n in ([1, 2] if page == 1 else [3, 4] if page == 2 else [])]
        total = 4
        if name == "empty":
            rows = []
            total = 0
        if name == "duplicate_identity" and page == 2:
            rows = [item(1), item(2)]
        if name == "numeric_identity" and page == 2:
            rows[0]["id"] = 3
        if name == "missing_identity" and page == 2:
            rows[0].pop("id")
        if name == "projection_drift" and pass_number >= 2 and page == 1:
            rows[0]["title"] = "Changed"
        if name in {"exhausted_growth", "legacy_duplicates"} and pass_number == 1 and page == 2:
            rows = [item(1), item(3)]
        if name == "total_drift" and page == 2:
            total = 5
        if name == "url_match_conflict" and page == 2:
            rows[0]["url"] = "https://careers.example.test/jobs/999"
        data = {"jobs": rows, "total": total}
        if name == "missing_later_total" and page == 2:
            data.pop("total")
        if name == "later_invalid_shape" and page >= 2:
            data["jobs"] = {}
        if name == "mixed_later_rows" and page == 2:
            data["jobs"] = [item(3), "invalid"]
        responses.append(data)
        requests.append(
            {"method": request.method, "url": str(request.url), "body": request.content.decode()}
        )
        if len(requests) > 60:
            raise RuntimeError("synthetic request bound")
        return httpx.Response(200, json=data)

    out = {
        "name": name,
        "board_url": "https://careers.example.test/",
        "metadata": md,
        "responses": responses,
        "requests": requests,
    }
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            value = await asyncio.wait_for(
                api_sniffer.discover({"board_url": out["board_url"], "metadata": md}, client), 30
            )
        if isinstance(value, (list, set)):
            jobs = value
            truncated = False
        else:
            jobs = (
                list((value.jobs_by_url or {}).values())
                if value.jobs_by_url is not None
                else list(value.urls or set())
            )
            truncated = value.truncated
        out.update(
            jobs=sorted(
                (
                    {k: v for k, v in asdict(j).items() if v is not None}
                    if not isinstance(j, str)
                    else {"url": j}
                    for j in jobs
                ),
                key=lambda x: x["url"],
            ),
            truncated=bool(truncated),
            error=False,
        )
    except Exception as e:
        out.update(jobs=[], truncated=False, error=True, error_class=type(e).__name__)
    print(
        name,
        "jobs",
        len(out["jobs"]),
        "truncated",
        out["truncated"],
        "error",
        out["error"],
        "requests",
        len(requests),
        flush=True,
    )
    return out


async def main():
    rows = [await case(n) for n in names]
    p = r / "python_inferred_pagination_convergence.json"
    p.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")


asyncio.run(main())
