"""Freeze actual Python HTTP inventories for grouped generic migration."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import io
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from src.core.monitor import MonitorResult, _apply_url_transform
from src.core.monitors.dom import _dom_discover_once
from src.core.monitors.inline import discover as inline_discover
from src.core.monitors.rss import discover_stream
from src.shared.tdm import TDMReservedError

BOARD = "https://example.com/careers?z=last&a=first&a=again"
ONE = '<a href="/jobs/one?tracking=1">One</a>'
TWO = '<a href="/jobs/two">Two</a>'
RSS = (
    "<rss><channel><item><link>https://example.com/jobs/one</link>"
    "<title> Café Engineer 東京 </title>"
    "<description><![CDATA[<p>Build &amp; learn</p>]]></description>"
    "<Location> Zürich </Location><JobID> 123 </JobID>"
    "<pubDate>2026-10-06</pubDate></item></channel></rss>"
)


def response(body="", status=200, headers=None):
    return {"body": body, "status": status, "headers": headers or {}}


CASES = []
for mode in [
    "empty",
    "gone404",
    "gone410",
    "duplicate",
    "retry429",
    "failed500",
    "failed403",
    "retry403",
    "reserved",
    "challenge",
]:
    md = {
        "url_filter": "/jobs/",
        "pagination": {"param_name": "page", "max_pages": 4, "transport_attempts": 3},
    }
    pages = [response(ONE), response(TWO), response("")]
    if mode.startswith("gone"):
        pages[-1] = response(status=int(mode[4:]))
    elif mode == "duplicate":
        pages[-1] = response(TWO)
    elif mode == "retry429":
        pages = [response(ONE), response(status=429), response(TWO), response("")]
    elif mode == "failed500":
        pages = [response(ONE), response(status=500)]
    elif mode == "failed403":
        pages = [response(ONE), response(status=403)]
    elif mode == "retry403":
        md["pagination"]["transient_403"] = True
        pages = [response(ONE), response(status=403), response(TWO), response("")]
    elif mode == "reserved":
        pages = [
            response(ONE),
            response(
                TWO, headers={"TDM-Reservation": "1", "TDM-Policy": "https://example.com/policy"}
            ),
        ]
    elif mode == "challenge":
        pages = [
            response(ONE),
            response(
                "<html><title>Just a moment...</title>"
                "<div id='cf-chl-widget'>Verify you are human</div></html>"
            ),
        ]
    CASES.append(("dom-" + mode, "dom", BOARD, md, pages))
CASES.append(
    (
        "dom-transform-identity",
        "dom",
        BOARD,
        {
            "url_filter": "/jobs/",
            "url_transform": {"find": r"\?tracking=\d+$", "replace": ""},
            "pagination": {"param_name": "page", "max_pages": 4},
        },
        [response(ONE), response(ONE.replace("tracking=1", "tracking=2") + TWO), response(TWO)],
    )
)
CASES.append(
    (
        "dom-template",
        "dom",
        BOARD,
        {
            "url_filter": "/jobs/",
            "link_selector": "main a",
            "pagination": {
                "url_template": "https://example.com/list/{page}",
                "start": 0,
                "increment": 2,
                "max_pages": 3,
            },
        },
        [response("<main>" + ONE + "</main>"), response("<main>" + TWO + "</main>"), response("")],
    )
)
for mode in [
    "complete",
    "empty",
    "namespaced",
    "nested",
    "malformed",
    "trailing",
    "retry429",
    "failed404",
    "failed500",
    "reserved",
]:
    pages = [response(RSS)]
    if mode == "empty":
        pages = [response("<rss><channel/></rss>")]
    elif mode == "namespaced":
        pages = [
            response(
                RSS.replace("<item>", '<x:item xmlns:x="urn:item">').replace("</item>", "</x:item>")
            )
        ]
    elif mode == "nested":
        pages = [response(RSS.replace(" Café Engineer 東京 ", " Head <b>ignored</b> tail "))]
    elif mode == "malformed":
        pages = [response(RSS[:-6])]
    elif mode == "trailing":
        pages = [response(RSS + "<rss/>")]
    elif mode == "retry429":
        pages = [response(status=429), response(RSS)]
    elif mode == "failed404":
        pages = [response(status=404)]
    elif mode == "failed500":
        pages = [response(status=500)]
    elif mode == "reserved":
        pages = [
            response(
                RSS, headers={"TDM-Reservation": "1", "TDM-Policy": "https://example.com/policy"}
            )
        ]
    CASES.append(
        (
            "rss-" + mode,
            "rss",
            BOARD,
            {"preset": "generic", "feed_url": "https://example.com/feed?department=eng"},
            pages,
        )
    )
for mode in ["cache", "fallback", "reserved"]:
    md = {
        "steps": [{"tag": "h2", "field": "title"}, {"tag": "p", "field": "description"}],
        "fetch_contains": "Engineer",
        "fetch_urls": [
            {
                "url": "https://example.com/alternate",
                "headers": {"X-No-Cache": "true", "X-Return-Format": "html"},
            }
        ],
    }
    pages = [response("<h2>Engineer</h2><p>Build products</p>")]
    if mode == "fallback":
        md["fetch_urls"].insert(0, "https://example.com/unavailable")
        pages.insert(0, response(status=404))
    elif mode == "reserved":
        pages[0]["headers"] = {"TDM-Reservation": "1", "TDM-Policy": "https://example.com/policy"}
    CASES.append(("inline-" + mode, "inline", BOARD, md, pages))


async def collect(case):
    name, provider, board_url, metadata, pages = case
    requests = []

    def handle(request):
        requests.append(
            {
                "url": str(request.url),
                "method": request.method,
                "cache": request.headers.get("X-No-Cache", ""),
                "format": request.headers.get("X-Return-Format", ""),
            }
        )
        r = pages[min(len(requests) - 1, len(pages) - 1)]
        return httpx.Response(r["status"], text=r["body"], headers=r["headers"])

    board = {"board_url": board_url, "metadata": metadata}
    jobs = []
    error = reserved = False
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            if provider == "dom":
                urls = await _dom_discover_once(board, client)
                normalized = _apply_url_transform(MonitorResult(urls=urls), metadata)
                jobs = [{"url": u} for u in sorted(normalized.urls)]
            elif provider == "rss":
                async for batch in discover_stream(board, client):
                    found = (
                        batch.jobs_by_url.values() if isinstance(batch, MonitorResult) else batch
                    )
                    jobs.extend(dataclasses.asdict(job) for job in found)
            else:
                found = await inline_discover(board, client)
                found = found.jobs_by_url.values() if isinstance(found, MonitorResult) else found
                jobs = [dataclasses.asdict(job) for job in found]
    except Exception as exc:
        error = True
        reserved = isinstance(exc, TDMReservedError)
        jobs = []
    return {
        "name": name,
        "provider": provider,
        "board_url": board_url,
        "metadata": metadata,
        "pages": pages,
        "requests": requests,
        "error": error,
        "reserved": reserved,
        "jobs": jobs,
    }


async def main():
    with contextlib.redirect_stdout(io.StringIO()), patch("asyncio.sleep", new=AsyncMock()):
        cases = [await collect(case) for case in CASES]
    Path(__file__).with_name("python_generic_variant_http.json").write_text(
        json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(cases)} actual Python generic variant HTTP cases")


if __name__ == "__main__":
    asyncio.run(main())
