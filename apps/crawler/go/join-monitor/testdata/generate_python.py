"""Freeze the production Python JOIN page parser without publisher traffic."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.core.monitors import nextdata
from src.core.monitors.join import _build_metadata


def page(items, count=1):
    data = {
        "props": {
            "pageProps": {
                "initialState": {"jobs": {"items": items, "pagination": {"pageCount": count}}}
            }
        }
    }
    return '<script id="__NEXT_DATA__">' + json.dumps(data, ensure_ascii=False) + "</script>"


async def main():
    cases = []

    def add(name, html, *, first=True, fill="x", prefix=0, suffix=0):
        cases.append(
            dict(name=name, html=html, first=first, fill=fill, prefix=prefix, suffix=suffix)
        )

    values = [
        "42-engineer",
        "Über-uns",
        "",
        "a/b?x=1",
        0,
        -12,
        9007199254740993,
        1.0,
        -0.0,
        1e-4,
        1e-5,
        1e15,
        1e16,
        1.2345678901234567,
        True,
        False,
        None,
        [],
        {},
    ]
    for index, value in enumerate(values):
        add(f"id-{index}", page([{"idParam": value}]))
    add(
        "mixed-and-duplicates",
        page([None, 3, [], {"x": "skip"}, {"idParam": "a"}, {"idParam": "a"}]),
    )
    for index, value in enumerate(
        [0, 1, 2, -1, "1", " 2 ", "2_0", True, False, 1.9, -1.9, "bad", None, [], {}]
    ):
        add(f"count-{index}", page([{"idParam": "a"}], value))
    for count in [0, 1, 2, -1, None]:
        add(f"empty-first-{count}", page([], count))
    add("empty-required", page([]), first=False)
    add("required-count-ignored", page([{"idParam": "a"}], "bad"), first=False)
    add("missing-items", page(None))
    add("missing-script", "<html>missing</html>")
    add("invalid-json", '<script id="__NEXT_DATA__">{bad}</script>')
    add("trailing-json", page([{"idParam": "a"}]).replace("</script>", "{} </script>"))
    add("wrong-root", '<script id="__NEXT_DATA__">[]</script>')
    add("prefix-inside-character-bound", page([{"idParam": "a"}]), prefix=3_999_000)
    add("prefix-outside-character-bound", page([{"idParam": "a"}]), prefix=4_000_000)
    add("suffix-beyond-bound", page([{"idParam": "a"}]), suffix=4_001_000)
    add("unicode-prefix-inside-bound", page([{"idParam": "a"}]), fill="é", prefix=3_999_000)
    add("unicode-prefix-outside-bound", page([{"idParam": "a"}]), fill="é", prefix=4_000_000)

    async def no_delay(_):
        pass

    original_sleep = nextdata.asyncio.sleep
    nextdata.asyncio.sleep = no_delay
    try:
        for case in cases:
            html = case["fill"] * case["prefix"] + case["html"] + case["fill"] * case["suffix"]
            metadata = _build_metadata("acme")
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request, html=html: httpx.Response(200, text=html)
                )
            ) as client:
                try:
                    data, items = await nextdata._fetch_embedded_page_with_retry(
                        "https://join.com/companies/acme",
                        render=False,
                        client=client,
                        path=metadata["path"],
                        source="nextdata",
                        allow_empty=case["first"],
                    )
                    count = 0
                    if case["first"]:
                        nextdata._validate_empty_first_page(
                            items,
                            data,
                            metadata["pagination"],
                            board_url="https://join.com/companies/acme",
                        )
                        count = nextdata._resolve_page_count(data, metadata["pagination"])
                        if count is None:
                            raise ValueError("invalid page count")
                    urls = nextdata._extract_urls(items, metadata["url_template"], None)
                    case["expected"] = {"urls": sorted(urls), "page_count": count}
                except (RuntimeError, ValueError):
                    case["expected_error"] = True
    finally:
        nextdata.asyncio.sleep = original_sleep
    path = Path(__file__).with_name("python_pages.json")
    path.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n")
    print(f"froze {len(cases)} Python page cases")


asyncio.run(main())
