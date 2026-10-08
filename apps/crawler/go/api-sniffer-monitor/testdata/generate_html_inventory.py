"""Freeze HTML-in-JSON URL inventories through original Python HTTP discovery."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import generate_inventory as oracle


async def main():
    def config(**extra):
        return {"api_url": oracle.API, "json_path": "html", **extra}

    def links(*ids):
        return "".join(f'<a href="/jobs/{i}">Role</a>' for i in ids)

    await oracle.run(
        "default-hrefs",
        config(),
        {
            (0, None): (
                200,
                {
                    "html": links(1, 2, 1)
                    + '<a href="#top">x</a><a href="mailto:x">x</a><a href="javascript:x">x</a>'
                },
            )
        },
    )
    await oracle.run(
        "custom-capture",
        config(url_regex=r'(?i)data-job=["\'](/job/[^"\']+)["\']'),
        {(0, None): (200, {"html": '<div DATA-JOB="/job/Zurich"><a href="/ignored">x</a></div>'})},
    )
    await oracle.run(
        "nested-html",
        config(json_path="postings.jobs", total_path="postings.size"),
        {(0, None): (200, {"postings": {"jobs": links(1, 2), "size": 2}})},
    )
    await oracle.run(
        "root-list-html", config(json_path="[0].html"), {(0, None): (200, [{"html": links(1)}])}
    )
    await oracle.run("empty-html", config(), {(0, None): (200, {"html": ""})})
    await oracle.run("initial-legitimate-tail", config(), {(0, None): (404, {})})
    for valid in (True, False):
        await oracle.run(
            f"array-root-empty-marker-{valid}",
            config(
                json_path="[0].content.rendered",
                empty_response={"[0].id": 44033, "[0].content.protected": False},
            ),
            {
                (0, None): (
                    200,
                    [{"id": 44033, "content": {"rendered": "", "protected": not valid}}],
                )
            },
        )
    empty = {"found_jobs": False}
    await oracle.run(
        "explicit-empty",
        config(empty_response=empty),
        {(0, None): (200, {"html": "", "found_jobs": False})},
    )
    await oracle.run(
        "unproven-empty",
        config(empty_response=empty),
        {(0, None): (200, {"html": "", "found_jobs": True})},
    )
    pg = {"param_name": "page", "start_value": 1, "increment": 1, "max_pages": 4}
    await oracle.run(
        "paged-union",
        config(params={"page": 1}, pagination=pg, total_path="total"),
        {
            (1, None): (200, {"html": links(1, 2), "total": 4}),
            (2, None): (200, {"html": links(3, 4), "total": 4}),
        },
    )
    await oracle.run(
        "no-growth",
        config(params={"page": 1}, pagination=pg),
        {(1, None): (200, {"html": links(1, 2)}), (2, None): (200, {"html": links(2, 1)})},
    )
    await oracle.run(
        "legitimate-tail",
        config(params={"page": 1}, pagination=pg),
        {(1, None): (200, {"html": links(1, 2)}), (2, None): (404, {})},
    )
    await oracle.run(
        "empty-tail",
        config(params={"page": 1}, pagination=pg),
        {(1, None): (200, {"html": links(1, 2)}), (2, None): (200, {"html": " "})},
    )
    await oracle.run(
        "schema-tail-advertised-gap",
        config(params={"page": 1}, pagination=pg, total_path="total"),
        {(1, None): (200, {"html": links(1, 2), "total": 10}), (2, None): (200, {"html": []})},
    )
    await oracle.run(
        "page-size-cap",
        config(
            params={"page": 1},
            pagination={**pg, "page_size": 100, "max_pages": 2},
            total_path="total",
        ),
        {
            (1, None): (200, {"html": links(1, 2), "total": 250}),
            (2, None): (200, {"html": links(3), "total": 250}),
        },
    )
    await oracle.run(
        "body-pagination",
        config(method="POST", post_data={"page": 1}, pagination={**pg, "location": "body"}),
        {
            (1, None): (200, {"html": links(1)}),
            (2, None): (200, {"html": links(2)}),
            (3, None): (200, {"html": ""}),
        },
    )
    await oracle.run(
        "later-transient-discards-all",
        config(params={"page": 1}, pagination=pg),
        {(1, None): (200, {"html": links(1, 2)}), (2, None): (503, {})},
    )
    Path(__file__).with_name("python_html_inventory.json").write_text(
        json.dumps(oracle.cases, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(oracle.cases)} original Python HTML HTTP cases")


if __name__ == "__main__":
    asyncio.run(main())
