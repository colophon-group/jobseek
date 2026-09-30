"""Freeze offline B0 processing output from the existing Python pipeline."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import os
from pathlib import Path

os.environ["JOB_ENRICHMENT_ENGINE"] = "python"
import src.processing.scrape as scrape
from src.core.job_content import JobContent

occ = {"software-engineer": 41, "data-engineer": 52}
sen = {"senior": 7, "junior": 3, "intern": 9}
tech = {"python": 4, "go": 2, "postgresql": 4}
rates = {"CHF": 1.05, "USD": 0.9}


class Resolver:
    def drain_location_misses(self):
        return []


class Lookups:
    async def _get_location_resolver(self, pool):
        return Resolver()

    async def _get_technology_ids(self, pool):
        return tech

    async def _get_occupation_ids(self, pool):
        return occ

    async def _get_seniority_ids(self, pool):
        return sen

    async def _get_currency_rates(self, pool):
        return rates

    async def _resolve_locations(self, resolver, locations, kind, posting_language=None):
        pool_output["location_inputs"] = [locations, kind, posting_language]
        return ([2], ["hybrid"]) if locations else (None, None)


lookups = Lookups()


class Pool:
    async def fetchrow(self, *args):
        return current_case["existing"]

    async def execute(self, query, *args):
        if query == scrape._UPDATE_ENRICH_CONTENT:
            pool_output["fields"] = list(args[1:])
        elif query == scrape._RECORD_SCRAPE_SUCCESS:
            pool_output["disposition"] = "success"
        elif query == scrape._RECORD_SCRAPE_TRANSIENT:
            pool_output["disposition"] = "transient"
        elif query == scrape._RECORD_SCRAPE_FAILURE:
            pool_output["disposition"] = "gone" if args[1] else "budget"
        return "UPDATE 1"


pool = Pool()


@contextlib.asynccontextmanager
async def write(*args, **kwargs):
    yield pool


async def reserved(*args):
    return False


async def parse(*args, **kwargs):
    return JobContent(**copy.deepcopy(current_case["content"]))


async def staged(conn, *, staged, **kwargs):
    pool_output["description"] = {"html": staged[0], "locale": staged[1], "hash": staged[2]}


scrape.authoritative_write = write
scrape.posting_reserved = reserved
scrape._scrape_with_browser_target_recovery = parse
scrape._upsert_staged_description = staged
base = {
    "title": "Senior Software Engineer",
    "description": "<p>Python and Go. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>",
    "locations": ["Zurich"],
    "job_location_type": "hybrid",
    "employment_type": "Full Time",
    "language": "en",
}
rich = {"titles": ["Existing monitor title"], "location_ids": [7], "employment_type": "part-time"}
cases = []


def add(name, changes=None, enrich=None, existing=None):
    content = copy.deepcopy(base)
    content.update(changes or {})
    cases.append(
        {
            "name": name,
            "content": content,
            "config": ({"enrich": enrich} if enrich is not None else {}),
            "existing": existing,
        }
    )


add("ordinary")
add("ordinary_intern", {"employment_type": "internship"})
add("ordinary_no_description", {"description": None, "locations": None, "language": None})
add("ordinary_empty_language", {"language": False})
add("ordinary_blank_title", {"title": "   ", "language": None, "description": None})
add("ordinary_garbage", {"title": "Access Denied"})
add("ordinary_title_list", {"title": ["Engineer"]})
add("description_preserves_monitor_fields", enrich=["description"], existing=rich)
add(
    "description_backfills_empty_fields",
    enrich=["description"],
    existing={"titles": [], "location_ids": [], "employment_type": None},
)
add("description_no_existing_row", enrich=["description"])
add("title_no_language_preserves_locales", {"description": None, "language": None}, ["title"], rich)
add("employment_intern_without_title", {"employment_type": "internship"}, ["employment_type"], rich)
add("missing_description", {"description": None}, ["description"], rich)
add("degenerate_description", {"description": "<script>removed</script>"}, ["description"], rich)


async def main():
    global current_case, pool_output
    for case in cases:
        current_case = case
        pool_output = {}
        result, _ = await scrape._process_one_scrape(
            scrape.ScrapeItem(
                job_posting_id="00000000-0000-0000-0000-000000000001",
                url="https://fixture.invalid/jobs/1",
                board_id="00000000-0000-0000-0000-000000000002",
            ),
            pool,
            None,
            "json-ld",
            case["config"],
            authority_guard=write,
            lookup_provider=lookups,
        )
        case["expected"] = {"success": result, **pool_output}
    output = {
        "schema_version": 1,
        "oracle": "Python 3.13 src.processing.scrape ordinary/enrich fenced pipeline",
        "occupations": occ,
        "seniorities": sen,
        "technologies": tech,
        "rates": rates,
        "cases": cases,
    }
    Path("go/lightpanda-b0-executor/testdata/python_prepare.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(main())
