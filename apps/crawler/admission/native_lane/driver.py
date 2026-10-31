"""Seed and inspect only the disposable, literal-address comparison database."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Sequence
from typing import Any

from admission.native_lane.workload import PROFILES, boards, jobs, workload_sha256

DATABASE = "postgresql://admission:admission@postgres:5432/admission"
REDIS = "redis://redis:6379/0"
COMPANY = uuid.UUID("00000000-0000-4000-8000-00000000ad02")
NAMESPACE = uuid.UUID("d265dd33-163e-4f91-a675-1ca58963bd6e")


def validate_environment() -> None:
    # This module deliberately has no general DSN or production override.
    if os.environ.get("LOCAL_DATABASE_URL") != DATABASE or os.environ.get("REDIS_URL") != REDIS:
        raise RuntimeError("native comparison requires its literal disposable endpoints")


def identity(value: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, value)


def request(path: str, *, storage: bool = False) -> dict[str, Any]:
    base = "https://fixture:8080" if storage else "http://typesense:8108"
    headers = {} if storage else {"X-TYPESENSE-API-KEY": "admission"}
    with urllib.request.urlopen(
        urllib.request.Request(base + path, headers=headers), timeout=5
    ) as r:
        return json.load(r)


async def bootstrap(pool: Any, redis: Any) -> dict[str, Any]:
    from src.lightpanda.admission import _LOOKUP_TABLES, _seed_startup_taxonomy

    if await pool.fetchval("SELECT count(*) FROM company") or await redis.dbsize():
        raise RuntimeError("comparison bootstrap requires empty disposable stores")
    for statement in _LOOKUP_TABLES:
        await pool.execute(statement.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
    await pool.execute("ALTER TABLE occupation ADD COLUMN IF NOT EXISTS parent_id integer")
    await pool.execute("ALTER TABLE technology ADD COLUMN IF NOT EXISTS name text")
    await pool.execute("""
        CREATE TABLE IF NOT EXISTS occupation_name (
            occupation_id integer, locale text, name text, is_display boolean);
        CREATE TABLE IF NOT EXISTS seniority_name (
            seniority_id integer, locale text, name text, is_display boolean);
        CREATE TABLE IF NOT EXISTS location_macro_member (country_id integer, macro_id integer);
    """)
    census = await _seed_startup_taxonomy(pool)
    await pool.execute("""
        UPDATE technology SET name=slug;
        UPDATE currency_rate SET currency='USD',to_eur=0.9 WHERE currency='X01';
        UPDATE location_name SET name='United States'
        WHERE location_id=1 AND locale='en' AND is_display=true;
        INSERT INTO occupation_name SELECT id,'en',slug,true FROM occupation;
        INSERT INTO seniority_name SELECT id,'en',slug,true FROM seniority;
        INSERT INTO exporter_state(key,value) VALUES(
            'last_export_ts:typesense:job_posting',
            '1970-01-01T00:00:00+00:00|00000000-0000-0000-0000-000000000000');
        SELECT setval('public.lightpanda_b0_routing_epoch_seq',2,true);
    """)
    await pool.execute(
        "INSERT INTO company(id,slug,name) VALUES($1,'native-comparison','Native comparison')",
        COMPANY,
    )
    for board in boards():
        board_id = identity(board["slug"])
        await pool.execute(
            """INSERT INTO job_board(id,company_id,board_slug,crawler_type,board_url,metadata,
               monitor_needs_browser,scraper_needs_browser,throttle_key,check_interval_minutes,
               scrape_interval_hours,next_check_at)
               VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,1440,24,now()+interval '1 day')""",
            board_id,
            COMPANY,
            board["slug"],
            board["crawler_type"],
            board["url"],
            json.dumps(board["metadata"]),
            board["monitor_needs_browser"],
            board["scraper_needs_browser"],
            board["domain"],
        )
        await redis.hset(f"board:{board_id}", mapping=cache_config(board))
    for row in jobs():
        if row["profile"] == "jsonld_direct":
            await pool.execute(
                """INSERT INTO job_posting(id,company_id,board_id,source_url,next_scrape_at)
                   VALUES($1,$2,$3,$4,NULL)""",
                identity(row["url"]),
                COMPANY,
                identity(row["profile"]),
                row["url"],
            )
    return {"workload_sha256": workload_sha256(), "startup_taxonomy": census}


def cache_config(board: dict[str, Any]) -> dict[str, str]:
    return {
        "company_id": str(COMPANY),
        "board_slug": board["slug"],
        "board_url": board["url"],
        "crawler_type": board["crawler_type"],
        "metadata": json.dumps(board["metadata"], sort_keys=True),
        "monitor_needs_browser": "1" if board["monitor_needs_browser"] else "0",
        "scraper_needs_browser": "1" if board["scraper_needs_browser"] else "0",
        "check_interval_minutes": "1440",
        "scrape_interval_hours": "24",
        "domain": board["domain"],
        "throttle_key": board["domain"],
    }


async def feed(pool: Any) -> dict[str, Any]:
    from src.redis_queue import MonitorSchedule, ScrapeSchedule, enqueue_monitors, enqueue_scrapes

    due = time.time() + 2
    monitors = []
    for board in boards():
        if not board["monitor_seeded"]:
            continue
        await pool.execute(
            "UPDATE job_board SET next_check_at=to_timestamp($1) WHERE id=$2",
            due,
            identity(board["slug"]),
        )
        monitors.append(
            MonitorSchedule(
                domain=board["domain"],
                board_id=str(identity(board["slug"])),
                next_check_at=due,
                config=cache_config(board),
                browser=board["monitor_needs_browser"],
                first_time=True,
            )
        )
    details = []
    for row in jobs():
        if row["profile"] != "jsonld_direct":
            continue
        posting_id = identity(row["url"])
        await pool.execute(
            "UPDATE job_posting SET next_scrape_at=to_timestamp($1) WHERE id=$2",
            due,
            posting_id,
        )
        details.append(
            ScrapeSchedule(
                domain=row["origin"],
                posting_id=str(posting_id),
                next_scrape_at=due,
                config={
                    "source_url": row["url"],
                    "board_id": str(identity(row["profile"])),
                    "description_r2_hash": "",
                    "scraper_needs_browser": "0",
                    "scrape_interval_hours": "24",
                    "scrape_step": "0",
                },
                first_time=True,
            )
        )
    if await enqueue_monitors(monitors) != [True] * len(monitors):
        raise RuntimeError("comparison did not enqueue every monitor exactly once")
    if await enqueue_scrapes(details) != [True] * len(details):
        raise RuntimeError("comparison did not enqueue every detail exactly once")
    return {"due": due, "monitors": len(monitors), "details": len(details)}


def progress_counts(
    rows: list[Any],
    descriptions: list[Any],
    successful_monitors: int,
    expected: dict[str, Any],
    monitor_states: Sequence[Any] = (),
    upstream_requests: dict[str, int] | None = None,
) -> dict[str, Any]:
    profiles = {
        profile: {"postings": 0, "descriptions": 0, "uploaded": 0, "failed": 0}
        for profile in PROFILES
    }
    for row in rows:
        profile = expected.get(row["source_url"], {}).get("profile")
        if profile in profiles:
            profiles[profile]["postings"] += 1
            profiles[profile]["failed"] += int(bool(row["scrape_failures"]))
    for row in descriptions:
        profile = expected.get(row["source_url"], {}).get("profile")
        if profile in profiles:
            profiles[profile]["descriptions"] += 1
            profiles[profile]["uploaded"] += int(row["r2_uploaded"] is True)
    for board in monitor_states:
        profile = board["board_slug"]
        if profile in profiles:
            profiles[profile].update(
                monitor_checked=int(board["checked"]),
                monitor_succeeded=int(board["succeeded"]),
                monitor_failures=board["consecutive_failures"],
            )
    for board in boards():
        profile = profiles[board["slug"]]
        origin = board["domain"]
        profile["listing_requests"] = (upstream_requests or {}).get(origin + "/listing", 0)
        profile["detail_requests"] = sum(
            count
            for path, count in (upstream_requests or {}).items()
            if path.startswith(origin + "/jobs/")
        )
    return {
        "expected_postings": len(expected),
        "postings": len(rows),
        "titled_postings": sum(bool(row["titles"]) for row in rows),
        "failed_postings": sum(bool(row["scrape_failures"]) for row in rows),
        "reserved_postings": sum(bool(row["tdm_reserved"]) for row in rows),
        "description_rows": len(descriptions),
        "uploaded_description_rows": sum(row["r2_uploaded"] is True for row in descriptions),
        "successful_monitors": successful_monitors,
        "profiles": profiles,
    }


async def collect(pool: Any, redis: Any, timeout: float, lane: str) -> dict[str, Any]:
    from src.lightpanda.admission import STABLE_COLUMNS

    deadline = time.monotonic() + timeout
    expected = {row["url"]: row for row in jobs()}
    while True:
        rows = await pool.fetch(
            "SELECT "
            + STABLE_COLUMNS
            + ",description_r2_hash,tdm_reserved FROM job_posting ORDER BY source_url"
        )
        descriptions = await pool.fetch("""
            SELECT p.id::text,p.source_url,d.locale,d.html,d.hash::text,d.r2_uploaded
            FROM descriptions d JOIN job_posting p ON p.id=d.posting_id
            ORDER BY p.source_url,d.locale
        """)
        finished_boards = await pool.fetchval(
            "SELECT count(*) FROM job_board "
            "WHERE last_success_at IS NOT NULL AND consecutive_failures=0"
        )
        if (
            len(rows) == len(expected)
            and finished_boards == 3
            and all(row["titles"] and row["scrape_failures"] == 0 for row in rows)
            and len(descriptions) == len(expected)
            and all(row["r2_uploaded"] is True for row in descriptions)
        ):
            break
        if time.monotonic() >= deadline:
            monitor_states = await pool.fetch(
                "SELECT board_slug,last_checked_at IS NOT NULL AS checked,"
                "last_success_at IS NOT NULL AS succeeded,consecutive_failures FROM job_board"
            )
            objects = request("/report", storage=True)
            print(
                "NATIVE_LANE_PROGRESS="
                + json.dumps(
                    progress_counts(
                        rows,
                        descriptions,
                        finished_boards,
                        expected,
                        monitor_states,
                        objects["requests"],
                    )
                ),
                file=sys.stderr,
                flush=True,
            )
            raise RuntimeError("comparison did not finish canonical, monitor and upload work")
        await asyncio.sleep(0.25)
    if {row["source_url"] for row in rows} != set(expected):
        raise RuntimeError("comparison posting identities differ from input")
    if any(row["titles"][0] != expected[row["source_url"]]["title"] for row in rows):
        raise RuntimeError("comparison posting titles differ from input")
    if any(
        row["employment_type"] != "full_time"
        or row["salary_min"] != 100000
        or row["salary_max"] != 120000
        or row["salary_currency"] != "USD"
        for row in rows
    ):
        raise RuntimeError("comparison employment or salary values differ from input")
    objects = request("/report", storage=True)
    for row in descriptions:
        key = f"/descriptions/job/{row['id']}/{row['locale']}/latest.html"
        if objects["uploaded_objects"].get(key) != hashlib.sha256(row["html"].encode()).hexdigest():
            raise RuntimeError("comparison uploaded description bytes differ from canonical SQL")
    # Keep terminal SQL and wire results independent of each implementation's counters.
    ids = [row["id"] for row in rows]
    while True:
        exported = {}
        for offset in range(0, len(ids), 100):
            selected = ids[offset : offset + 100]
            parameters = urllib.parse.urlencode(
                {
                    "q": "*",
                    "query_by": "title",
                    "filter_by": "id:=[" + ",".join(selected) + "]",
                    "per_page": len(selected),
                    "include_fields": "id,title,company_id",
                }
            )
            result = request("/collections/job_posting/documents/search?" + parameters)
            exported.update(
                {hit["document"]["id"]: hit["document"] for hit in result.get("hits", [])}
            )
        if len(exported) == len(expected):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("comparison exporter did not publish the complete input")
        await asyncio.sleep(0.25)
    if any(
        exported[row["id"]]["title"] != row["titles"][0]
        or exported[row["id"]]["company_id"] != str(COMPANY)
        for row in rows
    ):
        raise RuntimeError("comparison exported fields differ from canonical SQL")
    if any(row["tdm_reserved"] for row in rows) or objects["unexpected_requests"]:
        raise RuntimeError("comparison publisher policy or request boundary differs")
    canonical_rows = [
        {key: value for key, value in dict(row).items() if key != "id"} for row in rows
    ]
    canonical_descriptions = [
        {key: value for key, value in dict(row).items() if key != "id"} for row in descriptions
    ]
    digest = hashlib.sha256(
        json.dumps(
            {"postings": canonical_rows, "descriptions": canonical_descriptions},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()
    inflight = sum([await redis.zcard("inflight:" + worker) for worker in ("simple", "browser")])
    if inflight:
        raise RuntimeError("comparison still has inflight task representations")
    for board in boards():
        for worker in ("simple", "browser"):
            for prefix in ("scrapes_", "ft_scrapes_"):
                if await redis.zcard(prefix + worker + ":" + board["domain"]):
                    raise RuntimeError("comparison left completed details queued")
        if board["monitor_seeded"]:
            due = await pool.fetchval(
                "SELECT next_check_at FROM job_board WHERE id=$1", identity(board["slug"])
            )
            worker = "browser" if board["monitor_needs_browser"] else "simple"
            score = await redis.zscore(
                "monitors_" + worker + ":" + board["domain"], str(identity(board["slug"]))
            )
            if (
                due is None
                or due.timestamp() <= time.time()
                or score is None
                or abs(score - due.timestamp()) > 0.001
            ):
                raise RuntimeError("comparison lost its canonical future monitor schedule")
    if await pool.fetchval("SELECT count(*) FROM job_posting WHERE next_scrape_at IS NOT NULL"):
        raise RuntimeError("comparison rescheduled a never-rescrape posting")
    fences = await pool.fetchval(
        "SELECT count(*) FROM ordinary_worker_write_fence "
        "WHERE routing_epoch=2 AND state='completed'"
    )
    if lane == "native" and fences < 3 + 3 * 12:
        raise RuntimeError("native comparison lacks completed monitor/detail authority")
    return {
        "workload_sha256": workload_sha256(),
        "canonical_sha256": digest,
        "postings": len(rows),
        "descriptions": len(descriptions),
        "exported": len(exported),
        "completed_monitors": finished_boards,
        "completed_native_fences": fences,
        "inflight": inflight,
        "unexpected_requests": objects["unexpected_requests"],
        "peak_upstream_inflight": objects["peak_upstream_inflight"],
        "request_counts": objects["requests"],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    validate_environment()
    import asyncpg
    from redis.asyncio import Redis

    from src.redis_queue import close_redis

    pool = await asyncpg.create_pool(DATABASE, min_size=1, max_size=2)
    redis = Redis.from_url(REDIS)
    try:
        if args.phase == "health":
            deadline = time.monotonic() + 20
            while True:
                try:
                    if request("/health").get("ok") is True:
                        return {"typesense_healthy": True}
                except (OSError, ValueError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("comparison Typesense did not become healthy")
                await asyncio.sleep(0.25)
        if args.phase == "bootstrap":
            return await bootstrap(pool, redis)
        if args.phase == "feed":
            return await feed(pool)
        return await collect(pool, redis, args.timeout, args.lane)
    finally:
        await pool.close()
        await redis.aclose()
        await close_redis()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase", choices=("health", "bootstrap", "feed", "collect"), required=True
    )
    parser.add_argument("--lane", choices=("native", "legacy"), required=True)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    result = asyncio.run(run(args))
    print("NATIVE_LANE=" + json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
