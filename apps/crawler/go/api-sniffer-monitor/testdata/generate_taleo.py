"""Freeze actual Taleo identity, redirects, page parsing and full discovery."""

from __future__ import annotations

import asyncio
import html
import json
from pathlib import Path
from unittest.mock import patch

from src.core.monitors import taleo as monitor
from src.shared import taleo as shared

board = shared.TaleoBoard("phe.tbe.taleo.net", "phe01", "ACME", 1)
final = shared.TaleoBoard("phh.tbe.taleo.net", "phh01", "ACME", 41)


def identity(b):
    return {"host": b.host, "partition": b.partition, "org": b.org, "cws": b.cws}


def listing(ids, total=None, next_offset=None):
    body = '<section class="oracletaleocwsv2-search-results">'
    if total is not None:
        body += f'<span class="oracletaleocwsv2-panel-number">{total}</span>'
    body += "".join(f'<a href="{html.escape(board.job_url(n))}">Job</a>' for n in ids)
    if next_offset is not None:
        body += (
            f'<a class="jscroll-next" href="/{board.partition}/ats/careers/v2/'
            f"searchResults?next&amp;rowFrom={next_offset}&amp;act=null&amp;"
            'sortColumn=null&amp;sortOrder=null&amp;currentTime=1785797000000">Next</a>'
        )
    return body + "</section>"


urls = [
    board.listing_url(),
    board.job_url(123),
    board.listing_url(row_from=20),
    board.listing_url() + "&rowFrom=２０",
    board.listing_url() + "&rowFrom=-0",
    board.listing_url().replace("https://", "http://"),
    board.listing_url().replace("https://", "https://user@"),
    board.listing_url().replace("taleo.net", "taleo.net:444"),
    board.listing_url().replace("taleo.net", "taleo.net.evil.test"),
    board.listing_url().replace("/phe01/", "/phg01/"),
    board.listing_url() + "&org=OTHER",
    board.listing_url() + "&department=x",
    board.listing_url() + "&rowFrom=1",
    board.listing_url().replace("cws=1", "cws=0"),
    board.job_url(123) + "&rid=456",
    board.listing_url().replace("searchResults", "searchResults/extra"),
]
identities = []
for url in urls:
    try:
        parts = shared._url_parts(url)
        identities.append(
            {
                "url": url,
                "valid": parts is not None,
                "board": identity(parts[0]) if parts else None,
                "detail": parts[1] == "detail" if parts else False,
                "rid": parts[2] if parts else None,
                "offset": parts[3] if parts else None,
            }
        )
    except Exception:
        identities.append({"url": url, "valid": False})

dispatcher = (
    "https://phe.tbe.taleo.net/dispatcher/servlet/DispatcherServlet?"
    "org=ACME&act=redirectCws&redirectUrl="
    "https%3A%2F%2Fphe.tbe.taleo.net%2Fphe01%2Fats%2Fcareers%2Fv2%2F"
    "searchResults%3Forg%3DACME%26cws%3D1"
)
redirects = []
for target in [
    final.listing_url(),
    dispatcher,
    "https://evil.test/jobs",
    final.listing_url().replace("org=ACME", "org=OTHER"),
    final.listing_url(row_from=10),
    dispatcher + "%26rowFrom%3D10",
    dispatcher.replace("org=ACME", "org=OTHER", 1),
    "",
]:
    result = shared.taleo_safe_redirect(board, board.listing_url(), target)
    redirects.append(
        {
            "resource": board.listing_url(),
            "target": target,
            "valid": result is not None,
            "url": result[0] if result else "",
            "board": identity(result[1]) if result else None,
        }
    )

inactive = []
for resource, target in [
    (dispatcher, "INACTIVEcareers/v2/searchResults?org=ACME&cws=1"),
    (board.listing_url(), "INACTIVEcareers/v2/searchResults?org=ACME&cws=1"),
    (dispatcher, "INACTIVEcareers/v2/searchResults?org=OTHER&cws=1"),
    (dispatcher, "INACTIVEcareers/v2/searchResults?org=ACME&cws=2"),
    (dispatcher, "INACTIVEcareers/v2/searchResults?org=ACME&cws=1&x=1"),
]:
    inactive.append(
        {
            "resource": resource,
            "target": target,
            "gone": shared.taleo_inactive_redirect(board, resource, target),
        }
    )

