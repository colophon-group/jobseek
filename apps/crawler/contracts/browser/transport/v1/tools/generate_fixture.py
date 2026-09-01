#!/usr/bin/env python3
"""Generate or verify the exact standalone 86,400-second fixture."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

CRAWLER_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(CRAWLER_ROOT))

from src.shared.browser_transport import (  # noqa: E402
    CAPABILITIES,
    CAPTURE_SCHEMA,
    HISTOGRAM_BUCKETS,
    INSTRUMENTATION_REASONS,
    PRETRANSPORT_REASONS,
    REQUEST_LABELS,
    TERMINAL_OUTCOMES,
    VALID_ROUTE_PROVIDER_PAIRS,
    _request_label_sets,
    load_registry,
    validate_capture_fixture,
)

FIXTURE_PATH = CRAWLER_ROOT / "runtime-cost/fixtures/chromium-transport-86400-v1.json"


def _event_tape() -> list[dict[str, Any]]:
    tasks = [
        ("task-a", "monitor", "navigation-evaluation", "direct", "direct"),
        ("task-b", "detail", "interaction-capture", "proxy", "static-proxy"),
        ("task-c", "monitor", "identity-transport", "direct", "direct"),
        ("task-d", "detail", "navigation-evaluation", "proxy", "static-proxy"),
    ]
    events: list[dict[str, Any]] = []
    for task_id, stage, capability, route, provider in tasks:
        events.append(
            {
                "ordinal": len(events),
                "kind": "accepted_task",
                "task_id": task_id,
                "stage": stage,
                "capability": capability,
                "route": route,
                "provider": provider,
            }
        )
    events.append(
        {
            "ordinal": len(events),
            "kind": "pretransport",
            "task_id": "task-proxy-rejected",
            "stage": "detail",
            "reason": "required_proxy_unavailable",
        }
    )
    requests = [
        # task, class, outcome, bytes, redirect, warmup, main, configured provider
        ("task-a", "main", "complete_response", 3920, False, False, True, "none"),
        ("task-a", "redirect", "complete_response", 0, True, False, True, "none"),
        ("task-a", "subresource", "partial_response", 200, False, False, False, "none"),
        ("task-b", "main", "complete_response", 800, False, False, True, "static-proxy"),
        (
            "task-b",
            "subresource",
            "complete_response",
            250,
            False,
            False,
            False,
            "static-proxy",
        ),
        (
            "task-b",
            "subresource",
            "transport_failure",
            0,
            False,
            False,
            False,
            "static-proxy",
        ),
        ("task-c", "warmup", "complete_response", 100, False, True, True, "none"),
        ("task-c", "subresource", "partial_response", 500, False, False, False, "none"),
        ("task-c", "main", "cancelled", 0, False, False, True, "none"),
        ("task-d", "main", "policy_rejected", 0, False, False, True, "static-proxy"),
        (
            "task-d",
            "subresource",
            "target_closed",
            0,
            False,
            False,
            False,
            "static-proxy",
        ),
    ]
    task_map = {
        task_id: (stage, capability, route, provider)
        for task_id, stage, capability, route, provider in tasks
    }
    for index, request in enumerate(requests, start=1):
        (
            task_id,
            request_class,
            outcome,
            encoded_bytes,
            redirect,
            warmup,
            main,
            configured_provider,
        ) = request
        stage, capability, route, provider = task_map[task_id]
        events.append(
            {
                "ordinal": len(events),
                "kind": "request_terminal",
                "terminal_id": f"request-{index:02d}",
                "task_id": task_id,
                "stage": stage,
                "capability": capability,
                "route": route,
                "provider": provider,
                "request_class": request_class,
                "redirect_predecessor": redirect,
                "explicitly_warmup": warmup,
                "initial_navigation": main,
                "outcome": outcome,
                "encoded_bytes": encoded_bytes,
                "byte_lifecycle_complete": True,
                "proxy_requested": task_id == "task-a" or route == "proxy",
                "configured_proxy_provider": configured_provider,
            }
        )
    return events


def _empty_row(labels: dict[str, str]) -> dict[str, Any]:
    return {
        "labels": labels,
        "attempts": 0,
        "outcomes": {outcome: 0 for outcome in TERMINAL_OUTCOMES},
        "transferred_bytes": 0,
        "histogram": {
            "buckets": {str(bucket): 0 for bucket in HISTOGRAM_BUCKETS},
            "sum": 0,
            "count": 0,
        },
    }


def _support_boundaries() -> tuple[
    dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]
]:
    start = {"accepted_tasks": [], "pretransport": [], "instrumentation": []}
    end = {"accepted_tasks": [], "pretransport": [], "instrumentation": []}
    task_counts = Counter(
        {
            ("monitor", "direct", "direct", "navigation-evaluation"): 1,
            ("detail", "proxy", "static-proxy", "interaction-capture"): 1,
            ("monitor", "direct", "direct", "identity-transport"): 1,
            ("detail", "proxy", "static-proxy", "navigation-evaluation"): 1,
        }
    )
    for stage in ("monitor", "detail"):
        for route, provider in VALID_ROUTE_PROVIDER_PAIRS:
            for capability in CAPABILITIES:
                labels = {
                    "stage": stage,
                    "execution_class": "browser",
                    "browser_backend": "chromium",
                    "route": route,
                    "provider": provider,
                    "capability": capability,
                }
                start["accepted_tasks"].append({"labels": labels, "value": 0})
                end["accepted_tasks"].append(
                    {
                        "labels": labels,
                        "value": task_counts[(stage, route, provider, capability)],
                    }
                )
        for reason in PRETRANSPORT_REASONS:
            labels = {
                "stage": stage,
                "execution_class": "browser",
                "browser_backend": "chromium",
                "reason": reason,
            }
            start["pretransport"].append({"labels": labels, "value": 0})
            end["pretransport"].append(
                {
                    "labels": labels,
                    "value": int(stage == "detail" and reason == "required_proxy_unavailable"),
                }
            )
        for reason in INSTRUMENTATION_REASONS:
            labels = {
                "stage": stage,
                "execution_class": "browser",
                "browser_backend": "chromium",
                "reason": reason,
            }
            start["instrumentation"].append({"labels": labels, "value": 0})
            end["instrumentation"].append({"labels": labels, "value": 0})
    return start, end


def build_fixture() -> dict[str, Any]:
    registry = load_registry()
    tape = _event_tape()
    start_rows = [_empty_row(dict(labels)) for labels in _request_label_sets()]
    end_rows = json.loads(json.dumps(start_rows))
    by_key = {tuple(row["labels"][name] for name in REQUEST_LABELS): row for row in end_rows}
    response_sizes: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for event in tape:
        if event["kind"] != "request_terminal":
            continue
        key = (
            event["stage"],
            "browser",
            "chromium",
            event["request_class"],
            event["route"],
            event["provider"],
            event["capability"],
        )
        row = by_key[key]
        row["attempts"] += 1
        row["outcomes"][event["outcome"]] += 1
        row["transferred_bytes"] += event["encoded_bytes"]
        if event["outcome"] in {"complete_response", "partial_response"}:
            response_sizes[key].append(event["encoded_bytes"])
    for key, sizes in response_sizes.items():
        row = by_key[key]
        row["histogram"]["sum"] = sum(sizes)
        row["histogram"]["count"] = len(sizes)
        for bucket in HISTOGRAM_BUCKETS:
            row["histogram"]["buckets"][str(bucket)] = (
                len(sizes) if bucket == "+Inf" else sum(size <= int(bucket) for size in sizes)
            )
    start_support, end_support = _support_boundaries()
    document = {
        "schema_version": CAPTURE_SCHEMA,
        "registry": {"version": registry.version, "sha256": registry.digest},
        "window": {
            "start_unix": 1_788_220_800,
            "end_unix": 1_788_307_200,
            "seconds": 86_400,
        },
        "boundaries": {
            "start": {"rows": start_rows, **start_support},
            "end": {"rows": end_rows, **end_support},
        },
        "event_tape": tape,
    }
    validation = validate_capture_fixture(document, registry)
    if not validation.valid:
        raise RuntimeError(f"generated fixture is invalid: {dict(validation.blockers)}")
    return document


def _payload() -> bytes:
    return (json.dumps(build_fixture(), sort_keys=True, separators=(",", ":")) + "\n").encode()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = _payload()
    if args.check:
        if not FIXTURE_PATH.exists() or FIXTURE_PATH.read_bytes() != payload:
            print("fixture differs from canonical output", file=sys.stderr)
            return 1
        return 0
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_bytes(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
