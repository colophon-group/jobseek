"""One-shot database/queue driver for the B0 whole-lane admission harness."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import asyncpg
from redis.asyncio import Redis

from src.lightpanda.producer_client import request_manifest, request_task
from src.redis_queue import ScrapeSchedule, close_redis, enqueue_scrapes

WORKLOAD_SHA256 = "4be1503fef65b7ac74f1085f168cd7d2e9db19060fdd8271abb7b9ca42ef5390"
COHORT = (
    "browser-use-careers",
    "eclypsium-careers",
    "kandou-ai-careers",
    "poke-and-wiggle-careers",
)
PARSER = {
    "browser_backend": "lightpanda",
    "routing_revision": "b0-admission-v1",
    "render": True,
    "wait": "load",
    "wait_fallback": None,
    "timeout": 120000,
    "ignore_locations": True,
}
STABLE_COLUMNS = """
id::text, company_id::text, board_id::text, source_url, is_active, locales,
titles, location_ids, location_types, employment_type, salary_min, salary_max,
salary_currency, salary_period, salary_eur, experience_min::text,
experience_max::text, occupation_id, seniority_id, technology_ids,
scrape_failures, to_be_enriched
"""


class AdmissionDriverError(RuntimeError):
    """The disposable admission arm violated its closed contract."""


_LOOKUP_TABLES = (
    "CREATE TABLE location (id integer PRIMARY KEY, parent_id integer, "
    "type text NOT NULL, population integer, languages text[])",
    "CREATE TABLE location_name (location_id integer NOT NULL, locale text NOT NULL, "
    "name text NOT NULL, is_display boolean)",
    "CREATE TABLE occupation (id integer PRIMARY KEY, slug text NOT NULL)",
    "CREATE TABLE seniority (id integer PRIMARY KEY, slug text NOT NULL)",
    "CREATE TABLE technology (id integer PRIMARY KEY, slug text NOT NULL)",
    "CREATE TABLE taxonomy_miss (taxonomy text NOT NULL, raw_value text NOT NULL, "
    "sample_value text NOT NULL, hit_count integer NOT NULL DEFAULT 1, "
    "last_seen_at timestamptz NOT NULL DEFAULT now(), "
    "status text NOT NULL DEFAULT 'pending', UNIQUE (taxonomy, raw_value))",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, default=str, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


def _synthetic_tasks(path: Path, concurrency: int) -> list[dict[str, str]]:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != WORKLOAD_SHA256:
        raise AdmissionDriverError("frozen workload digest changed")
    document = json.loads(raw)
    tasks: list[dict[str, str]] = []
    for wave in document.get("waves", []):
        for task in wave.get("tasks", []):
            origin = task["origin_id"]
            if int(origin.removeprefix("origin-")) >= concurrency:
                continue
            tasks.append(
                {
                    "id": task["id"],
                    "board_slug": COHORT[int(origin.removeprefix("origin-"))],
                    "source_url": f"https://{origin}.lane.bench.test{task['path']}",
                }
            )
    if len(tasks) != 4 * concurrency or len({item["id"] for item in tasks}) != len(tasks):
        raise AdmissionDriverError("frozen workload has the wrong distinct task count")
    return tasks


def load_tasks(path: Path, concurrency: int) -> list[dict[str, str]]:
    if concurrency not in {1, 4}:
        raise AdmissionDriverError("fixture concurrency must be c1 or c4")
    return _synthetic_tasks(path, concurrency)


def _board_id(slug: str) -> str:
    return str(uuid.uuid5(uuid.UUID("6d47d449-6f4c-4ec4-821c-53c40a1d5774"), slug))


def _metadata(candidate: bool) -> dict[str, Any]:
    parser = dict(PARSER)
    if not candidate:
        parser.pop("browser_backend")
        parser.pop("routing_revision")
        parser["skip_ssl"] = True
    return {"scraper_type": "json-ld", "scraper_config": parser, "rescrape_policy": "never"}


async def _seed(
    pool: asyncpg.Pool, redis: Redis, tasks: list[dict[str, str]], candidate: bool
) -> None:
    if await redis.dbsize() != 0:
        raise AdmissionDriverError("disposable Redis was not empty")
    company_id = uuid.UUID("00000000-0000-4000-8000-00000000ad01")
    await pool.execute("TRUNCATE company, job_board, job_posting, descriptions CASCADE")
    await pool.execute(
        "INSERT INTO company (id, slug, name) VALUES ($1, 'b0-admission', 'B0 admission')",
        company_id,
    )
    for slug in sorted({row["board_slug"] for row in tasks}):
        board_id = uuid.UUID(_board_id(slug))
        metadata = _metadata(candidate)
        await pool.execute(
            """INSERT INTO job_board
               (id, company_id, board_slug, crawler_type, board_url, metadata,
                scraper_needs_browser, scrape_interval_hours)
               VALUES ($1,$2,$3,'dom',$4,$5::jsonb,true,24)""",
            board_id,
            company_id,
            slug,
            f"https://{slug}.invalid/jobs",
            json.dumps(metadata),
        )
        await redis.hset(
            f"board:{board_id}",
            mapping={
                "company_id": str(company_id),
                "board_slug": slug,
                "board_url": f"https://{slug}.invalid/jobs",
                "crawler_type": "dom",
                "metadata": json.dumps(metadata, sort_keys=True),
                "scraper_needs_browser": "true",
                "scrape_interval_hours": "24",
            },
        )
    for row in tasks:
        await pool.execute(
            """INSERT INTO job_posting
               (id, company_id, board_id, source_url, next_scrape_at)
               VALUES ($1,$2,$3,$4,now())""",
            uuid.UUID(row["id"]),
            company_id,
            uuid.UUID(_board_id(row["board_slug"])),
            row["source_url"],
        )


async def _feed(
    redis: Redis, tasks: list[dict[str, str]], candidate: bool, due: float
) -> list[str]:
    schedules: list[ScrapeSchedule] = []
    legacy_configs: dict[str, dict[str, str]] = {}
    for row in tasks:
        domain = urlsplit(row["source_url"]).hostname or ""
        config = {
            "source_url": row["source_url"],
            "board_id": _board_id(row["board_slug"]),
            "description_r2_hash": "",
            "scraper_needs_browser": "true",
            "scrape_interval_hours": "24",
            "scrape_step": "0",
        }
        legacy_configs[row["id"]] = config
        schedules.append(
            ScrapeSchedule(
                domain=domain,
                posting_id=row["id"],
                next_scrape_at=due,
                config=config,
                browser=True,
            )
        )
    if not candidate:
        added = await enqueue_scrapes(schedules)
        if added != [True] * len(tasks):
            raise AdmissionDriverError("legacy feed did not add every task exactly once")
        return [row["id"] for row in tasks]

    cohort = "c1" if len({row["board_slug"] for row in tasks}) == 1 else "c4"
    manifest = await request_manifest(cohort)
    if manifest.outcome != "manifest" or set(row["board_slug"] for row in tasks) != set(
        manifest.board_slugs
    ):
        raise AdmissionDriverError("Go producer manifest differs from the fixture cohort")
    for row in tasks:
        prepared = await request_task(
            operation="prepare",
            domain=urlsplit(row["source_url"]).hostname or "",
            posting_id=row["id"],
            next_scrape_at=due,
            config=legacy_configs[row["id"]],
            browser=True,
            operator_transfer=True,
        )
        if prepared.outcome != "prepared":
            raise AdmissionDriverError("Go producer did not prepare the fixture task")
        activated = await request_task(
            operation="activate",
            domain=urlsplit(row["source_url"]).hostname or "",
            posting_id=row["id"],
            next_scrape_at=due,
            config=legacy_configs[row["id"]],
            browser=True,
            operator_transfer=True,
            expected_digest=prepared.preparation_digest,
        )
        if activated.outcome != "activated" or not activated.activated:
            raise AdmissionDriverError("Go producer did not activate the fixture task")
    return [row["id"] for row in tasks]


async def _wait(pool: asyncpg.Pool, ids: list[str], timeout: float) -> list[asyncpg.Record]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = await pool.fetch(
            f"SELECT {STABLE_COLUMNS}, last_scraped_at, next_scrape_at FROM job_posting "
            "WHERE id = ANY($1::uuid[]) ORDER BY id",
            ids,
        )
        if len(rows) == len(ids) and all(row["last_scraped_at"] is not None for row in rows):
            if any(row["next_scrape_at"] is not None for row in rows):
                raise AdmissionDriverError("completed one-shot posting was rescheduled")
            return rows
        await asyncio.sleep(0.1)
    raise AdmissionDriverError("terminal persistence convergence timed out")


async def _redis_evidence(redis: Redis, candidate: bool, ids: list[str]) -> dict[str, Any]:
    await asyncio.sleep(2.1)
    keys = sorted(str(value) for value in await redis.keys("*"))
    if candidate:
        tag = f"lightpanda-b0:{{{os.environ['LIGHTPANDA_B0_QUEUE_NAMESPACE']}}}"
        route = await redis.hgetall(f"{tag}:route")
        records = await redis.hgetall(f"{tag}:records")
        terminal = sorted(str(value) for value in await redis.zrange(f"{tag}:terminal", 0, -1))
        parsed = [json.loads(value) for value in records.values()]
        return {
            "keys": keys,
            "claim_sequence": int(route.get("claim_sequence", "-1")),
            "record_ids": sorted(records),
            "terminal_ids": terminal,
            "states": sorted(item.get("state") for item in parsed),
            "failures": sum(int(item.get("failures", -1)) for item in parsed),
            "exact": int(route.get("claim_sequence", "-1")) == len(ids)
            and sorted(records) == sorted(ids)
            and terminal == sorted(ids)
            and all(item.get("state") == "terminal" for item in parsed),
        }
    forbidden = [
        key
        for key in keys
        if key.startswith(
            ("ready:", "scrapes_", "ft_scrapes_", "inflight:", "deadletter:", "scrape:")
        )
    ]
    return {"keys": keys, "forbidden": forbidden, "exact": not forbidden}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    tasks = load_tasks(args.workload, args.concurrency)
    expected = 4 * args.concurrency
    if len(tasks) != expected:
        raise AdmissionDriverError("task count differs from mode")
    pool = await asyncpg.create_pool(os.environ["LOCAL_DATABASE_URL"], min_size=1, max_size=2)
    redis = Redis.from_url(os.environ["REDIS_URL"], decode_responses=True, protocol=2)
    try:
        if args.phase == "reserve":
            # These lookup tables are supplied by sync in production, outside the
            # crawler's local migrations. The disposable fixture starts empty.
            for statement in _LOOKUP_TABLES:
                await pool.execute(statement)
            value = await pool.fetchval(
                "SELECT nextval('public.lightpanda_b0_routing_epoch_seq'::regclass)"
            )
            if value != 2:
                raise AdmissionDriverError("disposable routing epoch is not the first reservation")
            return {"phase": "reserve", "routing_epoch": value}
        if args.phase == "seed":
            await _seed(pool, redis, tasks, args.lane == "candidate")
            redis_time = await redis.time()
            redis_now = float(redis_time[0]) + float(redis_time[1]) / 1_000_000
            pg_now = await pool.fetchval("SELECT extract(epoch FROM clock_timestamp())")
            if abs(float(pg_now) - redis_now) > 1:
                raise AdmissionDriverError("Redis/Postgres clocks differ by more than one second")
            due = max(redis_now, float(pg_now)) + 45.0
            ids = await _feed(redis, tasks, False, due)
            return {"phase": "seed", "due": due, "feed": len(ids)}
        if args.phase == "transfer":
            if args.lane != "candidate" or args.due <= time.time():
                raise AdmissionDriverError("Go transfer requires a future candidate due time")
            ids = await _feed(redis, tasks, True, args.due)
            return {"phase": "transfer", "activated": len(ids)}
        if args.phase != "collect" or args.due <= 0:
            raise AdmissionDriverError("collector requires the exact seed due time")
        due = args.due
        ids = [row["id"] for row in tasks]
        until_due = due - time.time()
        if until_due <= 0:
            raise AdmissionDriverError("feed crossed the common due time")
        started = time.monotonic_ns() + int(until_due * 1_000_000_000)
        rows = await _wait(pool, ids, args.timeout)
        finished = time.monotonic_ns()
        stable = [
            dict((key, row[key]) for key in row if key not in {"last_scraped_at", "next_scrape_at"})
            for row in rows
        ]
        descriptions = await pool.fetch(
            "SELECT posting_id::text, locale, html, hash FROM descriptions "
            "ORDER BY posting_id, locale"
        )
        descriptions_by_posting: dict[str, list[dict[str, Any]]] = {}
        for description in descriptions:
            descriptions_by_posting.setdefault(str(description["posting_id"]), []).append(
                dict(description)
            )
        task_sha = {
            str(row["id"]): hashlib.sha256(
                _canonical(
                    {
                        "posting": row,
                        "descriptions": descriptions_by_posting.get(str(row["id"]), []),
                    }
                )
            ).hexdigest()
            for row in stable
        }
        persisted = len({str(row["id"]) for row in rows if row["last_scraped_at"]})
        latencies = sorted(
            max(0.0, (row["last_scraped_at"].timestamp() - due) * 1000) for row in rows
        )
        p99 = latencies[max(0, (99 * len(latencies) + 99) // 100 - 1)]
        evidence = await _redis_evidence(redis, args.lane == "candidate", ids)
        return {
            "schema_version": 1,
            "lane": args.lane,
            "mode": "synthetic",
            "feed": len(ids),
            "persisted": persisted,
            "terminal": len(ids) if evidence["exact"] else 0,
            "writes": len(descriptions),
            "elapsed_ns": finished - started,
            "p99_ms": p99,
            "canonical_sha256": hashlib.sha256(
                _canonical(
                    {
                        "postings": stable,
                        "descriptions": [dict(row) for row in descriptions],
                    }
                )
            ).hexdigest(),
            "per_task_sha256": task_sha,
            "redis": evidence,
        }
    finally:
        await redis.aclose()
        await pool.close()
        await close_redis()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lane", choices=("candidate", "control"), required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--concurrency", type=int, choices=(1, 4), required=True)
    parser.add_argument(
        "--phase", choices=("reserve", "seed", "transfer", "collect"), required=True
    )
    parser.add_argument("--due", type=float, default=0)
    args = parser.parse_args()
    try:
        evidence = asyncio.run(run(args))
    except (AdmissionDriverError, OSError, ValueError) as exc:
        print("ADMISSION_ARM_ERROR=" + json.dumps(str(exc)))
        return 2
    marker = "ADMISSION_ARM=" if args.phase == "collect" else "ADMISSION_SETUP="
    print(marker + json.dumps(evidence, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
