"""Freeze the existing partition collector on synthetic complete/failing snapshots."""

from __future__ import annotations

import asyncio
import copy
import html
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitors.dom import _paginate_partitioned_urls_once  # noqa: E402

ROOT = "https://jobs.example.com/list?lang=en"


def body(count, jobs=(), facets=()):
    return (
        f"<html><head><title>Jobs ({count} vacancies)</title></head><body>"
        + "".join(f'<a href="https://jobs.example.com/job/{j}">{j}</a>' for j in jobs)
        + "".join(f'<a class="{kind}" href="{html.escape(url)}">Facet</a>' for kind, url in facets)
        + "</body></html>"
    )


def base():
    return {
        "url_filter": r"/job/",
        "pagination": {
            "param_name": "page",
            "max_pages": 6,
            "transient_403": True,
            "partition_selector": "a.primary",
            "partition_count_regex": r"\((\d+)\s+vacancies",
            "partition_validate_total": True,
            "partition_stateless": True,
            "partition_drop_params": ["changefacet"],
        },
    }


def facet(value):
    return ROOT + "&facet=" + value


def leaf(pages, url, count, jobs):
    pages[url] = {"status": 200, "body": body(count, jobs)}
    pages[url + "&page=2"] = {"status": 200, "body": body(count, jobs)}


cases = []


def add(name, metadata, pages):
    cases.append({"name": name, "metadata": metadata, "pages": pages})


md = base()
pages = {
    ROOT: {
        "status": 200,
        "body": body(3, facets=[("primary", "?facet=a&changefacet=1"), ("primary", "?facet=b")]),
    }
}
leaf(pages, facet("a"), 1, ["one"])
leaf(pages, facet("b"), 2, ["two", "three"])
add("counted-flat", md, pages)
md = copy.deepcopy(md)
del md["pagination"]["partition_count_regex"]
md["pagination"]["partition_validate_total"] = False
add("uncounted-flat", md, copy.deepcopy(pages))
broken = copy.deepcopy(pages)
broken[ROOT]["body"] = broken[ROOT]["body"].replace("(3 vacancies)", "(4 vacancies)")
add("primary-count-changed", base(), broken)
broken = copy.deepcopy(pages)
broken[facet("b")]["body"] = body(2, ["two"])
broken[facet("b") + "&page=2"]["body"] = body(2, ["two"])
add("leaf-incomplete", base(), broken)
broken = copy.deepcopy(pages)
broken[facet("b") + "&page=2"] = {"status": 403, "body": "blocked"}
add("blocked-tail", base(), broken)
broken = copy.deepcopy(pages)
broken[facet("b") + "&page=2"] = {"status": 200, "body": ""}
add("empty-success-tail", base(), broken)
broken = copy.deepcopy(pages)
broken[ROOT]["body"] = body(3, facets=[("primary", "https://other.example.com/list?facet=a")])
add("cross-origin-facet", base(), broken)

md = base()
md["pagination"].update(partition_result_limit=2, partition_fallback_selector="a.child")
pages = {ROOT: {"status": 200, "body": body(4, facets=[("primary", "?facet=a")])}}
pages[facet("a")] = {
    "status": 200,
    "body": body(4, ["one"], [("child", "?child=x"), ("child", "?child=y")]),
}
leaf(pages, facet("a") + "&child=x", 2, ["one", "two"])
leaf(pages, facet("a") + "&child=y", 2, ["three", "four"])
add("exact-fallback", md, pages)
broken = copy.deepcopy(pages)
leaf(broken, facet("a") + "&child=y", 1, ["three"])
add("fallback-count-changed", copy.deepcopy(md), broken)
broken = copy.deepcopy(pages)
broken[facet("a")]["body"] = body(4, ["one"])
add("missing-fallback", copy.deepcopy(md), broken)

md = base()
md["pagination"].update(partition_result_limit=2, partition_cover_paths=[["a.left"], ["a.right"]])
pages = {ROOT: {"status": 200, "body": body(3, facets=[("primary", "?facet=a")])}}
pages[facet("a")] = {
    "status": 200,
    "body": body(3, ["one"], [("left", "?left=x"), ("right", "?right=y")]),
}
leaf(pages, facet("a") + "&left=x", 2, ["one", "two"])
leaf(pages, facet("a") + "&right=y", 2, ["two", "three"])
add("overlapping-cover", md, pages)
broken = copy.deepcopy(pages)
broken[ROOT]["body"] = broken[ROOT]["body"].replace("(3 vacancies)", "(4 vacancies)")
broken[facet("a")]["body"] = broken[facet("a")]["body"].replace("(3 vacancies)", "(4 vacancies)")
add("incomplete-cover", copy.deepcopy(md), broken)


async def no_wait(_):
    return None


async def main():
    for row in cases:
        metadata, pages = row["metadata"], row["pages"]

        async def respond(request, fixture_pages=pages):
            page = fixture_pages.get(str(request.url), {"status": 404, "body": "missing"})
            return httpx.Response(page["status"], text=page["body"], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            try:
                with patch("src.shared.http_retry.asyncio.sleep", new=no_wait):
                    result = await _paginate_partitioned_urls_once(
                        ROOT,
                        pages[ROOT]["body"],
                        metadata["pagination"],
                        client,
                        re.compile(metadata["url_filter"]),
                        None,
                        None,
                        None,
                    )
                row["expected"] = sorted(result)
                row["error"] = False
            except Exception as exc:
                row["expected"] = []
                row["error"] = True
                row["error_type"] = type(exc).__name__
        row["board_url"] = ROOT
    path = Path(__file__).with_name("python_dom_partitions.json")
    path.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {len(cases)} original partition cases")


asyncio.run(main())
