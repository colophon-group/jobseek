"""Actual Python SEEK identity, page and complete-inventory reference cases."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.core.monitors import seek

ADVERTISER = "9094357"
BASE = "https://au.seek.com/jobs?advertiserid=" + ADVERTISER


def row(identifier, owner=ADVERTISER):
    return {"id": identifier, "advertiser": {"id": owner}}


def payload(rows, total=None, page=1):
    count = len(rows) if total is None else total
    return {
        "data": rows,
        "totalCount": count,
        "solMetadata": {
            "advertiser": ADVERTISER,
            "pageNumber": page,
            "pageSize": 100,
            "totalJobCount": count,
        },
    }


async def main():
    cases = []
    urls = [
        BASE,
        "https://www.seek.com.au/jobs/?advertiserid=" + ADVERTISER,
        "https://nz.seek.com/jobs?advertiserid=22265755",
        "https://www.seek.co.nz/jobs?advertiserid=22265755",
        BASE.replace("https:", "http:"),
        BASE.replace("au.seek.com", "user@au.seek.com"),
        BASE.replace("au.seek.com", "au.seek.com:444"),
        BASE.replace("au.seek.com", "au.seek.com.evil.test"),
        "https://au.seek.com/jobs",
        BASE + "&page=2",
        BASE + "&advertiserid=" + ADVERTISER,
        BASE.replace(ADVERTISER, "not-numeric"),
        BASE + "#fragment",
        BASE.replace("au.seek.com", "au.seek.com:443"),
    ]
    for i, url in enumerate(urls):
        try:
            output = list(seek._board_identity({"board_url": url}))
            error = False
        except ValueError:
            output = None
            error = True
        cases.append(
            dict(
                name=f"identity-{i}",
                kind="identity",
                source=url,
                config={},
                output=output,
                error=error,
            )
        )
    for i, config in enumerate(
        [
            {"host": "www.seek.com.au", "advertiser_id": ADVERTISER},
            {"host": "nz.seek.com", "advertiser_id": ADVERTISER},
            {"host": "au.seek.com"},
            {"advertiser_id": ADVERTISER},
            {"host": "au.seek.com", "advertiser_id": 99},
            {"host": "au.seek.com", "advertiser_id": "999"},
        ]
    ):
        try:
            output = list(seek._board_identity({"board_url": BASE, "metadata": config}))
            error = False
        except ValueError:
            output = None
            error = True
        cases.append(
            dict(
                name=f"configured-{i}",
                kind="identity",
                source=BASE,
                config=config,
                output=output,
                error=error,
            )
        )
    pages = [
        ("complete", payload([row("94267983"), row("94267984")]), 1),
        ("zero", payload([]), 1),
        ("final", payload([row("94267983")], 101, 2), 2),
    ]
    for name, field, value in [
        ("missing-data", "data", None),
        ("object-data", "data", {}),
        ("nonobject-row", "data", [1, 2]),
        ("duplicate", "data", [row("1"), row("1")]),
        ("foreign-row", "data", [row("1", "foreign"), row("2")]),
        ("invalid-id", "data", [row("a"), row("2")]),
        ("bool-total", "totalCount", True),
        ("float-total", "totalCount", 2.0),
        ("negative-total", "totalCount", -1),
        ("over-cap", "totalCount", 50001),
        ("short", "totalCount", 3),
        ("no-owner-proof", "solMetadata", None),
    ]:
        p = payload([row("1"), row("2")])
        p[field] = value
        pages.append((name, p, 1))
    for key, value in [
        ("advertiser", None),
        ("advertiser", 999),
        ("advertiser", "foreign"),
        ("pageNumber", 2),
        ("pageNumber", True),
        ("pageNumber", 1.0),
        ("pageSize", 99),
        ("pageSize", True),
        ("totalJobCount", 3),
        ("totalJobCount", 2.0),
    ]:
        p = payload([row("1"), row("2")])
        p["solMetadata"][key] = value
        pages.append((f"metadata-{key}-{value}", p, 1))
    for name, p, page in pages:
        try:
            ids, total = seek._parse_page(p, advertiser_id=ADVERTISER, requested_page=page)
            output = {"ids": ids, "total": total}
            error = False
        except ValueError:
            output = None
            error = True
        cases.append(dict(name=name, kind="page", payload=p, page=page, output=output, error=error))
    first = payload([row(str(1000 + i)) for i in range(100)], 101)
    last = payload([row("2000")], 101, 2)
    for name, last_page in [
        ("complete-pages", last),
        ("changed-total", payload([row("2000"), row("2001")], 102, 2)),
        ("cross-page-duplicate", payload([row("1000")], 101, 2)),
        ("late-invalid", payload([row("broken")], 101, 2)),
    ]:
        pages = [first, last_page]

        def handler(request, pages=pages):
            return httpx.Response(
                200, json=pages[int(request.url.params["page"]) - 1], request=request
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                output = sorted(await seek.discover({"board_url": BASE}, client))
                error = False
            except ValueError:
                output = None
                error = True
        cases.append(
            dict(
                name=name,
                kind="inventory",
                source=BASE,
                config={},
                pages=pages,
                output=output,
                error=error,
            )
        )
    return cases


Path(__file__).with_name("python_seek.json").write_text(
    json.dumps(asyncio.run(main()), indent=2) + "\n"
)
