from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from src.shared.api_sniff import _fetch_page_with_retry, make_browser_fetcher
from src.shared.tdm import TDMReservedError

GOOD = '{"maxCount":0,"offset":0,"count":0,"jobPostings":[]}'
CASES = [
    ("success", [(200, {}, GOOD)]),
    ("server_recovers", [(503, {}, "bad"), (200, {}, GOOD)]),
    ("server_exhausts", [(500, {}, "bad")] * 3),
    ("timeout_recovers", [(408, {}, "bad"), (200, {}, GOOD)]),
    ("early_recovers", [(425, {}, "bad"), (200, {}, GOOD)]),
    ("limited_recovers", [(429, {}, "bad"), (200, {}, GOOD)]),
    ("unauthorized", [(401, {}, "bad"), (200, {}, GOOD)]),
    ("forbidden", [(403, {}, "bad"), (200, {}, GOOD)]),
    ("invalid_json_recovers", [(200, {}, "bad"), (200, {}, GOOD)]),
    ("invalid_json_exhausts", [(200, {}, "bad")] * 3),
    ("empty_204_recovers", [(204, {}, ""), (200, {}, GOOD)]),
    ("reserved_before_json", [(200, {"tdm-reservation": "1"}, "bad")]),
    ("reserved_meta_before_json", [(200, {}, '<meta name="tdm-reservation" content="1">')]),
    ("failed_http_before_reservation", [(503, {"tdm-reservation": "1"}, "bad"), (200, {}, GOOD)]),
    ("nonretry_http_before_reservation", [(403, {"tdm-reservation": "1"}, "bad")]),
]


class Page:
    def __init__(self, pages, calls):
        self.pages = pages
        self.calls = calls

    async def evaluate(self, expression, args):
        self.calls.append(args[1])
        status, headers, text = self.pages[len(self.calls) - 1]
        return {"status": status, "headers": headers, "text": text}


async def main() -> None:
    results = []
    for name, pages in CASES:
        calls, delays = [], []

        async def wait(delay, delays=delays):
            delays.append(delay)

        outcome = "ok"
        with (
            patch("src.shared.api_sniff.asyncio.sleep", wait),
            patch("src.shared.api_sniff.random.random", return_value=0.25),
        ):
            try:
                await _fetch_page_with_retry(
                    make_browser_fetcher(Page(pages, calls)),
                    "POST",
                    "https://jobs.dayforcehcm.com/api/geo/fixture/jobposting/search",
                    {},
                    "{}",
                )
            except TDMReservedError:
                outcome = "reserved"
            except Exception:
                outcome = "failed"
        results.append(
            {
                "name": name,
                "pages": [
                    {"status": status, "headers": headers, "body": text}
                    for status, headers, text in pages
                ],
                "calls": len(calls),
                "delays": delays,
                "outcome": outcome,
            }
        )
    Path(__file__).with_name("python_dayforce_transport.json").write_text(
        json.dumps(results, indent=2) + "\n"
    )


asyncio.run(main())
