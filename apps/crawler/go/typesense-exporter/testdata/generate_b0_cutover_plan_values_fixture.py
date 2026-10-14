"""Offline plan value corpus evaluated by the retained B0 activation helpers."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.lightpanda import activation


async def generate() -> None:
    timestamps = []
    for value in (
        "1970-01-01T00:00:00+00:00",
        "1970-01-01T00:00:00.000001+00:00",
        "1969-12-31T23:59:59.600000+00:00",
        "2026-10-14T12:30:00.123456+02:00",
        "0001-01-01T00:00:00+00:00",
        "9999-12-31T23:59:59.999999+00:00",
    ):
        time = datetime.fromisoformat(value)
        timestamps.append(
            {
                "timestamp": value,
                "seconds": activation._seconds(time),
                "first_time_ready": activation._go_ready_at(time, first_time=True),
            }
        )
    valid = "pilot|lightpanda-b0|7|board|jobs.example|recurring_browser|123.0001"
    candidates = [valid]
    for kind in ("ft_browser", "ft_simple", "recurring_simple"):
        candidates.append(valid.replace("recurring_browser", kind))
    for score in (
        "0",
        "9999999999.999",
        "9999999999.9991",
        "01",
        "1e3",
        "-1",
        "NaN",
        "0.00000000000000000001",
        "42.0000",
    ):
        candidates.append(valid.rsplit("|", 1)[0] + "|" + score)
    for before, after in (
        ("pilot", "other"),
        ("|7|", "|07|"),
        ("|board|", "|other|"),
        ("|jobs.example|", "|other.example|"),
        ("recurring_browser", "foreign"),
    ):
        candidates.append(valid.replace(before, after))
    candidates.extend(["", valid + "|extra", valid.rsplit("|", 1)[0]])
    guard_vectors = []
    stored = SimpleNamespace(
        task=SimpleNamespace(
            route=SimpleNamespace(shard_id="lightpanda-b0", routing_epoch=7),
            board_id="board",
            domain="jobs.example",
        )
    )
    for raw in candidates:

        class Redis:
            async def hget(self, key: str, task_id: str, raw: str = raw) -> str:
                assert key == "lightpanda-b0:legacy-guard" and task_id == "posting"
                return raw

        vector = {"raw": raw}
        with patch.object(
            activation,
            "settings",
            SimpleNamespace(
                lightpanda_b0_queue_namespace="pilot",
            ),
        ):
            try:
                kind, score = await activation._bound_legacy_guard(Redis(), "posting", stored)
            except activation.ActivationError:
                vector["accepted"] = False
            else:
                vector.update(accepted=True, kind=kind, score=score)
        guard_vectors.append(vector)
    document = {
        "tasks": [{"source_url": "https://jobs.example/ü", "ready": "123.0001"}],
        "routing_epoch": 7,
        "empty": [],
        "metadata": {"cjk": "東京"},
    }
    canonical, digest = activation._canonical(document)
    Path(__file__).with_name("python_b0_cutover_plan_values.json").write_text(
        json.dumps(
            {
                "schema": "jobseek.b0-cutover-plan-values-original/v1",
                "timestamps": timestamps,
                "guards": guard_vectors,
                "canonical": {"document": document, "bytes": canonical, "sha256": digest},
            },
            ensure_ascii=True,
            indent=2,
        )
        + "\n"
    )


asyncio.run(generate())
