"""Freeze the existing iCIMS monitor with in-memory pages, without network/DB."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

from src.core.monitors.icims import _listing_url, discover

host = "careers-native.icims.com"
child = "careers-child.icims.com"
peer = "careers-peer.icims.com"
jibe = "https://careers.example.net/jobs"


def listing(ids, tenant=host, page=1, pages=1, cards=False):
    links = []
    for identity in ids:
        target_host, job_id = identity if isinstance(identity, tuple) else (tenant, identity)
        url = f"https://{target_host}/jobs/{job_id}/engineer/job?untrusted=discarded"
        if cards:
            links.append(
                f'<div class="iCIMS_JobCardItem"><a class="iCIMS_Anchor" href="{url}">Open</a>'
                '<h3>Software Engineer</h3><div class="header left">'
                '<span class="sr-only">Location</span>'
                '<span>Zurich</span></div><dl class="iCIMS_JobHeaderTag"><dt>Job Type</dt>'
                "<dd>Full Time</dd></dl></div>"
            )
        else:
            links.append(f'<a href="{url}">Engineer</a>')
    return (
        '<html><head><title>Careers</title></head><body class="iCIMS_ListingsPage">'
        + f"Page {page} of {pages}"
        + "".join(links)
        + "</body></html>"
    )


def api_job(job_id, tenant=host):
    return {"data": {"slug": str(job_id), "apply_url": f"https://{tenant}/jobs/{job_id}/login"}}


cases = [
    ("classic", {}, {_listing_url(host): listing([1, 2])}),
    (
        "sequential-pages",
        {},
        {
            _listing_url(host): listing([1], pages=2),
            _listing_url(host, 1): listing([2], page=2, pages=2),
        },
    ),
    (
        "duplicates-truncate",
        {},
        {
            _listing_url(host): listing([1], pages=2),
            _listing_url(host, 1): listing([1], page=2, pages=2),
        },
    ),
    (
        "changed-page-total",
        {},
        {
            _listing_url(host): listing([1], pages=2),
            _listing_url(host, 1): listing([2], page=2, pages=3),
        },
    ),
    ("empty-board", {}, {_listing_url(host): listing([])}),
    ("wrong-first-page", {}, {_listing_url(host): listing([1], page=2, pages=2)}),
    ("zero-first-page", {}, {_listing_url(host): listing([1], page=0)}),
    (
        "empty-advertised-page",
        {},
        {
            _listing_url(host): listing([1], pages=2),
            _listing_url(host, 1): listing([], page=2, pages=2),
        },
    ),
    (
        "aggregate",
        {"job_hosts": [host, child]},
        {
            _listing_url(host): listing([(child, 1)]),
            _listing_url(child): listing([1], tenant=child),
        },
    ),
    (
        "aggregate-mismatch",
        {"job_hosts": [host, child]},
        {
            _listing_url(host): listing([(child, 1)]),
            _listing_url(child): listing([2], tenant=child),
        },
    ),
    (
        "id-dedupe",
        {"dedupe_job_ids_from_hosts": [peer]},
        {_listing_url(host): listing([1, 2]), _listing_url(peer): listing([2], tenant=peer)},
    ),
    (
        "locale-multiset",
        {"cross_locale_dedupe": {"peer_host": peer}},
        {
            _listing_url(host): listing([1, 2], cards=True),
            _listing_url(peer): listing([3], tenant=peer, cards=True),
        },
    ),
    (
        "locale-incomplete-card",
        {"cross_locale_dedupe": {"peer_host": peer}},
        {
            _listing_url(host): listing([1]),
            _listing_url(peer): listing([2], tenant=peer, cards=True),
        },
    ),
    ("non-listing", {}, {_listing_url(host): "<html><body>Wrong document</body></html>"}),
    (
        "jibe",
        {"jibe_url": jibe, "jibe_job_hosts": [host, child]},
        {
            _listing_url(
                host
            ): f'<script type="text/javascript">window.top.location.href = "{jibe}";</script>',
            "https://careers.example.net/api/jobs?page=1&limit=100": {
                "totalCount": 3,
                "jobs": [api_job(1), api_job(2, child)],
            },
            "https://careers.example.net/api/jobs?page=2&limit=100": {
                "totalCount": 3,
                "jobs": [api_job(3)],
            },
        },
    ),
    (
        "jibe-repeated-slug",
        {"jibe_url": jibe, "jibe_job_hosts": [host]},
        {
            _listing_url(
                host
            ): f'<script type="text/javascript">window.top.location.href = "{jibe}";</script>',
            "https://careers.example.net/api/jobs?page=1&limit=100": {
                "totalCount": 2,
                "jobs": [api_job(1), api_job(1)],
            },
        },
    ),
]


async def run():
    output = []
    for name, metadata, responses in cases:
        requests = []

        async def text(client, url, responses=responses, requests=requests, **kwargs):
            requests.append(
                {"url": url, "json": False, "follow_redirects": kwargs["follow_redirects"]}
            )
            return responses[url]

        async def data(client, url, responses=responses, requests=requests, **kwargs):
            url += "?" + urlencode(kwargs["params"])
            requests.append(
                {"url": url, "json": True, "follow_redirects": kwargs["follow_redirects"]}
            )
            return responses[url]

        with (
            patch("src.core.monitors.icims.fetch_text_page_with_retry", text),
            patch("src.core.monitors.icims.fetch_json_page_with_retry", data),
        ):
            try:
                found = await discover(
                    {"board_url": f"https://{host}/jobs/search", "metadata": metadata}, None
                )
                expected = {
                    "urls": sorted(found.urls if hasattr(found, "urls") else found),
                    "truncated": bool(getattr(found, "truncated", False)),
                }
            except (ValueError, RuntimeError):
                expected = {"error": True}
        output.append(
            {
                "name": name,
                "board_url": f"https://{host}/jobs/search",
                "metadata": metadata,
                "responses": responses,
                "requests": requests,
                "expected": expected,
            }
        )
    Path(__file__).with_name("python_icims.json").write_text(json.dumps(output, indent=2) + "\n")


asyncio.run(run())
