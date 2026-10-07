"""Freeze the actual Python HR Manager manifest/feed join without network access."""

from __future__ import annotations

import asyncio
import dataclasses
import html
import json
from pathlib import Path

import httpx

from src.core.monitors.rss import _PRESETS, _discover_hr_manager

BOARD = "https://candidate.hr-manager.net/vacancies/list.aspx?customer=fixture"
FEED = (
    "https://api.hr-manager.net/JobPortal.svc/fixture/"
    "PositionList/rss/?protype=RecruitmentProject&incads=true"
)


async def main():
    cases = []
    for mode in [
        "workplace",
        "multiple-locations",
        "primary-location",
        "missing-location",
        "language-invalid",
        "empty",
        "wrong-tenant",
        "failed-status",
        "wrong-count",
        "wrong-project",
        "duplicate-position",
        "duplicate-feed-id",
        "outside-position",
        "missing-feed-id",
        "missing-feed-link",
        "missing-position-feed",
        "malformed-feed",
    ]:
        position = {
            "Id": 123,
            "ProjectType": "RecruitmentProject",
            "WorkPlace": " Zurich ",
            "CustomList1": {"Name": " Full Time "},
            "Languages": [{"Code": "EN"}],
        }
        if mode in {"multiple-locations", "primary-location", "missing-location"}:
            position.pop("WorkPlace")
        if mode == "multiple-locations":
            position["PositionLocationMultiSelection"] = [
                {"Name": " Zurich "},
                {"Name": "Zurich"},
                {"Name": "Tokyo"},
                {"Name": ""},
            ]
        if mode == "primary-location":
            position["PositionLocation"] = {"Name": " Zurich "}
        if mode == "language-invalid":
            position["Languages"] = [{"Code": "en-US"}]
        if mode == "wrong-project":
            position["ProjectType"] = "Other"
        items = [position]
        if mode == "duplicate-position":
            items.append(position)
        if mode == "empty":
            items = []
        payload = {
            "PositionList": {
                "CustomerAlias": "other" if mode == "wrong-tenant" else "fixture",
                "TransactionStatus": {"StatusCode": 1 if mode == "failed-status" else 0},
                "PositionCountList": 2 if mode == "wrong-count" else len(items),
                "Items": items,
            }
        }
        page = (
            '<input id="prefix_HiddenField_PositionList" value="'
            + html.escape(json.dumps(payload), quote=True)
            + '">'
        )
        item = (
            "<item><link>https://candidate.hr-manager.net/jobs/123</link>"
            "<title>Senior Engineer</title><guid>123</guid>"
            "<description><![CDATA[<p>Build &amp; learn.</p>]]></description></item>"
        )
        if mode == "outside-position":
            item = item.replace("<guid>123</guid>", "<guid>456</guid>")
        if mode == "missing-feed-id":
            item = item.replace("<guid>123</guid>", "")
        if mode == "missing-feed-link":
            item = item.replace("<link>https://candidate.hr-manager.net/jobs/123</link>", "")
        if mode in {"empty", "missing-position-feed"}:
            item = ""
        if mode == "duplicate-feed-id":
            item += item
        feed = "<rss><channel>" + item + "</channel></rss>"
        if mode == "malformed-feed":
            feed = feed[:-6]

        def handler(request, page=page, feed=feed):
            return httpx.Response(200, text=page if str(request.url) == BOARD else feed)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                jobs = await _discover_hr_manager(
                    {"board_url": BOARD, "metadata": {"customer": "fixture"}},
                    client,
                    feed_url=FEED,
                    preset=_PRESETS["hr_manager"],
                )
                result = {"jobs": [dataclasses.asdict(job) for job in jobs], "error": False}
            except Exception as exc:
                result = {"jobs": [], "error": True, "error_type": type(exc).__name__}
        cases.append({"name": mode, "board": page, "feed": feed, **result})
    Path(__file__).with_name("python_hr_manager.json").write_text(
        json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(cases)} actual Python HR Manager cases")


asyncio.run(main())
