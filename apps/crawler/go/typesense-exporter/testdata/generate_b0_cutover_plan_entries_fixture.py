"""Offline rollback entries from the actual retained full planning function."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.lightpanda import activation

IDENTITY = {"Namespace": "pilot", "Shard": "lightpanda-b0", "Epoch": 7}
ROW = {
    "posting_id": "posting",
    "board_id": "board",
    "source_url": "https://jobs.example/posting",
    "description_r2_hash": 0,
    "is_active": True,
    "next_scrape_at": datetime(2026, 10, 14, 12, 30, 0, 123456, UTC),
    "lease_active": False,
    "is_enabled": True,
    "board_status": "active",
    "scraper_needs_browser": True,
    "scrape_interval_hours": 24,
}
STORED = {
    "BoardID": "board",
    "SourceURL": "https://jobs.example/posting",
    "Domain": "jobs.example",
    "State": "ready",
    "LegacyGuard": "pilot|lightpanda-b0|7|board|jobs.example|recurring_browser|42",
}


async def generate() -> None:
    cases = []
    for state in ("ready", "terminal", "dead"):
        for hash_value in (None, 0, -(2**63), 2**63 - 1):
            cases.append(
                (
                    f"{state}-hash-{hash_value}",
                    {**ROW, "description_r2_hash": hash_value},
                    {**STORED, "State": state},
                )
            )
    for kind in ("ft_browser", "ft_simple", "recurring_simple"):
        cases.append(
            (
                kind,
                ROW,
                {
                    **STORED,
                    "LegacyGuard": STORED["LegacyGuard"].replace(
                        "recurring_browser",
                        kind,
                    ),
                },
            )
        )
    for field, value in (
        ("is_active", False),
        ("is_enabled", False),
        ("board_status", "suspect"),
        ("next_scrape_at", None),
        ("lease_active", True),
        ("scrape_interval_hours", 0),
        ("scrape_interval_hours", 8761),
        ("board_id", "changed-board"),
        ("source_url", "https://jobs.example/changed"),
        ("scraper_needs_browser", False),
    ):
        cases.append((f"changed-{field}-{value}", {**ROW, field: value}, STORED))
    cases.append(("deleted-posting", None, STORED))
    cases.append(("invalid-guard", ROW, {**STORED, "LegacyGuard": "foreign"}))
    vectors = []
    for name, row, stored in cases:
        row, stored = copy.deepcopy(row), copy.deepcopy(stored)
        task = SimpleNamespace(
            board_id=stored["BoardID"],
            source_url=stored["SourceURL"],
            domain=stored["Domain"],
            route=SimpleNamespace(
                shard_id="lightpanda-b0",
                routing_epoch=7,
            ),
        )
        records = {"posting": SimpleNamespace(task=task, state=stored["State"])}

        class Pool:
            async def fetch(self, query: str, *args: object, row: dict | None = row) -> list:
                assert args
                return [] if row is None or query == activation._GO_FENCE_IDS_SQL else [row]

        class Redis:
            async def type(self, key: str) -> str:
                assert key.endswith(":route")
                return "hash"

            async def hget(self, key: str, task_id: str, stored: dict = stored) -> str:
                assert key == "lightpanda-b0:legacy-guard" and task_id == "posting"
                return stored["LegacyGuard"]

        async def owner(_redis: object) -> str:
            return activation._PRODUCER_OWNER_SCHEMA

        async def existing(*args: object, records: dict = records, **kwargs: object) -> dict:
            del args, kwargs
            return records

        async def suffix(*args: object) -> None:
            del args

        native_row = (
            None
            if row is None
            else {
                "ID": row["posting_id"],
                "BoardID": row["board_id"],
                "SourceURL": row["source_url"],
                "Domain": "jobs.example",
                "BoardStatus": row["board_status"],
                "Enabled": row["is_enabled"],
                "Active": row["is_active"],
                "Browser": row["scraper_needs_browser"],
                "LeaseActive": row["lease_active"],
                "NextScrapeAt": row["next_scrape_at"].isoformat()
                if row["next_scrape_at"]
                else None,
                "Hash": row["description_r2_hash"],
                "Interval": row["scrape_interval_hours"],
            }
        )
        vector = {"name": name, "row": native_row, "stored": stored}
        settings = SimpleNamespace(
            lightpanda_b0_producer_mode="off",
            lightpanda_b0_queue_namespace="pilot",
            lightpanda_b0_shard_id="lightpanda-b0",
            lightpanda_b0_routing_epoch=7,
        )
        with (
            patch.object(activation, "settings", settings),
            patch.object(activation, "_producer_marker_phase", lambda _: "active"),
            patch.object(activation, "_producer_owner_schema", owner),
            patch.object(activation, "_existing_records", existing),
            patch.object(activation, "_prove_no_suffix_authority", suffix),
        ):
            try:
                plan = await activation.build_rollback_plan(
                    Pool(),
                    Redis(),
                    cohort="cdom",
                    receipt_state="active",
                    source_receipt_sha256="a" * 64,
                )
            except activation.ActivationError:
                vector["accepted"] = False
            else:
                vector.update(
                    accepted=True, task=plan.tasks[0], entry=plan.document["redis_plan"]["posting"]
                )
        vectors.append(vector)
    Path(__file__).with_name("python_b0_cutover_plan_entries.json").write_text(
        json.dumps(
            {
                "schema": "jobseek.b0-cutover-plan-entries-original/v1",
                "identity": IDENTITY,
                "vectors": vectors,
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n"
    )


asyncio.run(generate())
