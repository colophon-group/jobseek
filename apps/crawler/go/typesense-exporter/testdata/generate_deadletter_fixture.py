"""Refresh the offline Python oracle without network or production access."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis

from src.deadletters import _classify_deadletters_python_reference, lifecycle_counts


async def generate():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    entries, boards, configs = [], [], {}
    cases = [
        "active",
        "removed",
        "disabled",
        "disabled_status",
        "missing",
        "stale",
        "route",
        "route_missing",
        "route_stale",
        "worker",
        "gone",
        "suspect",
        "quarantined",
        "nonmonitor",
        "malformed",
        "invalid",
        "uppercase",
        "compact",
        "nulls",
        "empty_domain",
        "browser_true",
        "duplicate_lane",
    ]
    for index, case in enumerate(cases, 1):
        board_id = f"abcdef00-0000-0000-0000-{index:012d}"
        row = dict(
            board_id=board_id,
            board_slug="fixture",
            board_url="https://example.test/careers",
            crawler_type="dom",
            board_status="active",
            is_enabled=True,
            throttle_key="example.test",
            monitor_needs_browser=False,
        )
        config = dict(
            domain="example.test",
            board_url=row["board_url"],
            crawler_type="dom",
            monitor_needs_browser="0",
        )
        domain, wtype, task_id = "example.test", "simple", board_id
        if case == "disabled":
            row["is_enabled"] = False
        if case == "disabled_status":
            row["board_status"] = "disabled"
        if case in {"gone", "suspect", "quarantined"}:
            row["board_status"] = case
        if case.startswith("route"):
            domain = "old.example.test"
        if case in {"missing", "route_missing"}:
            config = {}
        if case in {"stale", "route_stale"}:
            config["crawler_type"] = "old"
        if case == "worker":
            wtype = "browser"
        if case == "uppercase":
            task_id = board_id.upper()
        if case == "compact":
            task_id = board_id.replace("-", "")
        if case == "empty_domain":
            row["throttle_key"] = None
            config["domain"] = ""
        if case == "browser_true":
            wtype = "browser"
            row["monitor_needs_browser"] = True
            config["monitor_needs_browser"] = " YES "
        if case == "nulls":
            row["board_url"] = row["crawler_type"] = row["board_slug"] = row["board_status"] = None
            config.pop("board_url")
            config.pop("crawler_type")
        member = f"monitor|{domain}|{task_id}"
        if case == "nonmonitor":
            member = f"scrape|{domain}|{task_id}"
        if case == "malformed":
            member = "monitor|"
        if case == "invalid":
            member = "monitor|example.test|bad"
        score = 1725000000 + index / 17
        entries.append(dict(wtype=wtype, member=member, reaped_at=score))
        await redis.zadd(f"deadletter:{wtype}", {member: score})
        if case == "duplicate_lane":
            entries.append(dict(wtype="browser", member=member, reaped_at=-0.0000006))
            await redis.zadd("deadletter:browser", {member: -0.0000006})
        if case != "removed":
            boards.append(row)
        configs[board_id] = config
        if config:
            await redis.hset(f"board:{board_id}", mapping=config)
    db = AsyncMock()

    async def fetch(query, ids):
        return [row for row in boards if row["board_id"] in {str(value) for value in ids}]

    db.fetch.side_effect = fetch
    with patch("src.redis_queue.get_redis", return_value=redis):
        result = await _classify_deadletters_python_reference(db)
    await redis.aclose()
    return dict(
        input=dict(entries=entries, boards=boards, configs=configs),
        expected=dict(
            action="inspect",
            dry_run=True,
            total=len(result),
            counts=lifecycle_counts(result),
            selected=len(result),
            entries=[item.to_dict() for item in result],
            outcomes=[],
        ),
    )


if __name__ == "__main__":
    import sys

    path = Path(__file__).with_name("deadletter_fixture.json")
    content = json.dumps(asyncio.run(generate()), ensure_ascii=False, indent=2) + "\n"
    if "--check" in sys.argv:
        assert path.read_text() == content, "Regenerate the Python deadletter oracle"
    else:
        path.write_text(content)
