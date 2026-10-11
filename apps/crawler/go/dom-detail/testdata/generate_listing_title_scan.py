"""Freeze numbered detail URL resolution through the original HTTP monitor."""

from __future__ import annotations

import asyncio
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import httpx

from src.core.monitors.dom import _dom_discover_once
from src.shared import http_retry


async def no_wait(_delay):
    return None


async def main():
    http_retry.asyncio.sleep = no_wait
    base = "https://example.com/careers"
    config = {
        "url_filter": r"^https://example\.com/vacante-\d+$",
        "title_matched_url_scan": {
            "listing_title_selector": ".title",
            "detail_title_selector": "h4",
            "url_template": "https://example.com/vacante-{index}",
            "max_scan": 3,
        },
    }
    listing = (
        '<div class="title"> Research <span>Engineer</span> </div><div class="title">Analyst</div>'
    )
    details = {
        "/vacante-1": (200, "<h4>Research Engineer</h4>"),
        "/vacante-2": (200, "<h4>Analyst</h4>"),
        "/vacante-3": (404, "gone"),
    }
    out = []
    for name in [
        "complete",
        "missing-title",
        "duplicate-listing",
        "empty-listing-title",
        "missing-match",
        "duplicate-match",
        "multiple-detail-titles",
        "non-job-title",
        "cross-origin",
        "filter-mismatch",
        "redirect",
        "late-policy",
        "over-cap",
        "unicode-whitespace",
    ]:
        md, source, docs = deepcopy(config), listing, deepcopy(details)
        if name == "missing-title":
            source = "<p>Layout changed</p>"
        if name == "duplicate-listing":
            source = '<div class="title">Analyst</div>' * 2
        if name == "empty-listing-title":
            source += '<div class="title"> </div>'
        if name == "missing-match":
            docs["/vacante-2"] = (410, "gone")
        if name == "duplicate-match":
            docs["/vacante-3"] = (200, "<h4>Analyst</h4>")
        if name == "multiple-detail-titles":
            docs["/vacante-3"] = (200, "<h4>Other</h4><h4>Other again</h4>")
        if name == "non-job-title":
            docs["/vacante-3"] = (200, "<h4>Unlisted role</h4>")
        if name == "cross-origin":
            md["title_matched_url_scan"]["url_template"] = "https://other.example/vacante-{index}"
        if name == "filter-mismatch":
            md["url_filter"] = "/other/"
        if name == "redirect":
            docs["/vacante-3"] = (302, "redirect")
        if name == "late-policy":
            docs["/vacante-3"] = (200, '<meta name="tdm-reservation" content="1">')
        if name == "over-cap":
            md["title_matched_url_scan"]["max_scan"] = 1
        if name == "unicode-whitespace":
            source = (
                '<div class="title">Research\u00a0Engineer</div><div class="title">Analyst</div>'
            )
        calls = []

        def reply(request, _calls=calls, _source=source, _docs=docs):
            _calls.append(str(request.url))
            status, body = (
                (200, _source)
                if request.url.path == "/careers"
                else _docs.get(request.url.path, (404, "gone"))
            )
            return httpx.Response(
                status, text=body, headers={"content-type": "text/html; charset=utf-8"}
            )

        row = {
            "name": name,
            "board_url": base,
            "metadata": md,
            "listing": source,
            "details": {k: {"status": s, "body": b} for k, (s, b) in docs.items()},
            "expected": [],
            "error": False,
        }
        try:
            async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
                result = await _dom_discover_once({"board_url": base, "metadata": md}, client)
            row["expected"] = sorted(result)
        except Exception:
            row["error"] = True
        row["calls"] = sorted(calls)
        out.append(row)
    Path(__file__).with_name("python_listing_title_scan.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
