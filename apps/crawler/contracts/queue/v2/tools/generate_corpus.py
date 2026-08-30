#!/usr/bin/env python3
"""Generate the deterministic synthetic queue-v2 conformance corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

V2_ROOT = Path(__file__).resolve().parents[1]
CRAWLER_ROOT = Path(__file__).resolve().parents[4]
if str(CRAWLER_ROOT) not in sys.path:
    sys.path.insert(0, str(CRAWLER_ROOT))

from contracts.queue.v2 import model  # noqa: E402

FIXTURES = V2_ROOT / "fixtures"
CORPUS_PATH = FIXTURES / "scenarios.json"
DIGEST_PATH = FIXTURES / "scenarios.sha256"


def route(*, epoch: int = 7, owner: str = "python", shard: str = "shard-03") -> dict[str, Any]:
    return {"engine_owner": owner, "routing_epoch": epoch, "shard_id": shard}


def record(
    task_id: str,
    revision: int,
    *,
    active_route: dict[str, Any] | None = None,
    state: str = "ready",
    token: str | None = None,
    lease_until: int | None = None,
    failures: int = 0,
) -> dict[str, Any]:
    selected = active_route or route()
    return {
        "claim_token": token,
        "config_revision": revision,
        "engine_owner": selected["engine_owner"],
        "failures": failures,
        "lease_until": lease_until,
        "routing_epoch": selected["routing_epoch"],
        "shard_id": selected["shard_id"],
        "state": state,
        "task_id": task_id,
    }


def snapshot(
    revisions: dict[str, int],
    records: list[dict[str, Any]],
    *,
    active_route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issued_tokens = sorted(
        {
            item["claim_token"]
            for item in records
            if item["state"] == "inflight" and item["claim_token"] is not None
        }
    )
    return {
        "configs": revisions,
        "issued_tokens": issued_tokens,
        "records": records,
        "route": active_route or route(),
    }


def fence(
    token: str,
    revision: int,
    *,
    active_route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = active_route or route()
    return {
        "claim_token": token,
        "config_revision": revision,
        "engine_owner": selected["engine_owner"],
        "routing_epoch": selected["routing_epoch"],
        "shard_id": selected["shard_id"],
    }


def op(kind: str, task_id: str, claim_fence: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"fence": claim_fence, "kind": kind, "task_id": task_id, **extra}


def source_cases() -> list[dict[str, Any]]:
    active = route()
    stale_epoch = route(epoch=6)
    stale_owner = route(owner="go")
    go_route = route(epoch=11, owner="go", shard="shard-08")
    unregistered_token = snapshot(
        {"unregistered-token": 1},
        [
            record(
                "unregistered-token",
                1,
                state="inflight",
                token="missing-from-ledger",
                lease_until=20,
            )
        ],
    )
    unregistered_token["issued_tokens"] = []
    duplicated_ledger = snapshot({"ledger-task": 1}, [record("ledger-task", 1)])
    duplicated_ledger["issued_tokens"] = ["issued-twice", "issued-twice"]

    cases: list[dict[str, Any]] = [
        {
            "id": "happy_complete_with_authoritative_write",
            "initial": snapshot({"monitor-a": 4}, [record("monitor-a", 4)]),
            "operations": [
                op("claim", "monitor-a", fence("claim-a", 4), lease_until=20),
                op("heartbeat", "monitor-a", fence("claim-a", 4), lease_until=30),
                op("authorize_write", "monitor-a", fence("claim-a", 4)),
                op("complete", "monitor-a", fence("claim-a", 4)),
            ],
        },
        {
            "id": "reschedule_rotates_claim_token",
            "initial": snapshot({"scrape-a": 9}, [record("scrape-a", 9)]),
            "operations": [
                op("claim", "scrape-a", fence("claim-old", 9), lease_until=20),
                op("reschedule", "scrape-a", fence("claim-old", 9)),
                op("claim", "scrape-a", fence("claim-old", 9), lease_until=40),
                op("claim", "scrape-a", fence("claim-new", 9), lease_until=40),
                op("authorize_write", "scrape-a", fence("claim-new", 9)),
                op("complete", "scrape-a", fence("claim-new", 9)),
            ],
        },
        {
            "id": "reap_reclaim_fences_every_stale_transition",
            "initial": snapshot({"monitor-b": 5}, [record("monitor-b", 5)]),
            "operations": [
                op("claim", "monitor-b", fence("claim-expired", 5), lease_until=10),
                op(
                    "reap",
                    "monitor-b",
                    fence("claim-expired", 5),
                    now=10,
                    max_failures=3,
                ),
                op("claim", "monitor-b", fence("claim-current", 5), lease_until=40),
                op("heartbeat", "monitor-b", fence("claim-expired", 5), lease_until=50),
                op("authorize_write", "monitor-b", fence("claim-expired", 5)),
                op("complete", "monitor-b", fence("claim-expired", 5)),
                op("reschedule", "monitor-b", fence("claim-expired", 5)),
                op("fail", "monitor-b", fence("claim-expired", 5), max_failures=2),
                op(
                    "reap",
                    "monitor-b",
                    fence("claim-expired", 5),
                    now=50,
                    max_failures=2,
                ),
                op("authorize_write", "monitor-b", fence("claim-current", 5)),
                op("complete", "monitor-b", fence("claim-current", 5)),
            ],
        },
        {
            "id": "routing_epoch_and_owner_fence_claim",
            "initial": snapshot(
                {"monitor-c": 2},
                [record("monitor-c", 2, active_route=go_route)],
                active_route=go_route,
            ),
            "operations": [
                op(
                    "claim",
                    "monitor-c",
                    fence(
                        "stale-epoch",
                        2,
                        active_route=route(epoch=10, owner="go", shard="shard-08"),
                    ),
                    lease_until=20,
                ),
                op(
                    "claim",
                    "monitor-c",
                    fence(
                        "wrong-owner",
                        2,
                        active_route=route(epoch=11, owner="python", shard="shard-08"),
                    ),
                    lease_until=20,
                ),
                op(
                    "claim",
                    "monitor-c",
                    fence("current", 2, active_route=go_route),
                    lease_until=20,
                ),
                op("complete", "monitor-c", fence("current", 2, active_route=go_route)),
            ],
        },
        {
            "id": "config_revision_fences_claim",
            "initial": snapshot({"scrape-b": 8}, [record("scrape-b", 8)]),
            "operations": [
                op("claim", "scrape-b", fence("old-config", 7), lease_until=20),
                op("claim", "scrape-b", fence("current-config", 8), lease_until=20),
                op("complete", "scrape-b", fence("current-config", 8)),
            ],
        },
        {
            "id": "stale_failure_cannot_consume_budget",
            "initial": snapshot({"scrape-c": 3}, [record("scrape-c", 3)]),
            "operations": [
                op("claim", "scrape-c", fence("failure-1", 3), lease_until=20),
                op("fail", "scrape-c", fence("failure-1", 3), max_failures=2),
                op("claim", "scrape-c", fence("failure-2", 3), lease_until=30),
                op("fail", "scrape-c", fence("failure-1", 3), max_failures=2),
                op("fail", "scrape-c", fence("failure-2", 3), max_failures=2),
            ],
        },
        {
            "id": "reap_requires_expiry_and_current_fence",
            "initial": snapshot({"monitor-d": 6}, [record("monitor-d", 6)]),
            "operations": [
                op("claim", "monitor-d", fence("lease-a", 6), lease_until=100),
                op("reap", "monitor-d", fence("lease-a", 6), now=99, max_failures=3),
                op("heartbeat", "monitor-d", fence("lease-a", 6), lease_until=120),
                op("reap", "monitor-d", fence("lease-a", 6), now=120, max_failures=3),
            ],
        },
        {
            "id": "claim_token_is_globally_unique_in_snapshot",
            "initial": snapshot(
                {"monitor-e": 1, "monitor-f": 1},
                [record("monitor-e", 1), record("monitor-f", 1)],
            ),
            "operations": [
                op("claim", "monitor-e", fence("shared-token", 1), lease_until=20),
                op("claim", "monitor-f", fence("shared-token", 1), lease_until=20),
                op("claim", "monitor-f", fence("unique-token", 1), lease_until=20),
            ],
        },
        {
            "id": "conservation_detects_loss",
            "initial": snapshot({"lost-task": 1}, []),
            "operations": [],
        },
        {
            "id": "conservation_detects_duplication",
            "initial": snapshot(
                {"duplicate-task": 1},
                [
                    record("duplicate-task", 1),
                    record(
                        "duplicate-task",
                        1,
                        state="terminal",
                    ),
                ],
            ),
            "operations": [],
        },
        {
            "id": "conservation_detects_orphan_config",
            "initial": snapshot({}, [record("orphan-task", 1)]),
            "operations": [],
        },
        {
            "id": "conservation_detects_route_mismatches",
            "initial": snapshot(
                {"wrong-shard": 1, "wrong-epoch": 1, "wrong-owner": 1},
                [
                    record("wrong-shard", 1, active_route=route(shard="shard-99")),
                    record("wrong-epoch", 1, active_route=stale_epoch),
                    record("wrong-owner", 1, active_route=stale_owner),
                ],
                active_route=active,
            ),
            "operations": [],
        },
        {
            "id": "conservation_detects_config_revision_mismatch",
            "initial": snapshot({"stale-config": 4}, [record("stale-config", 3)]),
            "operations": [],
        },
        {
            "id": "conservation_detects_token_collision",
            "initial": snapshot(
                {"claim-a": 1, "claim-b": 1},
                [
                    record("claim-a", 1, state="inflight", token="collision", lease_until=20),
                    record("claim-b", 1, state="inflight", token="collision", lease_until=30),
                ],
            ),
            "operations": [],
        },
        {
            "id": "conservation_detects_invalid_partition_shape",
            "initial": snapshot(
                {"invalid-inflight": 1, "invalid-ready": 1},
                [
                    record("invalid-inflight", 1, state="inflight"),
                    record("invalid-ready", 1, token="leftover", lease_until=20),
                ],
            ),
            "operations": [],
        },
        {
            "id": "conservation_detects_unregistered_inflight_token",
            "initial": unregistered_token,
            "operations": [],
        },
        {
            "id": "conservation_detects_issued_token_ledger_duplication",
            "initial": duplicated_ledger,
            "operations": [],
        },
    ]
    return cases


def build_corpus() -> dict[str, Any]:
    completed: list[dict[str, Any]] = []
    for case in source_cases():
        expected = model.run_case(case)
        completed.append({**case, "expected": expected, "result_digest": model.digest(expected)})
    return {"cases": completed, "format": model.FORMAT}


def rendered_files() -> tuple[bytes, bytes]:
    corpus_bytes = (
        json.dumps(build_corpus(), ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    digest_bytes = (hashlib.sha256(corpus_bytes).hexdigest() + "  scenarios.json\n").encode()
    return corpus_bytes, digest_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    corpus_bytes, digest_bytes = rendered_files()
    if args.check:
        if not CORPUS_PATH.exists() or CORPUS_PATH.read_bytes() != corpus_bytes:
            print(f"stale generated corpus: {CORPUS_PATH}", file=sys.stderr)
            return 1
        if not DIGEST_PATH.exists() or DIGEST_PATH.read_bytes() != digest_bytes:
            print(f"stale corpus digest: {DIGEST_PATH}", file=sys.stderr)
            return 1
        return 0
    FIXTURES.mkdir(parents=True, exist_ok=True)
    CORPUS_PATH.write_bytes(corpus_bytes)
    DIGEST_PATH.write_bytes(digest_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
