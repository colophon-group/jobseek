"""Capture disappearance decisions from the actual Python board processor."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import structlog

from src.processing import board
from src.processing.gone_policy import evaluate_gone_confirmation

IDENTITIES = {"https://job-boards.greenhouse.io/fixture/jobs/1"}
FINGERPRINT = board._inventory_fingerprint(IDENTITIES)


async def capture(
    name,
    *,
    history=(),
    active=2,
    missing=1,
    complete=True,
    discovered=1,
    candidate=None,
    config="fixture",
    streak=0,
    blast_floor=None,
):
    metadata = {
        "recent_discovered_counts": list(history),
        "suspect_streak": streak,
        "_monitor_config_fingerprint": config,
        "_confirmed_drop_candidate": candidate,
    }
    if blast_floor is not None:
        metadata["blast_radius_floor"] = blast_floor
    conn = AsyncMock()
    conn.fetchrow.return_value = {"active": active, "missing": missing}
    conn.fetch.return_value = [object()] * missing
    gone, skipped = await board._mark_gone_with_guards(
        conn,
        "00000000-0000-4000-8000-000000000001",
        discovered,
        datetime(2026, 10, 1, tzinfo=UTC),
        copy.deepcopy(metadata),
        1,
        structlog.get_logger(),
        inventory_fingerprint=FINGERPRINT if complete else None,
        complete_inventory=complete,
    )
    result = copy.deepcopy(metadata)
    for call in conn.execute.call_args_list:
        assert call.args[0] == board._UPDATE_METADATA
        result.update(json.loads(call.args[2]))
    return {
        "name": name,
        "metadata": metadata,
        "active": active,
        "missing": missing,
        "complete": complete,
        "discovered": discovered,
        "identities": sorted(IDENTITIES),
        "gone": gone,
        "skipped": skipped or "",
        "counted": conn.fetchrow.await_count,
        "delisted": conn.fetch.await_count,
        "result_metadata": result,
    }


async def main():
    prior = {
        "inventory_fingerprint": FINGERPRINT,
        "config_fingerprint": "fixture",
        "discovered": 1,
        "confirmations": 2,
    }
    cases = [
        await capture("fresh-half-missing"),
        await capture("fresh-over-half-missing", active=3, missing=2),
        await capture("no-active", active=0, missing=0),
        await capture("two-history-no-drop", history=[10, 10]),
        await capture("median-drop", history=[10, 10, 10]),
        await capture("drop-boundary", history=[10, 10, 10], discovered=7),
        await capture("even-median", history=[2, 10, 20, 30], discovered=10),
        await capture("rolling-five", history=[1, 1, 1, 1, 1, 1]),
        await capture("zero-median", history=[0, 0, 0]),
        await capture(
            "filtered-clears-candidate", history=[10] * 3, complete=False, candidate=prior, streak=2
        ),
        await capture("no-config-clears-candidate", history=[10] * 3, config=None, candidate=prior),
        await capture("third-exact-drop", history=[10] * 3, candidate=prior),
        await capture("third-exact-blast", active=3, missing=2, candidate=prior),
        await capture(
            "changed-inventory",
            history=[10] * 3,
            candidate={**prior, "inventory_fingerprint": "other"},
        ),
        await capture(
            "changed-config", history=[10] * 3, candidate={**prior, "config_fingerprint": "other"}
        ),
        await capture("changed-count", history=[10] * 3, candidate={**prior, "discovered": 2}),
        await capture(
            "string-count-does-not-match", history=[10] * 3, candidate={**prior, "discovered": "1"}
        ),
        await capture(
            "malformed-confirmations",
            history=[10] * 3,
            candidate={**prior, "confirmations": {"bad": True}},
        ),
        await capture(
            "max-missing-accepted", history=[10] * 3, active=6000, missing=5000, candidate=prior
        ),
        await capture(
            "max-missing-refused", history=[10] * 3, active=6000, missing=5001, candidate=prior
        ),
        await capture("passing-clears-candidate", candidate=prior, streak=2),
        await capture("configured-nine-tenths-passes", active=10, missing=8, blast_floor=0.9),
        await capture("configured-nine-tenths-boundary", active=10, missing=9, blast_floor=0.9),
        await capture("configured-nine-tenths-blocks", active=10, missing=10, blast_floor=0.9),
        await capture("configured-zero-survives", active=10, missing=1, blast_floor=0.0),
    ]
    target = Path(__file__).with_name("python_lifecycle.json")
    target.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")

    now = datetime(2026, 10, 1, tzinfo=UTC)
    gone_cases = []

    def gone(name, **changes):
        state = {
            "board_status": "active",
            "confirmation_count": 0,
            "first_confirmed_at": None,
            "last_confirmed_at": None,
            "last_success_at": None,
            "gone_at": None,
        }
        state.update(changes)
        result = evaluate_gone_confirmation(**state, now=now)
        gone_cases.append({"name": name, "now": now, "state": state, "result": asdict(result)})

    gone("first-no-history")
    gone("negative-count", confirmation_count=-1)
    gone("recent-healthy", last_success_at=now - timedelta(days=7))
    gone("older-healthy", last_success_at=now - timedelta(days=7, microseconds=1))
    gone(
        "recovery-resets-episode",
        first_confirmed_at=now - timedelta(days=30),
        last_confirmed_at=now - timedelta(hours=1),
        gone_at=now - timedelta(days=20),
    )
    for seconds in (0, 6 * 3600 - 1, 6 * 3600, 6 * 3600 + 1):
        gone(
            f"pending-spacing-{seconds}",
            board_status="gone_pending",
            confirmation_count=1,
            first_confirmed_at=now - timedelta(days=1),
            last_confirmed_at=now - timedelta(seconds=seconds),
        )
        gone(
            f"healthy-third-{seconds}",
            board_status="gone_pending",
            confirmation_count=2,
            last_success_at=now - timedelta(days=1),
            last_confirmed_at=now - timedelta(seconds=seconds),
        )
    for seconds in (0, 24 * 3600 - 1, 24 * 3600, 24 * 3600 + 1):
        gone(
            f"gone-recovery-{seconds}",
            board_status="gone",
            confirmation_count=3,
            first_confirmed_at=now - timedelta(days=20),
            gone_at=now - timedelta(days=10),
            last_confirmed_at=now - timedelta(seconds=seconds),
        )
    gone("gone-no-timestamps", board_status="gone", confirmation_count=2)
    gone("future-confirmation", confirmation_count=1, last_confirmed_at=now + timedelta(hours=1))
    gone("quarantined-starts-confirmation", board_status="quarantined")
    Path(__file__).with_name("python_gone.json").write_text(
        json.dumps(gone_cases, indent=2, default=lambda value: value.isoformat()) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
