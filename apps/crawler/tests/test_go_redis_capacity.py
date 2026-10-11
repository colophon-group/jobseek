from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src import redis_capacity

ROOT = Path(__file__).resolve().parents[1]


def test_native_capacity_retains_original_policies_and_atomic_reachability():
    native = ROOT / "go/typesense-exporter"
    assert json.loads((native / "redis_capacity_policy.json").read_text()) == [
        asdict(policy) for policy in redis_capacity.FAMILY_POLICIES
    ]
    assert (native / "schedule_rebuild.sql").read_text() == redis_capacity._REBUILD_QUERY
    assert (native / "redis_capacity_prune.lua").read_bytes() == (
        ROOT / "src/lua/prune_orphan_scrape.lua"
    ).read_bytes()


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["summary"], ["summary", "--format=json"]),
        (
            ["summary", "--format=prometheus"],
            ["summary", "--format=prometheus"],
        ),
        (["inspect"], ["inspect", "--format=json"]),
        (["rebuild"], ["rebuild", "--limit=10000"]),
        (
            [
                "rebuild",
                "--limit=100",
                "--after-id=00000000-0000-0000-0000-000000000001",
                "--apply",
            ],
            [
                "rebuild",
                "--limit=100",
                "--after-id=00000000-0000-0000-0000-000000000001",
                "--apply",
            ],
        ),
        (
            ["prune"],
            ["prune", "--cursor=0", "--max-scanned=50000", "--max-delete=50000"],
        ),
        (
            ["prune", "--cursor=42", "--max-scanned=100", "--max-delete=1", "--apply"],
            ["prune", "--cursor=42", "--max-scanned=100", "--max-delete=1", "--apply"],
        ),
    ],
)
async def test_capacity_native_command_precedes_python_pools(monkeypatch, arguments, expected):
    from src import cli

    monkeypatch.setattr(sys, "argv", ["crawler", "redis-capacity", *arguments])
    pool = AsyncMock(side_effect=AssertionError("Python pool must not open"))
    monkeypatch.setattr(cli, "create_local_pool", pool)

    def execute(binary, command):
        assert binary == "go-typesense-exporter"
        assert command == [binary, "--redis-capacity", *expected]
        raise FileNotFoundError("missing installed Go executable")

    monkeypatch.setattr(cli.os, "execvp", execute)
    with pytest.raises(FileNotFoundError, match="missing installed Go"):
        await cli.run()
    pool.assert_not_awaited()