page_inputs = [
    ("total-empty", listing([], 0), 0),
    ("total-ten", listing(range(1, 11), 17), 0),
    ("total-child", listing(range(11, 18), 17), 10),
    ("wrong-count", listing(range(1, 10), 17), 0),
    ("cursor-empty", listing([]), 0),
    ("cursor-first", listing(range(1, 11), next_offset=10), 0),
    ("cursor-child", listing([11]), 10),
    ("cursor-wrong-count", listing([1], next_offset=10), 0),
    ("cursor-skips", listing(range(1, 11), next_offset=20), 0),
    (
        "foreign-cursor",
        listing(range(1, 11), next_offset=10).replace(
            'href="/phe01/ats/careers/v2/searchResults',
            'href="https://evil.test/phe01/ats/careers/v2/searchResults',
        ),
        0,
    ),
    (
        "cursor-query-scope",
        listing(range(1, 11), next_offset=10).replace("next&amp;", "next=x&amp;"),
        0,
    ),
    ("duplicate-links", listing([1, 1], 1), 0),
    ("relative-link", listing([1], 1).replace("https://phe.tbe.taleo.net", ""), 0),
    ("foreign-job", listing([1], 1).replace("org=ACME", "org=OTHER"), 0),
    ("numeric-unicode", listing([1], "１"), 0),
    ("count-comma", listing(range(1, 11), "1,234"), 0),
    ("count-nested", listing([1], "<b>1</b>"), 0),
    ("count-label", listing([], "Open"), 0),
    ("count-conflict", listing([], 0) + '<span class="oracletaleocwsv2-panel-number">1</span>', 0),
    ("not-listing", "<html>challenge</html>", 0),
]
pages = []
for name, body, offset in page_inputs:
    case = {"name": name, "body": body, "offset": offset}
    try:
        total, result, next_offset = monitor._parse_page(body, board, row_from=offset)
        case.update(total=total, urls=sorted(result), next=next_offset, failed=False)
    except Exception:
        case.update(failed=True, urls=[])
    pages.append(case)


async def inventory(name, bodies):
    calls = []

    async def first(b, client, **kwargs):
        calls.append(b.listing_url())
        return b, bodies[0]

    async def child(b, client, offset):
        calls.append(b.listing_url(row_from=offset))
        body = bodies.get(offset)
        if body is None:
            raise ValueError("later fixture request failed")
        return body

    case = {
        "name": name,
        "board_url": board.listing_url(),
        "metadata": identity(board),
        "bodies": {str(k): v for k, v in bodies.items()},
        "calls": calls,
    }
    try:
        with (
            patch.object(monitor, "_fetch_first_page", first),
            patch.object(monitor, "_fetch_page", child),
        ):
            result = await monitor.discover(
                {"board_url": board.listing_url(), "metadata": identity(board)}, None
            )
        case.update(urls=sorted(result), failed=False)
    except Exception:
        case.update(urls=[], failed=True)
    return case


async def main():
    cases = [
        ("total-complete", {0: listing(range(1, 11), 17), 10: listing(range(11, 18), 17)}),
        ("total-empty", {0: listing([], 0)}),
        ("total-changed", {0: listing(range(1, 11), 17), 10: listing(range(11, 19), 18)}),
        ("total-overlap", {0: listing(range(1, 11), 17), 10: listing(range(10, 17), 17)}),
        ("total-late-failure", {0: listing(range(1, 11), 17), 10: None}),
        ("total-cap", {0: listing(range(1, 11), 50001)}),
        ("cursor-complete", {0: listing(range(1, 11), next_offset=10), 10: listing([11])}),
        ("cursor-empty", {0: listing([])}),
        ("cursor-child-empty", {0: listing(range(1, 11), next_offset=10), 10: listing([])}),
        ("cursor-overlap", {0: listing(range(1, 11), next_offset=10), 10: listing([10])}),
        ("cursor-theme-change", {0: listing(range(1, 11), next_offset=10), 10: listing([11], 11)}),
        ("cursor-late-failure", {0: listing(range(1, 11), next_offset=10), 10: None}),
    ]
    inventories = [await inventory(name, bodies) for name, bodies in cases]
    Path(__file__).with_name("python_taleo.json").write_text(
        json.dumps(
            {
                "board": identity(board),
                "identities": identities,
                "redirects": redirects,
                "inactive": inactive,
                "pages": pages,
                "inventories": inventories,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


asyncio.run(main())
