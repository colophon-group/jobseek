"""Freeze actual Beisen requests, modern/legacy inventory and failure authority."""

from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.monitors.beisen import discover

HERE = Path(__file__).parent
TESTS = HERE.parents[2] / "tests" / "test_beisen_monitor.py"
tree = ast.parse(TESTS.read_text())
helpers = ast.Module(
    body=[
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        or isinstance(node, ast.FunctionDef)
        and node.name in {"_bootstrap", "_modern_job", "_payload", "_legacy_page"}
    ],
    type_ignores=[],
)
ns = {"json": json}
exec(compile(helpers, str(TESTS), "exec"), ns)
ROOT, API, SOCIAL, INDEX = (ns[k] for k in ("ROOT_URL", "API_URL", "LEGACY_URL", "INLINE_URL"))
bootstrap, row, payload, legacy = (
    ns[k] for k in ("_bootstrap", "_modern_job", "_payload", "_legacy_page")
)
MODERN = {
    "tenant": ns["TENANT"],
    "variant": "modern",
    "portal_id": ns["PORTAL_ID"],
    "tenant_id": 123,
}
STANDARD = {
    "tenant": ns["TENANT"],
    "variant": "legacy",
    "listing_path": "/Social",
    "legacy_template": "standard",
}
INLINE = {**STANDARD, "listing_path": "/index", "legacy_template": "inline"}


def response(body, status=200, headers=None):
    return {
        "body": json.dumps(body) if isinstance(body, dict) else body,
        "status": status,
        "headers": headers or {},
    }


cases = []


def add(name, pages=None, metadata=None, board_url=ROOT):
    cases.append(
        (
            name,
            board_url,
            {"scraper_type": "skip", **(metadata or {})},
            pages or {ROOT: response(bootstrap()), API: response(payload(row()))},
        )
    )


