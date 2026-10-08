"""Actual Python Avature identities, listing pages and complete inventories."""

from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.monitors import avature as monitor
from src.shared import avature as core

root = Path(__file__).resolve().parents[3]
module = ast.parse((root / "tests/test_avature.py").read_text())
namespace = {}
fixture = next(x for x in module.body if isinstance(x, ast.FunctionDef) and x.name == "_listing")
exec(
    compile(ast.Module(body=[fixture], type_ignores=[]), "<existing-avature-fixture>", "exec"),
    namespace,
)
listing = namespace["_listing"]
BASE = "https://acme.avature.net/careers/SearchJobs"
JOBS = ["/careers/JobDetail/Engineer/101", "/careers/JobDetail/Analyst/102"]


def page_projection(page):
    return dict(
        board=asdict(page.board),
        portal_id=page.portal_id,
        total=page.total,
        total_exact=page.total_exact,
        range_start=page.range_start,
        range_end=page.range_end,
        jobs=page.jobs,
        next_urls=list(page.next_urls),
    )


async def main():
    cases = []
    urls = [
        BASE,
        "https://acme.avature.net/careers/JobDetail/Engineer/123",
        "https://acme.avature.net/careers/JobDetail?jobId=123",
        "https://acme.avature.net/careers/FolderDetail/Engineer/123",
        "https://acme.avature.net/en_US/jobs/PipelineDetail?pipelineId=123",
        BASE.replace("https:", "http:"),
        BASE.replace("acme.avature.net", "avature.net"),
        BASE + "?keyword=engineer",
        "https://acme.avature.net/careers/JobDetail?jobId=0",
        BASE.replace("acme.avature.net", "localhost"),
        BASE.replace("acme.avature.net", "127.0.0.1"),
    ]
    for i, url in enumerate(urls):
        board = core.avature_board_from_url(url, allow_custom_host=True)
        cases.append(
            dict(
                name=f"identity-{i}",
                kind="identity",
                source=url,
                output=asdict(board) if board else None,
                error=board is None,
            )
        )
    for i, config in enumerate(
        [
            {},
            {"listing_url": BASE, "portal_id": "4"},
            {"listing_url": BASE, "portal_id": 4},
            {"listing_url": BASE, "portal_id": 0},
            {"listing_url": "https://jobs.example.com/en_US/careers/SearchJobs", "portal_id": "3"},
        ]
    ):
        try:
            board, configured, portal = monitor._board_identity(
                {"board_url": BASE, "metadata": config}
            )
            output = {"board": asdict(board), "configured": configured, "portal_id": portal}
            error = False
        except ValueError:
            output = None
            error = True
        cases.append(
            dict(
                name=f"config-{i}",
                kind="options",
                source=BASE,
                config=config,
                output=output,
                error=error,
            )
        )
    bodies = [
        ("normal", listing(jobs=JOBS)),
        (
            "mixed-details",
            listing(
                jobs=[
                    JOBS[0] + "?tracking=x",
                    "/careers/JobDetail?jobId=102&utm=x",
                    "/careers/FolderDetail/Analyst/103",
                    "https://other.example/careers/JobDetail/Evil/104",
                ],
                total="3",
                displayed=3,
                next_url="/careers/SearchJobs/?jobRecordsPerPage=3&jobOffset=3",
                nested_next=True,
            ),
        ),
        ("dedupe-path", listing(jobs=["/careers/JobDetail?jobId=101", JOBS[0]], total="1")),
        ("lower-bound", listing(jobs=[JOBS[0]], total="999+")),
        (
            "double-escaped",
            listing(
                url=BASE + "/?jobRecordsPerPage=1&amp;amp;jobOffset=1",
                jobs=[JOBS[1]],
                start=2,
                total="2",
            ),
        ),
        ("zero", listing(total="0")),
        ("markerless", "<html><body>No jobs</body></html>"),
        ("foreign-page", listing(page="JobDetail", jobs=JOBS)),
        ("zero-portal", listing(portal_id="0", jobs=JOBS)),
        ("leading-zero-portal", listing(portal_id="004", jobs=JOBS)),
        ("missing-count", listing(start=None, jobs=JOBS)),
        ("pagination-legend", listing(legend_class="pagination__legend", jobs=JOBS)),
        ("thousands", listing(total="1,234", jobs=JOBS)),
    ]
    for name, body in bodies:
        page = core.parse_avature_page(body, BASE)
        cases.append(
            dict(
                name=name,
                kind="page",
                source=BASE,
                html=body,
                output=page_projection(page) if page else None,
                error=page is None,
            )
        )
    for i, url in enumerate(
        [
            "/careers/SearchJobs?jobOffset=6&jobRecordsPerPage=6",
            "https://evil.example/careers/SearchJobs?jobOffset=6&jobRecordsPerPage=6",
            "/careers/SearchJobs?jobOffset=6&jobRecordsPerPage=6&keyword=x",
            "/careers/SearchJobs?jobOffset=6&jobOffset=6&jobRecordsPerPage=6",
            "/careers/SearchJobs?jobOffset=0&jobRecordsPerPage=6",
            "/careers/SearchJobs?jobOffset=-1&jobRecordsPerPage=6",
        ]
    ):
        out = core.avature_pagination_url(url, core.AvatureBoard("acme.avature.net", "/careers"))
        cases.append(
            dict(
                name=f"pagination-{i}",
                kind="pagination",
                source=url,
                output=list(out) if out else None,
                error=out is None,
            )
        )
    first = listing(
        jobs=[JOBS[0]], total="2", next_url="/careers/SearchJobs?jobRecordsPerPage=1&jobOffset=1"
    )
    final = listing(jobs=[JOBS[1]], start=2, total="2")
    for name, last in [
        ("two-pages", final),
        ("total-drift", listing(jobs=[JOBS[1]], start=2, total="3")),
        ("late-missing-marker", "<html></html>"),
        ("late-foreign-portal", listing(jobs=[JOBS[1]], start=2, total="2", portal_id="5")),
        ("late-incomplete", listing(jobs=[], displayed=1, start=2, total="2")),
        ("late-duplicate", listing(jobs=[JOBS[0]], start=2, total="2")),
    ]:
        pages = [first, last]

        def handler(request, pages=pages):
            return httpx.Response(200, text=pages[1 if request.url.query else 0], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                out = await monitor.discover({"board_url": BASE}, client)
                output = dict(
                    urls=sorted(out.urls), truncated=out.truncated, metadata=out.metadata_updates
                )
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


Path(__file__).with_name("python_avature.json").write_text(
    json.dumps(asyncio.run(main()), indent=2) + "\n"
)
