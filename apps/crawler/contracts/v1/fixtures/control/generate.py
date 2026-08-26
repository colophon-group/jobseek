#!/usr/bin/env python3
"""Generate the deterministic runtime-v1 control conformance corpus."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

FORMAT = "jobseek.runtime.control-corpus/v1"
REQUIRED = sorted(
    {
        "accept_complete",
        "accept_deduplicated_bound_resume",
        "accept_identical_unacked_replay",
        "accept_resume_after_ambiguous_dispatch",
        "reject_actual_limit_exceeded",
        "reject_deadline_expired_history",
        "reject_divergent_sequence_reuse",
        "reject_duplicate_logical_dedup",
        "reject_duplicate_origin_dispatch",
        "reject_fault_metadata_mismatch",
        "reject_frame_after_terminal",
        "reject_late_frame_after_cancel",
        "reject_manifest_revision_changed",
        "reject_origin_dispatch_before_declaration",
        "reject_origin_identity_reused",
        "reject_origin_redeclaration_changed",
        "reject_request_binding_changed",
        "reject_reused_attempt",
        "reject_sequence_gap",
        "reject_sequence_rewind",
        "reject_stale_fence",
        "reject_terminal_count_mismatch",
        "reject_terminal_duplicate",
        "reject_terminal_missing",
        "reject_trace_binding_changed",
        "reject_unknown_checkpoint",
        "reject_unknown_origin_contact",
    }
)

REQUEST_ID = "request-001"
ATTEMPT_1 = "attempt-001"
ATTEMPT_2 = "attempt-002"
ATTEMPT_3 = "attempt-003"
FENCE_DIGEST = "b" * 64
FP_1 = "1" * 64
FP_2 = "2" * 64
FP_3 = "3" * 64


def at(second: int) -> str:
    return f"2026-08-27T00:00:{second:02d}Z"


def operation(
    number: int,
    fingerprint: str,
    *,
    role: str = "monitor-page",
    parent: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "operation_sequence": number,
        "origin_request_id": f"{REQUEST_ID}/origin/{number:04d}",
        "request_fingerprint": fingerprint,
        "role": role,
    }
    if parent is not None:
        value["parent_origin_request_id"] = parent
    return value


OP_1 = operation(1, FP_1)
OP_2 = operation(2, FP_2, parent=OP_1["origin_request_id"])
OP_3 = operation(3, FP_3, parent=OP_1["origin_request_id"])
OP_4 = operation(4, "4" * 64, parent=OP_1["origin_request_id"])
OP_5 = operation(5, "5" * 64, parent=OP_1["origin_request_id"])


def fence() -> dict[str, Any]:
    return {
        "claim_token": "claim-001",
        "config_revision": "config-r1",
        "engine_owner": "go",
        "fence_digest": FENCE_DIGEST,
        "lease_id": "lease-001",
        "routing_epoch": 42,
        "shard_id": "shard-01",
    }


def limits() -> dict[str, int]:
    return {
        "max_active_duration_ms": 300_000,
        "max_artifact_chunk_bytes": 2_048,
        "max_artifact_count": 4,
        "max_artifact_total_bytes": 4_096,
        "max_browser_actions": 16,
        "max_browser_captures": 8,
        "max_browser_evaluations": 8,
        "max_browser_transfer_bytes": 1_048_576,
        "max_execution_frames": 32,
        "max_frame_bytes": 1_024,
        "max_http_transfer_bytes": 1_048_576,
        "max_in_flight_frames": 8,
        "max_inline_body_bytes": 512,
        "max_monitor_batches": 8,
        "max_output_items": 64,
        "max_retry_after_ms": 60_000,
    }


def request(kind: str = "monitor") -> dict[str, Any]:
    inputs: dict[str, dict[str, Any]] = {
        "browser": {
            "plan": {
                "contract_version": "crawler.runtime/v1",
                "target_url": "https://example.test/",
            }
        },
        "monitor": {"monitor_type": "sitemap"},
        "scrape": {
            "scrape_step": 0,
            "scraper_type": "json-ld",
            "source_url": "https://example.test/job/1",
        },
    }
    return {
        "attempt_id": ATTEMPT_1,
        "board_manifest": {
            "config_fingerprint": "config-fingerprint-r1",
            "config_revision": "config-r1",
            "manifest_id": "manifest-r1",
        },
        "contract_version": "crawler.runtime/v1",
        "deadline_rfc3339": "2026-08-27T00:05:00Z",
        "fencing_context": fence(),
        "input": {kind: inputs[kind]},
        "kind": kind,
        "origin_operations": [copy.deepcopy(OP_1)],
        "origin_request_id": OP_1["origin_request_id"],
        "request_id": REQUEST_ID,
        "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        "tracestate": "jobseek=runtime-v1",
    }


def client_hello(value: dict[str, int], *, observed: int = 0) -> dict[str, Any]:
    return {
        "client_hello": {
            "implementation": "python",
            "requested_limits": copy.deepcopy(value),
            "supported_contract_versions": ["crawler.runtime/v1"],
        },
        "direction": "client",
        "observed_at_rfc3339": at(observed),
    }


def server_hello(
    value: dict[str, int],
    *,
    observed: int = 0,
    accepted_at: str = "2026-08-27T00:00:00Z",
    initial_window: int = 8,
) -> dict[str, Any]:
    return {
        "direction": "server",
        "observed_at_rfc3339": at(observed),
        "server_hello": {
            "accepted_at_rfc3339": accepted_at,
            "accepted_limits": copy.deepcopy(value),
            "implementation": "go",
            "initial_window_frames": initial_window,
            "resume_by_origin_request_id": True,
            "selected_contract_version": "crawler.runtime/v1",
        },
    }


def start(value: dict[str, Any], *, observed: int = 0) -> dict[str, Any]:
    return {
        "direction": "client",
        "observed_at_rfc3339": at(observed),
        "start": copy.deepcopy(value),
    }


def frame(
    ordinal: int,
    payload: dict[str, Any],
    *,
    attempt: str = ATTEMPT_1,
    observed: int | None = None,
    size: int = 128,
) -> dict[str, Any]:
    projected_payload = copy.deepcopy(payload)
    measurements = {"wire_size_bytes": size}
    if projected_payload["type"] == "monitor_batch":
        measurements["output_items"] = projected_payload.pop("output_items")
    return {
        "direction": "server",
        "frame": {
            "attempt_id": attempt,
            "contract_version": "crawler.runtime/v1",
            "fence_digest": FENCE_DIGEST,
            "payload": projected_payload,
            "request_id": REQUEST_ID,
            "sequence": ordinal - 1,
        },
        "measurements": measurements,
        "observed_at_rfc3339": at(ordinal if observed is None else observed),
    }


def declared(op: dict[str, Any]) -> dict[str, Any]:
    return {"operation": copy.deepcopy(op), "type": "origin_operation_declared"}


def contact(op: dict[str, Any], disposition: str = "dispatched") -> dict[str, Any]:
    return {
        "disposition": disposition,
        "operation": copy.deepcopy(op),
        "request_fingerprint": op["request_fingerprint"],
        "type": "origin_contact",
    }


def monitor(outputs: int = 1) -> dict[str, Any]:
    return {"output_items": outputs, "type": "monitor_batch"}


def scrape() -> dict[str, Any]:
    return {"type": "scrape_result"}


def browser() -> dict[str, Any]:
    return {"type": "browser_result"}


def artifact(handle: str = "artifact-001", size: int = 256) -> dict[str, Any]:
    return {"artifact": {"handle": handle, "size_bytes": size}, "type": "artifact"}


def error(code: str = "timeout") -> dict[str, Any]:
    return {"code": code, "type": "error"}


def terminal(
    *,
    frames: int,
    outputs: int,
    batches: int,
    artifacts: int,
    origins: int,
    status: str = "success",
    eligible: bool = True,
    duration: int = 1_000,
) -> dict[str, Any]:
    return {
        "active_duration_ms": duration,
        "artifact_count": artifacts,
        "eligible_for_commit": eligible,
        "frame_count": frames - 1,
        "monitor_batches": batches,
        "origin_operation_count": origins,
        "output_items": outputs,
        "status": status,
        "type": "terminal",
    }


def fault(
    point: str,
    ordinal: int,
    *,
    dispatched_op: dict[str, Any] | None = None,
    observed: int = 20,
    with_sequence: bool = True,
) -> dict[str, Any]:
    value = {
        "direction": "fault",
        "fault": {
            "origin_request_id": ""
            if dispatched_op is None
            else dispatched_op["origin_request_id"],
            "origin_was_dispatched": dispatched_op is not None,
            "point": point,
            "request_fingerprint": ""
            if dispatched_op is None
            else dispatched_op["request_fingerprint"],
        },
        "observed_at_rfc3339": at(observed),
    }
    if with_sequence:
        value["fault"]["sequence"] = ordinal - 1
    return value


def resume(
    acknowledged_frames: int,
    *,
    attempt: str = ATTEMPT_2,
    observed: int = 21,
    resume_fence: dict[str, Any] | None = None,
    origin_request_id: str = OP_1["origin_request_id"],
    reconnect: bool = True,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "direction": "client",
        "observed_at_rfc3339": at(observed),
        "resume": {
            "attempt_id": attempt,
            "contract_version": "crawler.runtime/v1",
            "fencing_context": fence() if resume_fence is None else resume_fence,
            "origin_request_id": origin_request_id,
            "request_id": REQUEST_ID,
        },
    }
    if acknowledged_frames:
        value["resume"]["after_sequence"] = acknowledged_frames - 1
    value["_reconnect"] = reconnect
    return value


def cancel(observed: int = 10) -> dict[str, Any]:
    return {
        "cancel": {
            "attempt_id": ATTEMPT_1,
            "fencing_context": fence(),
            "reason": "owner-cancelled",
            "request_id": REQUEST_ID,
        },
        "direction": "client",
        "observed_at_rfc3339": at(observed),
    }


def window(additional: int, *, attempt: str = ATTEMPT_1, observed: int = 10) -> dict[str, Any]:
    return {
        "direction": "client",
        "observed_at_rfc3339": at(observed),
        "window_update": {
            "additional_frames": additional,
            "attempt_id": attempt,
            "fence_digest": FENCE_DIGEST,
            "request_id": REQUEST_ID,
        },
    }


def make_case(
    case_id: str,
    events: list[dict[str, Any]],
    *,
    accepted: bool = True,
    code: str = "ok",
    kind: str = "monitor",
    request_value: dict[str, Any] | None = None,
    limits_value: dict[str, int] | None = None,
    initial_window: int = 8,
) -> dict[str, Any]:
    current_request = request(kind) if request_value is None else copy.deepcopy(request_value)
    current_limits = limits() if limits_value is None else copy.deepcopy(limits_value)
    wire_events = [
        client_hello(current_limits),
        server_hello(current_limits, initial_window=initial_window),
        start(current_request),
    ]
    for source_event in events:
        event = copy.deepcopy(source_event)
        reconnect = event.pop("_reconnect", False)
        if (
            "resume" in event
            and reconnect
            and not (
                len(wire_events) >= 2
                and "client_hello" in wire_events[-2]
                and "server_hello" in wire_events[-1]
            )
        ):
            observed_at = event["observed_at_rfc3339"]
            wire_events.extend(
                [
                    client_hello(current_limits),
                    server_hello(
                        current_limits,
                        accepted_at=observed_at,
                        initial_window=initial_window,
                    ),
                ]
            )
        wire_events.append(event)
    first_fault = next(
        (index for index, event in enumerate(wire_events) if event["direction"] == "fault"),
        None,
    )
    first_cancel = next(
        (index for index, event in enumerate(wire_events) if "cancel" in event),
        None,
    )
    if first_fault is None:
        if first_cancel is None:
            cut = 2
            phase = "none"
        else:
            cut = first_cancel - 1
            phase = "cancel"
    else:
        cut = max(
            index
            for index, event in enumerate(wire_events[:first_fault])
            if event["direction"] != "fault"
        )
        phase = wire_events[first_fault]["fault"]["point"]
    logical_time = wire_events[-1]["observed_at_rfc3339"]
    for event in wire_events:
        # Fixture-only timing is collapsed into case metadata. ProtocolEvent
        # itself has no timestamp field in the frozen descriptor.
        del event["observed_at_rfc3339"]
    return {
        "events": wire_events,
        "expected": {"accepted": accepted, "code": code},
        "id": case_id,
        "metadata": {
            "durable_cut_event_index": cut,
            "injection_phase": phase,
            "logical_time_rfc3339": logical_time,
        },
    }


def rejecting(
    case_id: str,
    code: str,
    events: list[dict[str, Any]],
    **case_options: Any,
) -> dict[str, Any]:
    return make_case(case_id, events, accepted=False, code=code, **case_options)


def corpus() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    cases.append(
        make_case(
            "accept_complete",
            [
                frame(1, contact(OP_1)),
                frame(2, monitor()),
                frame(3, declared(OP_2)),
                frame(4, contact(OP_2)),
                frame(5, monitor()),
                frame(6, terminal(frames=6, outputs=2, batches=2, artifacts=0, origins=2)),
            ],
        )
    )
    cases.append(
        make_case(
            "accept_resume_after_ambiguous_dispatch",
            [
                frame(1, contact(OP_1)),
                fault("after_dispatch", 1, dispatched_op=OP_1),
                resume(1),
                frame(2, contact(OP_1, "deduplicated"), attempt=ATTEMPT_2),
                frame(3, monitor(), attempt=ATTEMPT_2),
                frame(
                    4,
                    terminal(frames=4, outputs=1, batches=1, artifacts=0, origins=1),
                    attempt=ATTEMPT_2,
                ),
            ],
        )
    )
    cases.append(
        make_case(
            "accept_deduplicated_bound_resume",
            [
                frame(1, contact(OP_1)),
                fault("after_dispatch", 1, dispatched_op=OP_1),
                resume(0),
                frame(1, contact(OP_1), attempt=ATTEMPT_2),
                frame(2, contact(OP_1, "deduplicated"), attempt=ATTEMPT_2),
                frame(3, monitor(), attempt=ATTEMPT_2),
                frame(
                    4,
                    terminal(frames=4, outputs=1, batches=1, artifacts=0, origins=1),
                    attempt=ATTEMPT_2,
                ),
            ],
        )
    )
    cases.append(
        make_case(
            "accept_unacked_dynamic_declaration_replay",
            [
                frame(1, contact(OP_1)),
                frame(2, monitor()),
                frame(3, declared(OP_2)),
                fault("after_frame", 3),
                resume(2),
                frame(3, declared(OP_2), attempt=ATTEMPT_2),
                frame(4, contact(OP_2), attempt=ATTEMPT_2),
                frame(5, monitor(), attempt=ATTEMPT_2),
                frame(
                    6,
                    terminal(frames=6, outputs=2, batches=2, artifacts=0, origins=2),
                    attempt=ATTEMPT_2,
                ),
            ],
        )
    )
    cases.append(
        make_case(
            "accept_acked_resume_before_next_frame",
            [
                frame(1, contact(OP_1)),
                frame(2, monitor()),
                fault("before_frame", 3),
                resume(2),
                frame(
                    3,
                    terminal(frames=3, outputs=1, batches=1, artifacts=0, origins=1),
                    attempt=ATTEMPT_2,
                ),
            ],
        )
    )
    cases.append(
        make_case(
            "accept_repeated_bound_resume",
            [
                frame(1, contact(OP_1)),
                fault("after_dispatch", 1, dispatched_op=OP_1),
                resume(0),
                frame(1, contact(OP_1), attempt=ATTEMPT_2),
                fault("after_dispatch", 1, dispatched_op=OP_1, observed=22),
                resume(1, attempt=ATTEMPT_3, observed=23),
                frame(2, contact(OP_1, "deduplicated"), attempt=ATTEMPT_3),
                frame(3, monitor(), attempt=ATTEMPT_3),
                frame(
                    4,
                    terminal(frames=4, outputs=1, batches=1, artifacts=0, origins=1),
                    attempt=ATTEMPT_3,
                ),
            ],
        )
    )
    cases.append(
        make_case(
            "accept_window_replenishment",
            [
                frame(1, contact(OP_1)),
                frame(2, monitor()),
                window(2, observed=3),
                frame(3, artifact(), observed=4),
                frame(
                    4, terminal(frames=4, outputs=1, batches=1, artifacts=1, origins=1), observed=5
                ),
            ],
            initial_window=2,
        )
    )
    cases.append(
        make_case(
            "accept_cancelled_terminal_after_cancel",
            [
                frame(1, monitor(), observed=1),
                cancel(observed=2),
                frame(
                    2,
                    terminal(
                        frames=2,
                        outputs=1,
                        batches=1,
                        artifacts=0,
                        origins=1,
                        status="cancelled",
                        eligible=False,
                    ),
                    observed=3,
                ),
            ],
        )
    )
    cases.append(
        make_case(
            "accept_fault_without_sequence",
            [
                frame(1, contact(OP_1)),
                fault(
                    "after_dispatch",
                    1,
                    dispatched_op=OP_1,
                    with_sequence=False,
                ),
                resume(1),
                frame(2, contact(OP_1, "deduplicated"), attempt=ATTEMPT_2),
                frame(3, monitor(), attempt=ATTEMPT_2),
                frame(
                    4,
                    terminal(
                        frames=4,
                        outputs=1,
                        batches=1,
                        artifacts=0,
                        origins=1,
                    ),
                    attempt=ATTEMPT_2,
                ),
            ],
        )
    )
    cases.append(
        make_case(
            "accept_partial_replay_fault_resume",
            [
                frame(1, contact(OP_1)),
                frame(2, monitor()),
                fault("after_frame", 2),
                resume(0),
                frame(1, contact(OP_1), attempt=ATTEMPT_2),
                fault("after_frame", 1, observed=22),
                resume(0, attempt=ATTEMPT_3, observed=23),
                frame(1, contact(OP_1), attempt=ATTEMPT_3),
                frame(2, monitor(), attempt=ATTEMPT_3),
                frame(
                    3,
                    terminal(
                        frames=3,
                        outputs=1,
                        batches=1,
                        artifacts=0,
                        origins=1,
                    ),
                    attempt=ATTEMPT_3,
                ),
            ],
        )
    )

    # A before/after and unacknowledged replay matrix for every lane-owned
    # ExecutionFrame payload class. Resume.after_sequence is the only ACK.
    cases.extend(
        [
            make_case(
                "accept_disconnect_before_declaration",
                [
                    fault("before_frame", 1),
                    resume(0),
                    frame(1, declared(OP_2), attempt=ATTEMPT_2),
                    frame(2, monitor(), attempt=ATTEMPT_2),
                    frame(
                        3,
                        terminal(frames=3, outputs=1, batches=1, artifacts=0, origins=2),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
            make_case(
                "accept_disconnect_before_origin_contact",
                [
                    fault("before_frame", 1),
                    resume(0),
                    frame(1, contact(OP_1), attempt=ATTEMPT_2),
                    frame(2, monitor(), attempt=ATTEMPT_2),
                    frame(
                        3,
                        terminal(frames=3, outputs=1, batches=1, artifacts=0, origins=1),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
            make_case(
                "accept_disconnect_before_monitor_batch",
                [
                    frame(1, contact(OP_1)),
                    fault("before_frame", 2, dispatched_op=OP_1),
                    resume(1),
                    frame(2, contact(OP_1, "deduplicated"), attempt=ATTEMPT_2),
                    frame(3, monitor(), attempt=ATTEMPT_2),
                    frame(
                        4,
                        terminal(frames=4, outputs=1, batches=1, artifacts=0, origins=1),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
            make_case(
                "accept_disconnect_before_artifact",
                [
                    frame(1, monitor()),
                    fault("before_frame", 2),
                    resume(1),
                    frame(2, artifact(), attempt=ATTEMPT_2),
                    frame(
                        3,
                        terminal(frames=3, outputs=1, batches=1, artifacts=1, origins=1),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
            make_case(
                "accept_disconnect_before_error",
                [
                    fault("before_frame", 1),
                    resume(0),
                    frame(1, error(), attempt=ATTEMPT_2),
                    frame(
                        2,
                        terminal(
                            frames=2,
                            outputs=0,
                            batches=0,
                            artifacts=0,
                            origins=1,
                            status="error",
                            eligible=False,
                        ),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
            make_case(
                "accept_disconnect_before_terminal",
                [
                    frame(1, monitor()),
                    fault("before_frame", 2),
                    resume(1),
                    frame(
                        2,
                        terminal(frames=2, outputs=1, batches=1, artifacts=0, origins=1),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
            make_case(
                "accept_disconnect_result_before_terminal",
                [
                    frame(1, monitor()),
                    fault("result_before_terminal", 1),
                    resume(1),
                    frame(
                        2,
                        terminal(frames=2, outputs=1, batches=1, artifacts=0, origins=1),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
            make_case(
                "accept_identical_unacked_replay",
                [
                    frame(1, contact(OP_1)),
                    frame(2, monitor()),
                    fault("after_frame", 2),
                    resume(1),
                    frame(2, monitor(), attempt=ATTEMPT_2),
                    frame(
                        3,
                        terminal(frames=3, outputs=1, batches=1, artifacts=0, origins=1),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
            make_case(
                "accept_unacked_artifact_replay",
                [
                    frame(1, monitor()),
                    frame(2, artifact()),
                    fault("after_frame", 2),
                    resume(1),
                    frame(2, artifact(), attempt=ATTEMPT_2),
                    frame(
                        3,
                        terminal(frames=3, outputs=1, batches=1, artifacts=1, origins=1),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
            make_case(
                "accept_unacked_error_replay",
                [
                    frame(1, error()),
                    fault("after_frame", 1),
                    resume(0),
                    frame(1, error(), attempt=ATTEMPT_2),
                    frame(
                        2,
                        terminal(
                            frames=2,
                            outputs=0,
                            batches=0,
                            artifacts=0,
                            origins=1,
                            status="error",
                            eligible=False,
                        ),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
            make_case(
                "accept_disconnect_after_terminal",
                [
                    frame(1, monitor()),
                    frame(2, terminal(frames=2, outputs=1, batches=1, artifacts=0, origins=1)),
                    fault("after_frame", 2),
                    resume(1),
                    frame(
                        2,
                        terminal(frames=2, outputs=1, batches=1, artifacts=0, origins=1),
                        attempt=ATTEMPT_2,
                    ),
                ],
            ),
        ]
    )

    scrape_case = make_case(
        "accept_unacked_scrape_result_replay",
        [
            frame(1, contact(OP_1)),
            frame(2, scrape()),
            fault("after_frame", 2),
            resume(1),
            frame(2, scrape(), attempt=ATTEMPT_2),
            frame(
                3,
                terminal(frames=3, outputs=1, batches=0, artifacts=0, origins=1),
                attempt=ATTEMPT_2,
            ),
        ],
        kind="scrape",
    )
    cases.append(scrape_case)
    browser_case = make_case(
        "accept_unacked_browser_result_replay",
        [
            frame(1, contact(OP_1)),
            frame(2, browser()),
            fault("after_frame", 2),
            resume(1),
            frame(2, browser(), attempt=ATTEMPT_2),
            frame(
                3,
                terminal(frames=3, outputs=1, batches=0, artifacts=0, origins=1),
                attempt=ATTEMPT_2,
            ),
        ],
        kind="browser",
    )
    cases.append(browser_case)

    changed_request = request()
    changed_request["attempt_id"] = ATTEMPT_2
    changed_request["input"]["monitor"]["monitor_type"] = "workday"
    cases.append(
        rejecting(
            "reject_request_binding_changed",
            "binding_changed",
            [fault("before_frame", 1), start(changed_request, observed=21)],
        )
    )
    changed_request = request()
    changed_request["attempt_id"] = ATTEMPT_2
    changed_request["board_manifest"]["config_revision"] = "config-r2"
    cases.append(
        rejecting(
            "reject_manifest_revision_changed",
            "manifest_revision_changed",
            [fault("before_frame", 1), start(changed_request, observed=21)],
        )
    )
    changed_limits = limits()
    changed_limits["max_frame_bytes"] = 2_048
    cases.append(
        rejecting(
            "reject_limits_changed",
            "limits_changed",
            [
                fault("before_frame", 1),
                client_hello(changed_limits, observed=20),
                server_hello(changed_limits, observed=20),
                resume(0),
            ],
        )
    )
    changed_request = request()
    changed_request["attempt_id"] = ATTEMPT_2
    changed_request["deadline_rfc3339"] = "2026-08-27T00:04:59Z"
    cases.append(
        rejecting(
            "reject_deadline_regression",
            "deadline_regression",
            [fault("before_frame", 1), start(changed_request, observed=21)],
        )
    )
    changed_request = request()
    changed_request["attempt_id"] = ATTEMPT_2
    changed_request["traceparent"] = "00-4bf92f3577b34da6a3ce929d0e0e4737-00f067aa0ba902b7-01"
    cases.append(
        rejecting(
            "reject_trace_binding_changed",
            "trace_binding_changed",
            [fault("before_frame", 1), start(changed_request, observed=21)],
        )
    )

    stale = fence()
    stale["routing_epoch"] = 41
    cases.append(
        rejecting(
            "reject_stale_fence",
            "stale_fence",
            [fault("before_frame", 1), resume(0, resume_fence=stale)],
        )
    )
    cases.append(
        rejecting(
            "reject_reused_attempt",
            "reused_attempt",
            [fault("before_frame", 1), resume(0, attempt=ATTEMPT_1)],
        )
    )
    cases.append(
        rejecting(
            "reject_resume_without_reconnect_handshake",
            "resume_handshake_missing",
            [
                fault("before_frame", 1),
                resume(0, reconnect=False),
            ],
        )
    )
    cases.append(
        rejecting(
            "reject_resume_checkpoint_rewind",
            "sequence_rewind",
            [
                frame(1, monitor()),
                fault("after_frame", 1),
                resume(1),
                fault("before_frame", 2, observed=22),
                resume(0, attempt=ATTEMPT_3, observed=23),
            ],
        )
    )
    cases.append(
        rejecting(
            "reject_late_frame_after_cancel",
            "cancelled",
            [cancel(), frame(1, monitor(), observed=11)],
        )
    )
    cases.append(
        rejecting(
            "reject_origin_dispatch_before_declaration",
            "origin_dispatch_before_declaration",
            [frame(1, contact(OP_2))],
        )
    )
    cases.append(
        rejecting(
            "reject_unknown_origin_contact",
            "unknown_origin_contact",
            [frame(1, contact(OP_3))],
        )
    )
    initial_gap_request = request()
    root_at_two = operation(2, FP_1)
    initial_gap_request["origin_operations"] = [root_at_two]
    initial_gap_request["origin_request_id"] = root_at_two["origin_request_id"]
    cases.append(
        rejecting(
            "reject_initial_origin_sequence_gap",
            "initial_origin_sequence_invalid",
            [],
            request_value=initial_gap_request,
        )
    )
    initial_parent_request = request()
    unknown_parent = copy.deepcopy(OP_2)
    unknown_parent["parent_origin_request_id"] = f"{REQUEST_ID}/origin/9999"
    initial_parent_request["origin_operations"] = [copy.deepcopy(OP_1), unknown_parent]
    cases.append(
        rejecting(
            "reject_initial_origin_parent_unknown",
            "initial_origin_parent_unknown",
            [],
            request_value=initial_parent_request,
        )
    )
    initial_unsorted_request = request()
    first = operation(2, FP_2)
    second = operation(1, FP_1)
    initial_unsorted_request["origin_operations"] = [first, second]
    initial_unsorted_request["origin_request_id"] = first["origin_request_id"]
    cases.append(
        rejecting(
            "reject_initial_origin_unsorted",
            "initial_origin_sequence_invalid",
            [],
            request_value=initial_unsorted_request,
        )
    )
    reused = copy.deepcopy(OP_2)
    reused["origin_request_id"] = OP_3["origin_request_id"]
    cases.append(
        rejecting(
            "reject_origin_identity_reused",
            "origin_identity_reused",
            [frame(1, declared(OP_2)), frame(2, declared(reused))],
        )
    )
    mutated = copy.deepcopy(OP_2)
    mutated["role"] = "changed-role"
    cases.append(
        rejecting(
            "reject_origin_redeclaration_changed",
            "origin_redeclaration_changed",
            [frame(1, declared(OP_2)), frame(2, declared(mutated))],
        )
    )
    cases.append(
        rejecting(
            "reject_duplicate_origin_dispatch",
            "duplicate_origin_dispatch",
            [frame(1, contact(OP_1)), frame(2, contact(OP_1))],
        )
    )
    cases.append(
        rejecting(
            "reject_fault_metadata_mismatch",
            "fault_metadata_mismatch",
            [frame(1, declared(OP_2)), fault("after_dispatch", 1, dispatched_op=OP_2)],
        )
    )
    cases.append(rejecting("reject_sequence_gap", "sequence_gap", [frame(2, monitor())]))
    cases.append(
        rejecting(
            "reject_sequence_rewind",
            "sequence_rewind",
            [
                frame(1, monitor()),
                frame(1, terminal(frames=2, outputs=1, batches=1, artifacts=0, origins=1)),
            ],
        )
    )
    limited_limits = limits()
    limited_limits["max_execution_frames"] = 1
    limited = rejecting(
        "reject_frame_limit_exceeded",
        "frame_limit_exceeded",
        [frame(1, monitor()), frame(2, artifact())],
        limits_value=limited_limits,
    )
    cases.append(limited)
    cases.append(rejecting("reject_terminal_missing", "terminal_missing", [frame(1, monitor())]))
    cases.append(
        rejecting(
            "reject_terminal_duplicate",
            "terminal_duplicate",
            [
                frame(1, monitor()),
                frame(2, terminal(frames=2, outputs=1, batches=1, artifacts=0, origins=1)),
                frame(3, terminal(frames=3, outputs=1, batches=1, artifacts=0, origins=1)),
            ],
        )
    )
    cases.append(
        rejecting(
            "reject_frame_after_terminal",
            "frame_after_terminal",
            [
                frame(1, monitor()),
                frame(2, terminal(frames=2, outputs=1, batches=1, artifacts=0, origins=1)),
                frame(3, artifact()),
            ],
        )
    )
    cases.append(
        rejecting(
            "reject_terminal_count_mismatch",
            "terminal_count_mismatch",
            [
                frame(1, monitor()),
                frame(2, terminal(frames=3, outputs=1, batches=1, artifacts=0, origins=1)),
            ],
        )
    )

    fingerprint_changed = copy.deepcopy(OP_1)
    fingerprint_changed["request_fingerprint"] = "9" * 64
    cases.append(
        rejecting(
            "reject_origin_fingerprint_changed",
            "origin_fingerprint_changed",
            [frame(1, contact(fingerprint_changed))],
        )
    )
    cases.append(
        rejecting(
            "reject_divergent_sequence_reuse",
            "divergent_sequence_reuse",
            [
                frame(1, monitor()),
                fault("after_frame", 1),
                resume(0),
                frame(1, monitor(2), attempt=ATTEMPT_2),
            ],
        )
    )
    cases.append(
        rejecting(
            "reject_stale_attempt_frame_after_resume",
            "binding_changed",
            [fault("before_frame", 1), resume(0), frame(1, monitor(), attempt=ATTEMPT_1)],
        )
    )
    cases.append(
        rejecting(
            "reject_unknown_checkpoint",
            "unknown_checkpoint",
            [fault("before_frame", 1), resume(1)],
        )
    )
    cases.append(
        rejecting(
            "reject_dedup_without_ambiguous_dispatch",
            "origin_deduplication_not_ambiguous",
            [
                fault("before_frame", 1),
                resume(0),
                frame(1, contact(OP_1, "deduplicated"), attempt=ATTEMPT_2),
            ],
        )
    )
    cases.append(
        rejecting(
            "reject_duplicate_logical_dedup",
            "duplicate_logical_dedup",
            [
                frame(1, contact(OP_1)),
                fault("after_dispatch", 1, dispatched_op=OP_1),
                resume(1),
                frame(2, contact(OP_1, "deduplicated"), attempt=ATTEMPT_2),
                frame(3, contact(OP_1, "deduplicated"), attempt=ATTEMPT_2),
            ],
        )
    )

    credit = rejecting(
        "reject_physical_replay_credit_exhausted",
        "credit_exceeded",
        [
            frame(1, monitor()),
            fault("after_frame", 1),
            resume(0),
            frame(1, monitor(), attempt=ATTEMPT_2),
            frame(
                2,
                terminal(frames=2, outputs=1, batches=1, artifacts=0, origins=1),
                attempt=ATTEMPT_2,
            ),
        ],
        initial_window=1,
    )
    cases.append(credit)
    overflow = rejecting("reject_credit_update_overflow", "credit_exceeded", [window(1)])
    cases.append(overflow)

    late_request = request()
    late_request["deadline_rfc3339"] = "2026-08-27T00:00:30Z"
    late = rejecting(
        "reject_deadline_expired_history",
        "deadline_exceeded",
        [frame(1, monitor(), observed=31)],
        request_value=late_request,
    )
    late["metadata"]["injection_phase"] = "deadline"
    cases.append(late)
    deadline_prefix_request = request()
    deadline_prefix_request["deadline_rfc3339"] = "2026-08-27T00:00:30Z"
    deadline_prefix_limits = limits()
    deadline_prefix_limits["max_active_duration_ms"] = 30_000
    deadline_prefix = rejecting(
        "reject_deadline_after_durable_prefix",
        "deadline_exceeded",
        [
            frame(1, monitor(), observed=1),
            resume(1, observed=31),
            frame(
                2,
                terminal(
                    frames=2,
                    outputs=1,
                    batches=1,
                    artifacts=0,
                    origins=1,
                ),
                attempt=ATTEMPT_2,
                observed=32,
            ),
        ],
        request_value=deadline_prefix_request,
        limits_value=deadline_prefix_limits,
    )
    deadline_prefix["metadata"].update(
        {
            "durable_cut_event_index": 3,
            "injection_phase": "deadline",
        }
    )
    cases.append(deadline_prefix)
    duration_limits = limits()
    duration_limits["max_active_duration_ms"] = 299_999
    duration = rejecting(
        "reject_negotiated_duration_exceeded",
        "active_duration_limit_exceeded",
        [],
        limits_value=duration_limits,
    )
    cases.append(duration)

    invalid_trace_request = request()
    invalid_trace_request["traceparent"] = "00-00000000000000000000000000000000-00f067aa0ba902b7-01"
    invalid_trace = rejecting(
        "reject_invalid_traceparent",
        "invalid_trace_context",
        [],
        request_value=invalid_trace_request,
    )
    cases.append(invalid_trace)
    invalid_state_request = request()
    invalid_state_request["tracestate"] = "jobseek=one,jobseek=two"
    invalid_state = rejecting(
        "reject_invalid_tracestate_duplicate",
        "invalid_trace_context",
        [],
        request_value=invalid_state_request,
    )
    cases.append(invalid_state)
    invalid_deadline_request = request()
    invalid_deadline_request["deadline_rfc3339"] = "2026-08-27T00:00:00Z"
    cases.append(
        rejecting(
            "reject_invalid_deadline",
            "invalid_deadline",
            [],
            request_value=invalid_deadline_request,
        )
    )
    invalid_shape_request = request()
    invalid_shape_request["input"] = {}
    cases.append(
        rejecting(
            "reject_invalid_wire_shape",
            "invalid_corpus",
            [],
            request_value=invalid_shape_request,
        )
    )
    cases.append(
        rejecting(
            "reject_wrong_frame_kind",
            "wrong_frame_kind",
            [frame(1, monitor())],
            kind="scrape",
        )
    )

    oversized = rejecting(
        "reject_actual_limit_exceeded",
        "frame_size_limit_exceeded",
        [frame(1, monitor(), size=1_025)],
    )
    cases.append(oversized)
    outputs = rejecting(
        "reject_output_limit_exceeded", "output_limit_exceeded", [frame(1, monitor(65))]
    )
    cases.append(outputs)
    artifacts = rejecting(
        "reject_artifact_count_limit_exceeded",
        "artifact_count_limit_exceeded",
        [
            frame(1, artifact("a1")),
            frame(2, artifact("a2")),
            frame(3, artifact("a3")),
            frame(4, artifact("a4")),
            frame(5, artifact("a5")),
        ],
    )
    cases.append(artifacts)
    artifact_bytes = rejecting(
        "reject_artifact_total_bytes_limit_exceeded",
        "artifact_total_bytes_limit_exceeded",
        [frame(1, artifact(size=4_097))],
    )
    cases.append(artifact_bytes)
    cases.append(
        rejecting(
            "reject_artifact_identity_reused",
            "artifact_identity_reused",
            [frame(1, artifact("same")), frame(2, artifact("same"))],
        )
    )
    cases.append(
        rejecting(
            "reject_origin_local_cap_exceeded",
            "origin_local_cap_exceeded",
            [
                frame(1, declared(OP_2)),
                frame(2, declared(OP_3)),
                frame(3, declared(OP_4)),
                frame(4, declared(OP_5)),
            ],
        )
    )
    cases.append(
        rejecting(
            "reject_error_local_cap_exceeded",
            "error_local_cap_exceeded",
            [frame(index, error()) for index in range(1, 6)],
        )
    )

    # Every actual Terminal counter/authority field gets a dedicated mutation.
    mutations = {
        "artifact": {"artifact_count": 1},
        "eligible": {"eligible_for_commit": False},
        "monitor_batch": {"monitor_batches": 2},
        "origin": {"origin_operation_count": 2},
        "output": {"output_items": 2},
    }
    for label, mutation in mutations.items():
        payload = terminal(frames=2, outputs=1, batches=1, artifacts=0, origins=1)
        payload.update(mutation)
        cases.append(
            rejecting(
                f"reject_terminal_{label}_field_mismatch",
                "terminal_count_mismatch",
                [frame(1, monitor()), frame(2, payload)],
            )
        )
    active = rejecting(
        "reject_terminal_active_duration_limit",
        "active_duration_limit_exceeded",
        [
            frame(1, monitor()),
            frame(
                2,
                terminal(frames=2, outputs=1, batches=1, artifacts=0, origins=1, duration=300_001),
            ),
        ],
    )
    cases.append(active)
    cases.append(
        rejecting(
            "reject_terminal_status_mismatch",
            "terminal_count_mismatch",
            [
                frame(1, monitor()),
                frame(
                    2,
                    terminal(
                        frames=2,
                        outputs=1,
                        batches=1,
                        artifacts=0,
                        origins=1,
                        status="error",
                        eligible=False,
                    ),
                ),
            ],
        )
    )
    cases.append(
        rejecting(
            "reject_cancelled_terminal_without_cancel",
            "terminal_count_mismatch",
            [
                frame(1, monitor()),
                frame(
                    2,
                    terminal(
                        frames=2,
                        outputs=1,
                        batches=1,
                        artifacts=0,
                        origins=1,
                        status="cancelled",
                        eligible=False,
                    ),
                ),
            ],
        )
    )

    relabel_cancel = rejecting(
        "reject_cancel_relabelled_for_deduplication",
        "fault_metadata_mismatch",
        [
            frame(1, contact(OP_1)),
            cancel(observed=2),
            fault("after_dispatch", 1, dispatched_op=OP_1, observed=3),
        ],
    )
    cases.append(relabel_cancel)

    cut_mismatch = rejecting(
        "reject_fixture_cut_mismatch",
        "fixture_cut_mismatch",
        [
            frame(1, monitor()),
            frame(
                2,
                terminal(
                    frames=2,
                    outputs=1,
                    batches=1,
                    artifacts=0,
                    origins=1,
                ),
            ),
        ],
    )
    cut_mismatch["metadata"]["durable_cut_event_index"] = 0
    cases.append(cut_mismatch)
    phase_mismatch = rejecting(
        "reject_fixture_injection_phase_mismatch",
        "fixture_injection_phase_mismatch",
        [
            fault("before_frame", 1),
            resume(0),
            frame(
                1,
                terminal(
                    frames=1,
                    outputs=0,
                    batches=0,
                    artifacts=0,
                    origins=1,
                    status="error",
                    eligible=False,
                ),
                attempt=ATTEMPT_2,
            ),
        ],
    )
    phase_mismatch["metadata"]["injection_phase"] = "after_dispatch"
    cases.append(phase_mismatch)
    ordered = sorted(cases, key=lambda item: item["id"])
    ids = [item["id"] for item in ordered]
    assert len(ids) == len(set(ids))
    assert set(REQUIRED) <= set(ids)
    return {"cases": ordered, "format": FORMAT, "required_case_ids": REQUIRED}


def render() -> bytes:
    return (
        json.dumps(corpus(), ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parent
    manifest = root / "manifest.json"
    digest = root / "manifest.sha256"
    content = render()
    digest_content = f"{hashlib.sha256(content).hexdigest()}  manifest.json\n".encode("ascii")
    if arguments.check:
        if manifest.read_bytes() != content or digest.read_bytes() != digest_content:
            raise SystemExit("control corpus is not deterministic; run generate.py --write")
        return 0
    manifest.write_bytes(content)
    digest.write_bytes(digest_content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