add("modern")
add("configured-modern", metadata=MODERN)
add("portal-identity-changed", metadata={**MODERN, "tenant_id": 124})
add("configured-tenant-mismatch", metadata={**MODERN, "tenant": "other"})
add("legacy-to-modern", metadata=STANDARD)
add("disabled-portal", {ROOT: response(bootstrap(status=0))})
add("boolean-bootstrap-status", {ROOT: response(bootstrap(status=True))})
add("malformed-bootstrap", {ROOT: response("var BSGlobal = {bad};")})
add("root-gone", {ROOT: response("gone", 404)})
add("root-410", {ROOT: response("gone", 410)})
add("root-redirect", {ROOT: response("redirect", 302, {"Location": SOCIAL})})
add(
    "root-challenge",
    {
        ROOT: response(
            "<title>Just a moment...</title><div id='cf-chl-widget'>Checking your browser</div>"
        )
    },
)
add(
    "root-retry",
    {ROOT: [response("busy", 202), response(bootstrap())], API: response(payload(row()))},
)
add(
    "root-cookie",
    {
        ROOT: response(bootstrap(), headers={"Set-Cookie": "portal=one; Path=/; Secure"}),
        API: response(payload(row())),
    },
)
add("root-reserved", {ROOT: response("invalid", headers={"TDM-Reservation": "1"})})
add("root-meta", {ROOT: response('<meta name="tdm-reservation" content="1">')})
add(
    "api-reserved",
    {ROOT: response(bootstrap()), API: response("invalid", headers={"TDM-Reservation": "1"})},
)
add("api-gone-is-failure", {ROOT: response(bootstrap()), API: response("gone", 404)})
add(
    "api-retry",
    {ROOT: response(bootstrap()), API: [response("busy", 401), response(payload(row()))]},
)
add(
    "api-malformed-retry",
    {ROOT: response(bootstrap()), API: [response("bad"), response(payload(row()))]},
)
add("invalid-count", {ROOT: response(bootstrap()), API: response(payload(row(), count=True))})
add(
    "modern-duplicate",
    {ROOT: response(bootstrap()), API: response(payload(row(), row(title="Other")))},
)
add(
    "modern-invalid-row",
    {ROOT: response(bootstrap()), API: response(payload(row(), {"Id": "bad"}))},
)
add("modern-empty", {ROOT: response(bootstrap()), API: response(payload())})
add("modern-incomplete-empty", {ROOT: response(bootstrap()), API: response(payload(count=2))})
add(
    "modern-boolean-status",
    {ROOT: response(bootstrap()), API: response(payload({**row(), "Status": True}))},
)
add(
    "modern-fields",
    {
        ROOT: response(bootstrap()),
        API: response(
            payload(
                {
                    **row(title=" Research &amp; Engineer "),
                    "LocNames": ["Straße", "STRASSE", "Paris"],
                    "JobAdId": 90071992547409931234,
                    "Duty": "Build <systems>.\n\nKeep &amp; improve.",
                    "Kind": "Full-time",
                    "PostDate": "2026-08-01T12:00:00",
                }
            )
        ),
    },
)
add("total-overcap", {ROOT: response(bootstrap()), API: response(payload(row(), count=50001))})
first = [
    row(public_id=f"{n:08x}-1111-4111-8111-111111111111", title=f"Engineer {n}")
    for n in range(1000)
]
last = row(public_id="ffffffff-1111-4111-8111-111111111111", title="Last engineer")
add(
    "modern-pagination",
    {
        ROOT: response(bootstrap()),
        API: [response(payload(*first, count=1001)), response(payload(last, count=1001))],
    },
)
add(
    "modern-later-failure",
    {
        ROOT: response(bootstrap()),
        API: [response(payload(row(), count=1001)), response("gone", 404)],
    },
)
add("legacy-standard", {ROOT: response("legacy root"), SOCIAL: response(legacy(123))}, STANDARD)
add(
    "legacy-inline",
    {ROOT: response("legacy root"), INDEX: response(legacy(123, inline=True))},
    INLINE,
)
add(
    "legacy-inferred",
    {
        ROOT: response("<script>_splash('new_zhiye_com')</script><a href='/Social'>Jobs</a>"),
        SOCIAL: response(legacy(123)),
    },
)
add(
    "legacy-pagination",
    {
        ROOT: response("legacy root"),
        SOCIAL: response(legacy(123, page_max=2)),
        SOCIAL + "?PageIndex=2": response(legacy(456, page_max=2)),
    },
    STANDARD,
)
add(
    "legacy-later-gone",
    {
        ROOT: response("legacy root"),
        SOCIAL: response(legacy(123, page_max=2)),
        SOCIAL + "?PageIndex=2": response("gone", 404),
    },
    STANDARD,
)
add(
    "legacy-later-empty",
    {
        ROOT: response("legacy root"),
        SOCIAL: response(legacy(123, page_max=2)),
        SOCIAL + "?PageIndex=2": response(legacy(page_max=2)),
    },
    STANDARD,
)
add("legacy-first-gone", {ROOT: response("legacy root"), SOCIAL: response("gone", 404)}, STANDARD)
add("legacy-empty", {ROOT: response("legacy root"), SOCIAL: response(legacy())}, STANDARD)
add(
    "legacy-duplicate",
    {ROOT: response("legacy root"), SOCIAL: response(legacy(123, 123))},
    STANDARD,
)
add(
    "legacy-missing-marker",
    {ROOT: response("legacy root"), SOCIAL: response("not a listing")},
    STANDARD,
)
add(
    "legacy-advertised-total",
    {ROOT: response("legacy root"), SOCIAL: response(legacy(123) + "共 2 条记录")},
    STANDARD,
)
add(
    "legacy-meta-reserved",
    {
        ROOT: response("legacy root"),
        SOCIAL: response(legacy(123) + '<meta name="tdm-reservation" content="1">'),
    },
    STANDARD,
)


async def main():
    output = []
    for name, board_url, metadata, pages in cases:
        requests, counts = [], {}

        def handle(request, requests=requests, pages=pages, counts=counts):
            url = str(request.url)
            requests.append(
                {
                    "method": request.method,
                    "url": url,
                    "body": request.content.decode(),
                    "cookie": request.headers.get("cookie", ""),
                }
            )
            r = pages.get(url)
            assert r is not None, "undeclared request"
            index = counts.get(url, 0)
            counts[url] = index + 1
            if isinstance(r, list):
                r = r[min(index, len(r) - 1)]
            return httpx.Response(r["status"], text=r["body"], headers=r["headers"])

        expected = {
            "error": False,
            "gone": False,
            "reserved": False,
            "truncated": False,
            "hybrid": False,
            "jobs": [],
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            try:
                result = await discover({"board_url": board_url, "metadata": metadata}, client)
                expected["truncated"] = result.truncated
                expected["hybrid"] = result.hybrid
                expected["jobs"] = [asdict(j) for j in result.jobs_by_url.values()]
            except Exception as exc:
                expected["error"] = True
                expected["gone"] = type(exc).__name__ == "BoardGoneError"
                expected["reserved"] = type(exc).__name__ == "TDMReservedError"
        output.append(
            {
                "name": name,
                "board_url": board_url,
                "metadata": metadata,
                "pages": pages,
                "requests": requests,
                "expected": expected,
            }
        )
    (HERE / "python_beisen.json").write_text(
        json.dumps({"cases": output}, indent=2, ensure_ascii=False, default=str) + "\n"
    )


asyncio.run(main())
