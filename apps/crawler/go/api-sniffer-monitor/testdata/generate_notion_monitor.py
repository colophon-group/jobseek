"""Freeze actual anonymous Notion discovery requests and complete inventories."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.core.monitors import notion

HOME = "11111111-1111-1111-1111-111111111111"
JOB = "22222222-2222-2222-2222-222222222222"
NESTED = "33333333-3333-3333-3333-333333333333"
SPACE = "44444444-4444-4444-4444-444444444444"
COLLECTION = "55555555-5555-5555-5555-555555555555"
VIEW = "66666666-6666-6666-6666-666666666666"
BASE = "https://fixture.notion.site"


def rec(kind, title, **kwargs):
    return dict(value=dict(type=kind, properties=dict(title=[[title]]), **kwargs))


def graph():
    return dict(
        recordMap=dict(
            block={
                HOME: rec("page", "Careers", content=["layout"]),
                "layout": rec("column", "", content=[JOB]),
                JOB: rec("page", "Engineer", content=[NESTED]),
                NESTED: rec("page", "Embedded description page"),
            }
        )
    )


async def no_wait(_delay):
    pass


async def freeze():
    cases = []
    saved_sleep = notion.asyncio.sleep
    saved_api = notion._api_post
    notion.asyncio.sleep = no_wait
    try:
        for name in (
            "layout-boundary",
            "nested-pages",
            "title-exclude",
            "url-exclude",
            "slug-resolution",
            "root-canonical-fallback",
            "explicit-no-fallback",
            "root-403",
            "transient-chunk",
            "collection",
            "collection-title-filter",
            "collection-property-filter",
            "late-collection-failure",
            "explicit-home-fallback",
        ):
            metadata = {}
            board = BASE + "/"
            chunk = graph()
            public = dict(spaceId=SPACE, publicHomePage=HOME)
            if name == "nested-pages":
                metadata["include_nested"] = True
            elif name == "title-exclude":
                metadata["title_exclude"] = "engineer"
            elif name == "url-exclude":
                metadata["url_filter"] = dict(exclude=JOB.replace("-", ""))
            elif name == "slug-resolution":
                board = BASE + "/careers"
            elif name in ("explicit-no-fallback", "explicit-home-fallback"):
                board = BASE + "/" + JOB.replace("-", "")
            if name.startswith("collection") or name == "late-collection-failure":
                chunk = dict(
                    recordMap=dict(
                        block={
                            HOME: rec("page", "Careers", content=["database"]),
                            "database": rec(
                                "collection_view", "Jobs", collection_id=COLLECTION, view_ids=[VIEW]
                            ),
                        }
                    )
                )
            if name == "collection-title-filter":
                metadata["title_exclude"] = "engineer"
            elif name == "collection-property-filter":
                metadata["property_filter"] = dict(
                    include=dict(Status="OPEN"), exclude=dict(Team="Other")
                )
            query = dict(
                result=dict(reducerResults=dict(collection_group_results=dict(blockIds=[JOB]))),
                recordMap=dict(
                    block={JOB: rec("page", "Engineer")},
                    collection={
                        COLLECTION: dict(
                            value=dict(
                                schema={
                                    "status": dict(name="Status", type="select"),
                                    "team": dict(name="Team", type="select"),
                                }
                            )
                        )
                    },
                ),
            )
            query["recordMap"]["block"][JOB]["value"]["properties"].update(
                status=[["Open"]], team=[["Platform"]]
            )
            calls = []
            logical_calls = []
            seen = {}

            async def observe_api(
                client, subdomain, endpoint, payload, *, api_host=None, logical_calls=logical_calls
            ):
                logical_calls.append(
                    dict(
                        url=f"https://{api_host or subdomain + '.notion.site'}/api/v3/{endpoint}",
                        payload=payload,
                    )
                )
                return await saved_api(client, subdomain, endpoint, payload, api_host=api_host)

            notion._api_post = observe_api

            def handler(
                request, name=name, chunk=chunk, public=public, query=query, calls=calls, seen=seen
            ):
                endpoint = str(request.url)
                payload = json.loads(request.content)
                calls.append(dict(url=endpoint, payload=payload))
                seen[endpoint] = seen.get(endpoint, 0) + 1
                if endpoint.endswith("getPublicPageData"):
                    if "www.notion.so" not in endpoint:
                        if name in ("root-canonical-fallback", "explicit-no-fallback"):
                            return httpx.Response(500, json={})
                        if name == "root-403":
                            return httpx.Response(403, json={})
                    return httpx.Response(200, json=public)
                if endpoint.endswith("loadPageChunk"):
                    if name == "transient-chunk" and seen[endpoint] == 1:
                        return httpx.Response(503, json={})
                    if name == "explicit-home-fallback" and payload["page"]["id"] == JOB:
                        return httpx.Response(
                            200,
                            json=dict(
                                recordMap=dict(block={JOB: rec("page", "No jobs", content=[])})
                            ),
                        )
                    return httpx.Response(200, json=chunk)
                assert endpoint.endswith("queryCollection"), endpoint
                if name == "late-collection-failure":
                    return httpx.Response(503, json={})
                return httpx.Response(200, json=query)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                try:
                    urls = sorted(
                        await notion.discover(dict(board_url=board, metadata=metadata), client)
                    )
                    error = False
                except notion.PaginationFetchError:
                    urls, error = None, True
            cases.append(
                dict(
                    name=name,
                    board=board,
                    metadata=metadata,
                    public=public,
                    chunk=chunk,
                    query=query,
                    urls=urls,
                    error=error,
                    calls=calls,
                    logical_calls=logical_calls,
                )
            )
    finally:
        notion.asyncio.sleep = saved_sleep
        notion._api_post = saved_api
    return cases


Path(__file__).with_name("python_notion_monitor.json").write_text(
    json.dumps(asyncio.run(freeze()), indent=2, ensure_ascii=False) + "\n"
)
