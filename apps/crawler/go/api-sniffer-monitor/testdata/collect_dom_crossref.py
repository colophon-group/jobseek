from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.shared.api_sniff import extract_urls_via_dom_crossref


class Page:
    def __init__(self, links: list[str]) -> None:
        self.links = links

    async def evaluate(self, _expression: str) -> list[str]:
        return self.links


async def main() -> None:
    board = "https://example.com/careers"
    cases = [
        (
            "numeric-csod",
            [{"requisitionId": 3829}, {"requisitionId": 3821}],
            ["https://example.com/ux/ats/requisition/3829"],
        ),
        (
            "slug",
            [{"id": 1, "slug": "one"}, {"id": 2, "slug": "two"}],
            ["https://example.com/jobs/1/one"],
        ),
        (
            "query-fields",
            [
                {"id": 1, "locale": "en", "location": {"name": "New York"}},
                {"id": 2, "locale": "de", "location": {"name": "Zug"}},
            ],
            ["https://example.com/jobs/1?locale=en&city=New+York&unrelated=private"],
        ),
        (
            "first-id-field",
            [{"externalId": "AA", "id": "BB"}, {"externalId": "CC", "id": "DD"}],
            ["https://example.com/jobs/AA", "https://example.com/jobs/BB"],
        ),
        (
            "query-only-first-reference",
            [{"id": "AA"}],
            ["https://example.com/jobs?id=AA", "https://example.com/jobs/AA"],
        ),
        ("foreign-host", [{"id": "AA"}], ["https://foreign.example/jobs/AA"]),
        ("missing-id", [{"title": "A"}], ["https://example.com/jobs/A"]),
        ("missing-item-id", [{"id": "AA"}, {"title": "B"}], ["https://example.com/jobs/AA"]),
        ("first-ten-only", [{"id": i} for i in range(100, 111)], ["https://example.com/jobs/110"]),
        (
            "duplicate-id-last-content",
            [{"id": "AA", "slug": "first"}, {"id": "AA", "slug": "last"}],
            ["https://example.com/jobs/AA/first"],
        ),
        ("encoded-path", [{"id": "AA"}, {"id": "BB"}], ["https://example.com/jobs%2F/AA"]),
        (
            "blank-and-duplicate-query",
            [{"id": "AA", "locale": "en"}, {"id": "BB", "locale": "de"}],
            ["https://example.com/jobs/AA?locale=&locale=en&locale=ignored"],
        ),
    ]
    out = []
    for name, items, links in cases:
        urls = await extract_urls_via_dom_crossref(Page(links), items, board)
        out.append({"name": name, "board_url": board, "items": items, "links": links, "urls": urls})
    Path(__file__).with_name("python_dom_crossref.json").write_text(
        json.dumps(out, indent=2) + "\n"
    )


asyncio.run(main())
