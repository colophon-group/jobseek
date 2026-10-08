"""Freeze actual anonymous Notion detail rendering and property contracts."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.scrapers import notion

PAGE = "11111111-1111-1111-1111-111111111111"
URL = "https://fixture.notion.site/" + PAGE.replace("-", "")


def record(kind, title, content=None, nested=False):
    value = dict(type=kind, properties=dict(title=title))
    if content is not None:
        value["content"] = content
    return dict(value=dict(value=value)) if nested else dict(value=value)


def payload():
    return dict(
        recordMap=dict(
            block={
                PAGE: record("page", [["Engineer & Operations"]], ["a", "b", "c", "d", "e"]),
                "a": record("header", [["Build <Go>", [["b"], ["i"]]]]),
                "b": record("bulleted_list", [["One"]]),
                "c": record("numbered_list", [["Two"]]),
                "d": record("toggle", [["Details"]], ["f", "g"]),
                "e": record("divider", []),
                "f": record("bulleted_list", [["Nested"]]),
                "g": record("quote", [["Read", [["a", "https://example.com/?x='&y=\""]]]]),
            },
            collection={
                "jobs": dict(
                    value=dict(
                        schema={
                            "title": dict(name="Role", type="title"),
                            "loc": dict(name="Location", type="multi_select"),
                            "team": dict(name="Team", type="select"),
                            "type": dict(name="Employment Type", type="select"),
                            "remote": dict(name="Remote", type="select"),
                        }
                    )
                )
            },
        )
    )


async def freeze():
    base = payload()
    props = base["recordMap"]["block"][PAGE]["value"]["properties"]
    props.update(
        loc=[["Paris, Zürich"]], team=[["Platform"]], type=[["Full time"]], remote=[["Remote"]]
    )
    cases = []
    for name in (
        "complete",
        "double-wrapper",
        "single-location",
        "property-override",
        "empty",
        "property-precedence",
        "scalar-property",
        "bad-rich-text",
        "bad-link",
        "unknown-block",
    ):
        data = copy.deepcopy(base)
        config = {}
        blocks = data["recordMap"]["block"]
        schema = data["recordMap"]["collection"]["jobs"]["value"]["schema"]
        if name == "double-wrapper":
            for block in blocks.values():
                block["value"] = dict(value=block["value"])
            collection = data["recordMap"]["collection"]["jobs"]
            collection["value"] = dict(value=collection["value"])
        elif name == "single-location":
            schema["loc"]["type"] = "select"
        elif name == "property-override":
            config = dict(property_map=dict(team="locations", location="metadata.team"))
        elif name == "empty":
            blocks[PAGE] = record("page", [], [])
        elif name == "property-precedence":
            schema["later"] = dict(name="City", type="select")
            blocks[PAGE]["value"]["properties"]["later"] = [["London"]]
        elif name == "scalar-property":
            blocks[PAGE]["value"]["properties"]["team"] = [[True], [None], [13]]
        elif name == "bad-rich-text":
            blocks["a"]["value"]["properties"]["title"] = [[13]]
        elif name == "bad-link":
            blocks["g"]["value"]["properties"]["title"] = [["Read", [["a", True]]]]
        elif name == "unknown-block":
            blocks["a"]["value"]["type"] = "unsupported"

        def handler(_request, data=data):
            return httpx.Response(200, json=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                output = asdict(await notion.scrape(URL, config, client))
                output = {
                    k: output[k]
                    for k in (
                        "title",
                        "description",
                        "locations",
                        "employment_type",
                        "job_location_type",
                        "metadata",
                    )
                }
                error = False
            except (TypeError, AttributeError):
                output, error = None, True
        cases.append(
            dict(name=name, page_id=PAGE, data=data, config=config, output=output, error=error)
        )
    return cases


Path(__file__).with_name("python_notion_detail.json").write_text(
    json.dumps(asyncio.run(freeze()), indent=2, ensure_ascii=False) + "\n"
)
