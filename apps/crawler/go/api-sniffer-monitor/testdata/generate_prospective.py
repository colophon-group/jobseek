"""Freeze original Prospective filtered paging, redirects and locale identities."""

from __future__ import annotations

import ast
import asyncio
import dataclasses
import json
import sys
from pathlib import Path

import httpx

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
from src.core.monitors import prospective  # noqa: E402

# Reuse the repository's original synthetic pages without executing its tests.
tree = ast.parse((root / "tests/test_prospective_monitor.py").read_text())
selected = [
    n
    for n in tree.body
    if isinstance(n, ast.FunctionDef)
    and n.name in ("_page", "_detail")
    or isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id in ("BOARD_URL", "IDENTITY_CONFIG") for t in n.targets)
]
scope = {"json": json}
exec(
    compile(
        ast.Module(body=selected, type_ignores=[]), "original-prospective-fixture-functions", "exec"
    ),
    scope,
)
page, detail = scope["_page"], scope["_detail"]
board = scope["BOARD_URL"]
first = "11111111-1111-4111-8111-111111111111"
second = "22222222-2222-4222-8222-222222222222"
third = "33333333-3333-4333-8333-333333333333"
application = "99999999-9999-4999-8999-999999999999"


async def capture(mode: str) -> dict:
    identity = {**scope["IDENTITY_CONFIG"], "concurrency": 1}
    if mode in ("aliases", "alias-with-link"):
        identity["source_url_aliases"] = {first: first, second: first}
    if mode in ("redirect", "redirect-loop", "redirect-foreign"):
        identity["source_url_allowlist"] = (
            r"^https://apply\.example\.com/(?:jobs|start)/[0-9a-f-]{36}$"
        )
    metadata = {
        "medium_id": "1000613",
        "application_identity": identity,
        "filters": {"filter_10": ["owned"]},
    }
    record = {"name": mode, "board": board, "metadata": metadata, "requests": [], "responses": []}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        headers, status = {}, 200
        if request.url.host == "apply.example.com":
            source = "<html>Application</html>"
            if request.url.path.startswith("/start/"):
                status = 302
                headers["Location"] = f"https://apply.example.com/jobs/{application}"
                if mode == "redirect-loop":
                    headers["Location"] = url
                if mode == "redirect-foreign":
                    headers["Location"] = "https://foreign.example/application"
        elif request.url.path.startswith("/offene-stellen/job/"):
            job = request.url.path.rsplit("/", 1)[-1]
            locale = "de" if job == first else "en"
            if mode == "duplicate-locale":
                locale = "en"
            if mode == "unknown-locale":
                locale = "es"
            source = detail(
                job, locale=locale, application_id=application if mode != "complete" else job
            )
            if mode == "aliases":
                source = source.replace(
                    f'<a href="https://apply.example.com/jobs/{application}">Apply</a>', ""
                )
            if mode in ("redirect", "redirect-loop", "redirect-foreign"):
                source = source.replace("/jobs/", "/start/")
            if mode == "foreign-identity":
                source = source.replace("apply.example.com", "foreign.example")
        elif request.method == "GET":
            source = page(jobs=(first,))
        else:
            values = dict(httpx.QueryParams(request.content.decode()).multi_items())
            if mode in ("zero", "unproved-zero"):
                source = page(authoritative_empty=True)
                if mode == "unproved-zero":
                    source = source.replace('id="no-results"', 'id="unknown"')
            elif values["offset"] == "0":
                source = page(jobs=(first, second), next_offset=10 if mode == "complete" else None)
            else:
                source = page(offset=10, jobs=(third,))
        if mode == "missing-filter" and request.url.host == "jobs.example.com":
            source = source.replace('value="owned"', 'value="removed"')
        record["requests"].append(
            {
                "url": url,
                "method": request.method,
                "body": request.content.decode(),
                "headers": {
                    k: request.headers[k]
                    for k in ("Accept", "Content-Type", "Origin", "Referer")
                    if k in request.headers
                },
            }
        )
        record["responses"].append({"body": source, "status": status, "headers": headers})
        return httpx.Response(status, text=source, headers=headers, request=request)

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await prospective.discover({"board_url": board, "metadata": metadata}, client)
            record["jobs"] = [dataclasses.asdict(j) for j in jobs]
    except (ValueError, RuntimeError, httpx.HTTPError):
        record["error"] = True
    return record


async def main() -> None:
    cases = [
        await capture(mode)
        for mode in (
            "complete",
            "localized",
            "aliases",
            "alias-with-link",
            "redirect",
            "redirect-loop",
            "redirect-foreign",
            "duplicate-locale",
            "unknown-locale",
            "foreign-identity",
            "missing-filter",
            "zero",
            "unproved-zero",
        )
    ]
    Path(__file__).with_name("python_prospective.json").write_text(
        json.dumps(cases, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"Original Prospective inventories frozen: {len(cases)}")


asyncio.run(main())
