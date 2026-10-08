"""Freeze direct-board and JSON-LD verification through original DOM monitor."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import httpx

from src.core.monitors.dom import _dom_discover_once
from src.processing.board import _prepare_discovered_sources
from src.shared import http_retry

BOARD = "https://example.com/careers"
JOB = '<script type="application/ld+json">{"@type":"JobPosting","title":"Engineer"}</script>'
LINK = '<a class="job" href="/jobs/1">Engineer</a>'


async def no_wait(_delay):
    return None


async def main():
    http_retry.asyncio.sleep = no_wait
    cases = []
    definitions = [
        ("include-empty-document", {"include_board_url": True}, "<html>Document</html>", {}),
        ("include-links", {"include_board_url": True}, LINK, {"/jobs/1": (200, JOB)}),
        ("include-disabled", {"include_board_url": False}, "<html>Document</html>", {}),
        (
            "verify-included-board",
            {"include_board_url": True, "require_jsonld_jobposting": True},
            JOB,
            {},
        ),
        ("verify-live", {"require_jsonld_jobposting": True}, LINK, {"/jobs/1": (200, JOB)}),
        (
            "verify-mixed",
            {"require_jsonld_jobposting": True},
            LINK + '<a class="job" href="/jobs/2">Gone</a><a class="job" href="/jobs/3">Other</a>',
            {
                "/jobs/1": (200, JOB),
                "/jobs/2": (404, "gone"),
                "/jobs/3": (200, "<html>Other</html>"),
            },
        ),
        (
            "verify-all-nonjob",
            {"require_jsonld_jobposting": True},
            LINK,
            {"/jobs/1": (200, "<html>Other</html>")},
        ),
        ("verify-gone404", {"require_jsonld_jobposting": True}, LINK, {"/jobs/1": (404, "gone")}),
        ("verify-gone410", {"require_jsonld_jobposting": True}, LINK, {"/jobs/1": (410, "gone")}),
        (
            "verify-graph",
            {"require_jsonld_jobposting": True},
            LINK,
            {
                "/jobs/1": (
                    200,
                    '<script type="application/ld+json">'
                    '{"@graph":[{"@type":["Thing","JobPosting"]}]}</script>',
                )
            },
        ),
        (
            "verify-malformed-jsonld",
            {"require_jsonld_jobposting": True},
            LINK,
            {"/jobs/1": (200, '<script type="application/ld+json">invalid</script>')},
        ),
        (
            "verify-transient503",
            {"require_jsonld_jobposting": True},
            LINK,
            {"/jobs/1": (503, "unavailable")},
        ),
        (
            "verify-transient403",
            {"require_jsonld_jobposting": True},
            LINK,
            {"/jobs/1": (403, "blocked")},
        ),
        ("verify-empty200", {"require_jsonld_jobposting": True}, LINK, {"/jobs/1": (200, "")}),
        ("verify-nonretry201", {"require_jsonld_jobposting": True}, LINK, {"/jobs/1": (201, JOB)}),
        (
            "verify-reserved-body",
            {"require_jsonld_jobposting": True},
            LINK,
            {"/jobs/1": (200, '<meta name="tdm-reservation" content="1">' + JOB)},
        ),
    ]
    for name, options, listing, details in definitions:
        metadata = {"link_selector": "a.job", "url_filter": "/jobs/", **options}
        calls = []

        def handler(request, *, calls=calls, details=details, listing=listing):
            calls.append(str(request.url))
            status, body = details.get(request.url.path, (200, listing))
            return httpx.Response(
                status, text=body, headers={"content-type": "text/html; charset=utf-8"}
            )

        row = {
            "name": name,
            "board_url": BOARD,
            "metadata": metadata,
            "listing": listing,
            "details": {
                path: {"status": status, "body": body} for path, (status, body) in details.items()
            },
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                result = await _dom_discover_once(
                    {"board_url": BOARD, "metadata": metadata}, client
                )
                row["expected"] = sorted(result)
                processed, drops, _ = _prepare_discovered_sources(
                    SimpleNamespace(urls=sorted(result), jobs_by_url=None), BOARD, set()
                )
                row["processing_expected"] = sorted(url for url, _, _ in processed)
                row["drop_reasons"] = drops
            except Exception as exc:
                row.update(error=True, error_class=type(exc).__name__)
        row["calls"] = calls
        cases.append(row)
    Path(__file__).with_name("python_listing_verification.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(cases)} actual Python DOM verification HTTP cases")


if __name__ == "__main__":
    asyncio.run(main())
