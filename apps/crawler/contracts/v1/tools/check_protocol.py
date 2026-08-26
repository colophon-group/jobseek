#!/usr/bin/env python3
"""Offline validator for the candidate runtime-v1 control corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

FORMAT = "jobseek.runtime.control-corpus/v1"
CONTRACT_VERSION = "crawler.runtime/v1"

MANDATORY_CASE_IDS = frozenset(
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

ERROR_CODES = frozenset(
    {
        "ok",
        "active_duration_limit_exceeded",
        "artifact_count_limit_exceeded",
        "artifact_identity_reused",
        "artifact_total_bytes_limit_exceeded",
        "binding_changed",
        "cancelled",
        "credit_exceeded",
        "deadline_exceeded",
        "deadline_regression",
        "divergent_sequence_reuse",
        "duplicate_logical_dedup",
        "duplicate_origin_dispatch",
        "error_local_cap_exceeded",
        "fault_metadata_mismatch",
        "frame_after_terminal",
        "frame_limit_exceeded",
        "frame_size_limit_exceeded",
        "fixture_cut_mismatch",
        "fixture_injection_phase_mismatch",
        "initial_origin_parent_unknown",
        "initial_origin_sequence_invalid",
        "invalid_corpus",
        "invalid_deadline",
        "invalid_trace_context",
        "limits_changed",
        "manifest_revision_changed",
        "origin_deduplication_not_ambiguous",
        "origin_dispatch_before_declaration",
        "origin_fingerprint_changed",
        "origin_identity_reused",
        "origin_local_cap_exceeded",
        "origin_redeclaration_changed",
        "output_limit_exceeded",
        "reused_attempt",
        "resume_handshake_missing",
        "sequence_gap",
        "sequence_rewind",
        "stale_fence",
        "terminal_count_mismatch",
        "terminal_duplicate",
        "terminal_missing",
        "trace_binding_changed",
        "unknown_checkpoint",
        "unknown_origin_contact",
        "wrong_frame_kind",
    }
)

# Fixture-machine safety caps. These are deliberately not negotiated Limits
# fields and do not extend the frozen wire descriptor.
LOCAL_MAX_ORIGIN_OPERATIONS = 4
LOCAL_MAX_ERRORS = 4

_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_TRACESTATE_KEY = re.compile(
    r"^(?:[a-z][a-z0-9_\-*/]{0,255}|"
    r"[a-z0-9][a-z0-9_\-*/]{0,240}@[a-z][a-z0-9_\-*/]{0,13})$"
)
_ORIGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,95}/origin/[0-9]{4,10}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_ERROR_CODES = frozenset(
    {
        "anti_bot",
        "ambiguous_origin",
        "cancelled",
        "empty_result",
        "http_status",
        "internal",
        "invalid_config",
        "navigation",
        "permanent_gone",
        "provider_gone",
        "resource_limit",
        "session_lost",
        "target_lost",
        "tdm_reserved",
        "timeout",
        "transport",
        "unsupported_capability",
    }
)


class ProtocolFailure(Exception):
    """A stable fail-closed conformance result."""

    def __init__(self, code: str) -> None:
        if code not in ERROR_CODES:
            raise AssertionError(f"unregistered protocol error code: {code}")
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise ProtocolFailure(code)


def _object(
    value: Any,
    *,
    allowed: set[str],
    required: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        _fail("invalid_corpus")
    if set(value) - allowed or required - set(value):
        _fail("invalid_corpus")
    return value


def _string(value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        _fail("invalid_corpus")
    return value


def _integer(value: Any, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail("invalid_corpus")
    return value


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        _fail("invalid_corpus")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _parse_rfc3339(value: Any) -> datetime:
    text = _string(value)
    if not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})",
        text,
    ):
        _fail("invalid_deadline")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        _fail("invalid_deadline")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail("invalid_deadline")
    offset = parsed.utcoffset()
    assert offset is not None
    if abs(offset.total_seconds()) > 14 * 60 * 60:
        _fail("invalid_deadline")
    return parsed.astimezone(UTC)


def _validate_trace(traceparent: Any, tracestate: Any) -> None:
    if traceparent is None:
        if tracestate is not None:
            _fail("invalid_trace_context")
        return
    match = _TRACEPARENT.fullmatch(_string(traceparent))
    if match is None or int(match.group(1), 16) == 0 or int(match.group(2), 16) == 0:
        _fail("invalid_trace_context")
    if tracestate is None:
        return
    value = _string(tracestate)
    if len(value.encode("ascii", errors="ignore")) != len(value) or len(value) > 512:
        _fail("invalid_trace_context")
    members = value.split(",")
    if len(members) > 32 or any(member != member.strip() for member in members):
        _fail("invalid_trace_context")
    keys: set[str] = set()
    for member in members:
        if member.count("=") != 1:
            _fail("invalid_trace_context")
        key, item = member.split("=", 1)
        if (
            _TRACESTATE_KEY.fullmatch(key) is None
            or key in keys
            or not item
            or len(item) > 256
            or item[0] == " "
            or item[-1] == " "
            or any(ord(char) < 0x20 or ord(char) > 0x7E or char in ",=" for char in item)
        ):
            _fail("invalid_trace_context")
        keys.add(key)


def _validate_fence(value: Any) -> dict[str, Any]:
    fence = _object(
        value,
        allowed={
            "claim_token",
            "config_revision",
            "engine_owner",
            "fence_digest",
            "lease_id",
            "routing_epoch",
            "shard_id",
        },
        required={
            "claim_token",
            "config_revision",
            "engine_owner",
            "fence_digest",
            "lease_id",
            "routing_epoch",
            "shard_id",
        },
    )
    for key in ("claim_token", "config_revision", "lease_id", "shard_id"):
        _string(fence[key])
    if fence["engine_owner"] not in {"python", "go"}:
        _fail("invalid_corpus")
    _integer(fence["routing_epoch"], minimum=1)
    if _HEX_64.fullmatch(_string(fence["fence_digest"])) is None:
        _fail("invalid_corpus")
    return fence


def _validate_operation(value: Any) -> dict[str, Any]:
    operation = _object(
        value,
        allowed={
            "operation_sequence",
            "origin_request_id",
            "parent_origin_request_id",
            "request_fingerprint",
            "role",
        },
        required={
            "operation_sequence",
            "origin_request_id",
            "request_fingerprint",
            "role",
        },
    )
    if _ORIGIN_ID.fullmatch(_string(operation["origin_request_id"])) is None:
        _fail("invalid_corpus")
    _integer(operation["operation_sequence"], minimum=1)
    _string(operation["role"])
    if _HEX_64.fullmatch(_string(operation["request_fingerprint"])) is None:
        _fail("invalid_corpus")
    if (
        "parent_origin_request_id" in operation
        and _ORIGIN_ID.fullmatch(_string(operation["parent_origin_request_id"])) is None
    ):
        _fail("invalid_corpus")
    return operation


_LIMIT_KEYS = {
    "max_active_duration_ms",
    "max_artifact_chunk_bytes",
    "max_artifact_count",
    "max_artifact_total_bytes",
    "max_browser_actions",
    "max_browser_captures",
    "max_browser_evaluations",
    "max_browser_transfer_bytes",
    "max_execution_frames",
    "max_frame_bytes",
    "max_http_transfer_bytes",
    "max_in_flight_frames",
    "max_inline_body_bytes",
    "max_monitor_batches",
    "max_output_items",
    "max_retry_after_ms",
}


def _validate_limits(value: Any) -> dict[str, int]:
    limits = _object(value, allowed=_LIMIT_KEYS, required=_LIMIT_KEYS)
    result = {key: _integer(limits[key], minimum=1) for key in _LIMIT_KEYS}
    if result["max_inline_body_bytes"] > result["max_frame_bytes"]:
        _fail("invalid_corpus")
    return result


def _validate_request(value: Any) -> dict[str, Any]:
    request = _object(
        value,
        allowed={
            "attempt_id",
            "board_manifest",
            "contract_version",
            "deadline_rfc3339",
            "fencing_context",
            "input",
            "kind",
            "origin_operations",
            "origin_request_id",
            "request_id",
            "traceparent",
            "tracestate",
        },
        required={
            "attempt_id",
            "board_manifest",
            "contract_version",
            "deadline_rfc3339",
            "fencing_context",
            "input",
            "kind",
            "origin_operations",
            "origin_request_id",
            "request_id",
        },
    )
    if request["contract_version"] != CONTRACT_VERSION:
        _fail("invalid_corpus")
    _string(request["request_id"])
    _string(request["attempt_id"])
    if request["kind"] not in {"monitor", "scrape", "browser"}:
        _fail("invalid_corpus")
    _parse_rfc3339(request["deadline_rfc3339"])
    _validate_trace(request.get("traceparent"), request.get("tracestate"))
    manifest = _object(
        request["board_manifest"],
        allowed={"config_fingerprint", "config_revision", "manifest_id"},
        required={"config_fingerprint", "config_revision", "manifest_id"},
    )
    for key in manifest:
        _string(manifest[key])
    input_value = _object(
        request["input"],
        allowed={"browser", "monitor", "scrape"},
        required={request["kind"]},
    )
    if len(input_value) != 1:
        _fail("invalid_corpus")
    if not isinstance(input_value[request["kind"]], dict):
        _fail("invalid_corpus")
    _validate_fence(request["fencing_context"])
    operations = request["origin_operations"]
    if not isinstance(operations, list) or not operations:
        _fail("invalid_corpus")
    validated = [_validate_operation(operation) for operation in operations]
    if request["origin_request_id"] != validated[0]["origin_request_id"]:
        _fail("invalid_corpus")
    seen_operation_ids: set[str] = set()
    for expected_sequence, operation in enumerate(validated, start=1):
        if operation["operation_sequence"] != expected_sequence:
            _fail("initial_origin_sequence_invalid")
        parent = operation.get("parent_origin_request_id")
        if parent is not None and parent not in seen_operation_ids:
            _fail("initial_origin_parent_unknown")
        seen_operation_ids.add(operation["origin_request_id"])
    return request


@dataclass
class LedgerEntry:
    operation: dict[str, Any]
    state: str = "declared"
    dispatch_sequence: int | None = None
    dedup_sequence: int | None = None

    def result(self) -> dict[str, Any]:
        return {
            "operation_sequence": self.operation["operation_sequence"],
            "origin_request_id": self.operation["origin_request_id"],
            "request_fingerprint": self.operation["request_fingerprint"],
            "state": self.state,
        }


@dataclass
class Counts:
    artifact_bytes: int = 0
    artifacts: int = 0
    deduplicated: int = 0
    dispatched: int = 0
    errors: int = 0
    frames: int = 0
    monitor_batches: int = 0
    outputs: int = 0
    replayed_frames: int = 0

    def result(self, ledger_size: int) -> dict[str, int]:
        return {
            "artifact_bytes": self.artifact_bytes,
            "artifacts": self.artifacts,
            "deduplicated": self.deduplicated,
            "declared": ledger_size,
            "dispatched": self.dispatched,
            "errors": self.errors,
            "frames": self.frames,
            "monitor_batches": self.monitor_batches,
            "outputs": self.outputs,
            "replayed_frames": self.replayed_frames,
        }


@dataclass
class ProtocolResult:
    accepted: bool
    binding_sha256: str
    case_id: str
    code: str
    counts: dict[str, int]
    ledger: list[dict[str, Any]]
    terminal: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "binding_sha256": self.binding_sha256,
            "case_id": self.case_id,
            "code": self.code,
            "counts": self.counts,
            "ledger": self.ledger,
            "terminal": self.terminal,
        }


@dataclass
class Validator:
    case_id: str
    request: dict[str, Any] = field(default_factory=dict)
    limits: dict[str, int] = field(default_factory=dict)
    accepted_at: datetime | None = None
    logical_time: datetime | None = None
    durable_cut_event_index: int = 0
    injection_phase: str = "none"
    initial_window: int = 0
    binding_sha256: str = ""
    current_attempt: str = ""
    attempts: set[str] = field(default_factory=set)
    credit: int = 0
    requested_limits: dict[str, int] | None = None
    pending_limits: dict[str, int] | None = None
    pending_window: int | None = None
    pending_accepted_at: datetime | None = None
    ledger: dict[str, LedgerEntry] = field(default_factory=dict)
    operation_sequences: set[int] = field(default_factory=set)
    artifact_handles: set[str] = field(default_factory=set)
    counts: Counts = field(default_factory=Counts)
    history: dict[int, dict[str, Any]] = field(default_factory=dict)
    last_sequence: int = -1
    replay_cursor: int | None = None
    replay_to: int = 0
    highest_acknowledged: int = -1
    terminal: dict[str, Any] | None = None
    cancelled: bool = False
    resumed: bool = False
    last_payload_type: str | None = None
    last_result_sequence: int | None = None
    last_physical_sequence: int = -1
    last_physical_payload_type: str | None = None

    @classmethod
    def create(cls, case: dict[str, Any]) -> Validator:
        metadata = case["metadata"]
        return cls(
            case_id=_string(case["id"]),
            logical_time=_parse_rfc3339(metadata["logical_time_rfc3339"]),
            durable_cut_event_index=_integer(metadata["durable_cut_event_index"]),
            injection_phase=_string(metadata["injection_phase"]),
        )

    def _add_initial_operation(self, operation: dict[str, Any]) -> None:
        operation_id = operation["origin_request_id"]
        sequence = operation["operation_sequence"]
        if operation_id in self.ledger or sequence in self.operation_sequences:
            _fail("origin_identity_reused")
        self.ledger[operation_id] = LedgerEntry(operation=operation)
        self.operation_sequences.add(sequence)
        if len(self.ledger) > LOCAL_MAX_ORIGIN_OPERATIONS:
            _fail("origin_local_cap_exceeded")

    def _result(self, accepted: bool, code: str) -> ProtocolResult:
        return ProtocolResult(
            accepted=accepted,
            binding_sha256=self.binding_sha256,
            case_id=self.case_id,
            code=code,
            counts=self.counts.result(len(self.ledger)),
            ledger=[
                entry.result()
                for entry in sorted(
                    self.ledger.values(),
                    key=lambda item: item.operation["operation_sequence"],
                )
            ],
            terminal=self.terminal,
        )

    def _deadline_expired(self) -> bool:
        return bool(
            self.request
            and self.logical_time is not None
            and self.logical_time > _parse_rfc3339(self.request["deadline_rfc3339"])
        )

    def _check_fence(self, fence: dict[str, Any]) -> None:
        expected = self.request["fencing_context"]
        if fence == expected:
            return
        if (
            fence.get("routing_epoch", 0) < expected["routing_epoch"]
            or fence.get("fence_digest") != expected["fence_digest"]
        ):
            _fail("stale_fence")
        _fail("binding_changed")

    def _check_start_binding(self, checkpoint: dict[str, Any]) -> None:
        original = self.request
        if (
            checkpoint["board_manifest"]["config_revision"]
            != original["board_manifest"]["config_revision"]
        ):
            _fail("manifest_revision_changed")
        original_deadline = _parse_rfc3339(original["deadline_rfc3339"])
        checkpoint_deadline = _parse_rfc3339(checkpoint["deadline_rfc3339"])
        if checkpoint_deadline < original_deadline:
            _fail("deadline_regression")
        if checkpoint.get("traceparent") != original.get("traceparent") or checkpoint.get(
            "tracestate"
        ) != original.get("tracestate"):
            _fail("trace_binding_changed")
        original_fence = original["fencing_context"]
        checkpoint_fence = checkpoint["fencing_context"]
        if checkpoint_fence != original_fence:
            if (
                checkpoint_fence["routing_epoch"] < original_fence["routing_epoch"]
                or checkpoint_fence["fence_digest"] != original_fence["fence_digest"]
            ):
                _fail("stale_fence")
            _fail("binding_changed")
        left = {key: value for key, value in original.items() if key != "attempt_id"}
        right = {key: value for key, value in checkpoint.items() if key != "attempt_id"}
        if left != right:
            _fail("binding_changed")

    def _handle_client_hello(self, value: Any) -> None:
        if self.requested_limits is not None or self.pending_limits is not None:
            _fail("invalid_corpus")
        hello = _object(
            value,
            allowed={"implementation", "requested_limits", "supported_contract_versions"},
            required={"implementation", "requested_limits", "supported_contract_versions"},
        )
        versions = hello["supported_contract_versions"]
        if (
            not isinstance(versions, list)
            or versions != [CONTRACT_VERSION]
            or hello["implementation"] not in {"python", "go"}
        ):
            _fail("invalid_corpus")
        self.requested_limits = _validate_limits(hello["requested_limits"])

    def _handle_server_hello(self, value: Any) -> None:
        hello = _object(
            value,
            allowed={
                "accepted_at_rfc3339",
                "accepted_limits",
                "implementation",
                "initial_window_frames",
                "resume_by_origin_request_id",
                "selected_contract_version",
            },
            required={
                "accepted_at_rfc3339",
                "accepted_limits",
                "implementation",
                "initial_window_frames",
                "resume_by_origin_request_id",
                "selected_contract_version",
            },
        )
        if (
            self.requested_limits is None
            or self.pending_limits is not None
            or hello["selected_contract_version"] != CONTRACT_VERSION
            or hello["implementation"] not in {"python", "go"}
            or _boolean(hello["resume_by_origin_request_id"]) is not True
        ):
            _fail("invalid_corpus")
        accepted = _validate_limits(hello["accepted_limits"])
        if any(accepted[key] > self.requested_limits[key] for key in _LIMIT_KEYS):
            _fail("invalid_corpus")
        window = _integer(hello["initial_window_frames"], minimum=1)
        if window > accepted["max_in_flight_frames"]:
            _fail("invalid_corpus")
        self.pending_limits = accepted
        self.pending_window = window
        self.pending_accepted_at = _parse_rfc3339(hello["accepted_at_rfc3339"])

    def _handle_start(self, value: Any) -> None:
        candidate = _validate_request(value)
        if self.request:
            self._check_start_binding(candidate)
            _fail("binding_changed")
        if (
            self.pending_limits is None
            or self.pending_window is None
            or self.pending_accepted_at is None
        ):
            _fail("invalid_corpus")
        deadline = _parse_rfc3339(candidate["deadline_rfc3339"])
        if deadline <= self.pending_accepted_at:
            _fail("invalid_deadline")
        duration_ms = int((deadline - self.pending_accepted_at).total_seconds() * 1000)
        if duration_ms > self.pending_limits["max_active_duration_ms"]:
            _fail("active_duration_limit_exceeded")
        self.request = candidate
        self.limits = self.pending_limits
        self.initial_window = self.pending_window
        self.accepted_at = self.pending_accepted_at
        if self.logical_time is None or self.logical_time < self.accepted_at:
            _fail("invalid_deadline")
        self.current_attempt = candidate["attempt_id"]
        self.attempts.add(self.current_attempt)
        self.credit = self.initial_window
        binding_value = {
            "negotiated_limits": self.limits,
            "request": {key: value for key, value in candidate.items() if key != "attempt_id"},
        }
        self.binding_sha256 = _sha256(binding_value)
        for operation in candidate["origin_operations"]:
            self._add_initial_operation(operation)
        self.requested_limits = None
        self.pending_limits = None
        self.pending_window = None
        self.pending_accepted_at = None

    def _handle_resume(self, value: Any) -> None:
        resume = _object(
            value,
            allowed={
                "after_sequence",
                "attempt_id",
                "contract_version",
                "fencing_context",
                "origin_request_id",
                "request_id",
            },
            required={
                "attempt_id",
                "contract_version",
                "fencing_context",
                "origin_request_id",
                "request_id",
            },
        )
        if (
            not self.request
            or self.pending_limits is None
            or self.pending_window is None
            or self.pending_accepted_at is None
        ):
            _fail("resume_handshake_missing")
        if self.pending_limits != self.limits:
            _fail("limits_changed")
        if resume["contract_version"] != CONTRACT_VERSION:
            _fail("binding_changed")
        if (
            resume["request_id"] != self.request["request_id"]
            or resume["origin_request_id"] != self.request["origin_request_id"]
        ):
            _fail("binding_changed")
        fence = _validate_fence(resume["fencing_context"])
        self._check_fence(fence)
        attempt = _string(resume["attempt_id"])
        if attempt in self.attempts:
            _fail("reused_attempt")
        after_value = resume.get("after_sequence")
        after_sequence = -1 if after_value is None else _integer(after_value)
        if after_sequence < self.highest_acknowledged:
            _fail("sequence_rewind")
        if after_sequence > self.last_sequence:
            _fail("unknown_checkpoint")
        self.current_attempt = attempt
        self.attempts.add(attempt)
        self.replay_cursor = after_sequence + 1 if after_sequence < self.last_sequence else None
        self.replay_to = self.last_sequence
        self.credit = self.pending_window
        self.resumed = True
        self.highest_acknowledged = after_sequence
        self.requested_limits = None
        self.pending_limits = None
        self.pending_window = None
        self.pending_accepted_at = None

    def _handle_window_update(self, value: Any) -> None:
        update = _object(
            value,
            allowed={"additional_frames", "attempt_id", "fence_digest", "request_id"},
            required={"additional_frames", "attempt_id", "fence_digest", "request_id"},
        )
        if (
            update["request_id"] != self.request["request_id"]
            or update["attempt_id"] != self.current_attempt
            or update["fence_digest"] != self.request["fencing_context"]["fence_digest"]
        ):
            _fail("stale_fence")
        additional = _integer(update["additional_frames"], minimum=1)
        if self.credit + additional > self.limits["max_in_flight_frames"]:
            _fail("credit_exceeded")
        self.credit += additional

    def _handle_cancel(self, value: Any) -> None:
        cancel = _object(
            value,
            allowed={"attempt_id", "fencing_context", "reason", "request_id"},
            required={"attempt_id", "fencing_context", "reason", "request_id"},
        )
        if (
            cancel["request_id"] != self.request["request_id"]
            or cancel["attempt_id"] != self.current_attempt
        ):
            _fail("binding_changed")
        self._check_fence(_validate_fence(cancel["fencing_context"]))
        _string(cancel["reason"])
        self.cancelled = True

    def _handle_client(self, event: dict[str, Any]) -> None:
        present = [
            key
            for key in ("client_hello", "start", "resume", "window_update", "cancel")
            if key in event
        ]
        if len(present) != 1:
            _fail("invalid_corpus")
        kind = present[0]
        if kind == "client_hello":
            self._handle_client_hello(event[kind])
        elif kind == "start":
            self._handle_start(event[kind])
        elif kind == "resume":
            self._handle_resume(event[kind])
        elif kind == "window_update":
            self._handle_window_update(event[kind])
        else:
            self._handle_cancel(event[kind])

    def _fault_operation(self, fault: dict[str, Any]) -> LedgerEntry | None:
        operation_id = _string(fault["origin_request_id"], allow_empty=True)
        fingerprint = _string(fault["request_fingerprint"], allow_empty=True)
        dispatched = _boolean(fault["origin_was_dispatched"])
        if not dispatched:
            if operation_id or fingerprint:
                _fail("fault_metadata_mismatch")
            return None
        entry = self.ledger.get(operation_id)
        if (
            entry is None
            or entry.state not in {"dispatched", "ambiguous"}
            or fingerprint != entry.operation["request_fingerprint"]
            or entry.dispatch_sequence is None
        ):
            _fail("fault_metadata_mismatch")
        return entry

    def _handle_fault(self, event: dict[str, Any]) -> None:
        fault = _object(
            event["fault"],
            allowed={
                "origin_request_id",
                "origin_was_dispatched",
                "point",
                "request_fingerprint",
                "sequence",
            },
            required={
                "origin_request_id",
                "origin_was_dispatched",
                "point",
                "request_fingerprint",
            },
        )
        if self.cancelled or self._deadline_expired():
            _fail("fault_metadata_mismatch")
        point = fault["point"]
        if point not in {"after_dispatch", "before_frame", "after_frame", "result_before_terminal"}:
            _fail("invalid_corpus")
        entry = self._fault_operation(fault)
        sequence = fault.get("sequence")
        if sequence is not None:
            sequence = _integer(sequence)
        if point == "after_dispatch":
            if (
                entry is None
                or entry.dispatch_sequence != self.last_physical_sequence
                or self.last_physical_payload_type != "origin_contact"
                or (sequence is not None and sequence != self.last_physical_sequence)
            ):
                _fail("fault_metadata_mismatch")
        elif point == "before_frame":
            if sequence is not None and sequence != self.last_physical_sequence + 1:
                _fail("fault_metadata_mismatch")
        elif point == "after_frame":
            if self.last_physical_sequence < 0 or (
                sequence is not None and sequence != self.last_physical_sequence
            ):
                _fail("fault_metadata_mismatch")
        else:
            if (
                self.last_physical_sequence < 0
                or (sequence is not None and sequence != self.last_physical_sequence)
                or self.last_physical_payload_type
                not in {"monitor_batch", "scrape_result", "browser_result"}
                or entry is not None
            ):
                _fail("fault_metadata_mismatch")
        if entry is not None:
            entry.state = "ambiguous"

    def _frame_signature(
        self, frame: dict[str, Any], measurements: dict[str, int]
    ) -> dict[str, Any]:
        return {
            "contract_version": frame["contract_version"],
            "fence_digest": frame["fence_digest"],
            "payload": frame["payload"],
            "request_id": frame["request_id"],
            "sequence": frame["sequence"],
            "measurements": measurements,
        }

    def _validate_frame_common(
        self, event: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, int], str]:
        frame = _object(
            event["frame"],
            allowed={
                "attempt_id",
                "contract_version",
                "fence_digest",
                "payload",
                "request_id",
                "sequence",
            },
            required={
                "attempt_id",
                "contract_version",
                "fence_digest",
                "payload",
                "request_id",
                "sequence",
            },
        )
        _integer(frame["sequence"])
        if (
            frame["contract_version"] != CONTRACT_VERSION
            or frame["request_id"] != self.request["request_id"]
            or frame["attempt_id"] != self.current_attempt
        ):
            _fail("binding_changed")
        if frame["fence_digest"] != self.request["fencing_context"]["fence_digest"]:
            _fail("stale_fence")
        measurements = _object(
            event["measurements"],
            allowed={"output_items", "wire_size_bytes"},
            required={"wire_size_bytes"},
        )
        wire_size = _integer(measurements["wire_size_bytes"], minimum=1)
        if wire_size > self.limits["max_frame_bytes"]:
            _fail("frame_size_limit_exceeded")
        if self.credit <= 0:
            _fail("credit_exceeded")
        payload = _object(
            frame["payload"],
            allowed={
                "active_duration_ms",
                "artifact",
                "artifact_count",
                "code",
                "disposition",
                "eligible_for_commit",
                "frame_count",
                "monitor_batches",
                "operation",
                "origin_operation_count",
                "output_items",
                "request_fingerprint",
                "status",
                "type",
            },
            required={"type"},
        )
        payload_type = _string(payload["type"])
        if payload_type == "monitor_batch":
            _integer(measurements.get("output_items"))
        elif "output_items" in measurements:
            _fail("invalid_corpus")
        return frame, measurements, payload_type

    def _add_dynamic_operation(self, payload: dict[str, Any]) -> None:
        operation = _validate_operation(payload.get("operation"))
        operation_id = operation["origin_request_id"]
        sequence = operation["operation_sequence"]
        existing = self.ledger.get(operation_id)
        if existing is not None:
            if existing.operation == operation:
                _fail("origin_identity_reused")
            _fail("origin_redeclaration_changed")
        if sequence in self.operation_sequences:
            _fail("origin_identity_reused")
        if sequence != max(self.operation_sequences, default=0) + 1:
            _fail("origin_identity_reused")
        parent = operation.get("parent_origin_request_id")
        if parent is not None and parent not in self.ledger:
            _fail("origin_identity_reused")
        self.ledger[operation_id] = LedgerEntry(operation=operation)
        self.operation_sequences.add(sequence)
        if len(self.ledger) > LOCAL_MAX_ORIGIN_OPERATIONS:
            _fail("origin_local_cap_exceeded")

    def _origin_contact(self, payload: dict[str, Any], sequence: int) -> None:
        operation = _validate_operation(payload.get("operation"))
        operation_id = operation["origin_request_id"]
        entry = self.ledger.get(operation_id)
        if entry is None:
            if operation["operation_sequence"] != max(self.operation_sequences, default=0) + 1:
                _fail("unknown_origin_contact")
            _fail("origin_dispatch_before_declaration")
        if operation["request_fingerprint"] != entry.operation["request_fingerprint"]:
            _fail("origin_fingerprint_changed")
        if operation != entry.operation:
            _fail("origin_redeclaration_changed")
        if payload.get("request_fingerprint") != entry.operation["request_fingerprint"]:
            _fail("origin_fingerprint_changed")
        disposition = payload.get("disposition")
        if disposition == "dispatched":
            if entry.state != "declared":
                _fail("duplicate_origin_dispatch")
            entry.state = "dispatched"
            entry.dispatch_sequence = sequence
            self.counts.dispatched += 1
        elif disposition == "deduplicated":
            if entry.state == "deduplicated":
                _fail("duplicate_logical_dedup")
            if not self.resumed or entry.state != "ambiguous":
                _fail("origin_deduplication_not_ambiguous")
            entry.state = "deduplicated"
            entry.dedup_sequence = sequence
            self.counts.deduplicated += 1
        else:
            _fail("invalid_corpus")

    def _apply_payload(
        self,
        payload: dict[str, Any],
        payload_type: str,
        sequence: int,
        measurements: dict[str, int],
    ) -> None:
        kind = self.request["kind"]
        if payload_type == "origin_operation_declared":
            self._add_dynamic_operation(payload)
        elif payload_type == "origin_contact":
            self._origin_contact(payload, sequence)
        elif payload_type == "monitor_batch":
            if kind != "monitor" or set(payload) != {"type"}:
                _fail("wrong_frame_kind")
            outputs = measurements["output_items"]
            self.counts.monitor_batches += 1
            self.counts.outputs += outputs
            self.last_result_sequence = sequence
            if self.counts.monitor_batches > self.limits["max_monitor_batches"]:
                _fail("output_limit_exceeded")
        elif payload_type == "scrape_result":
            if kind != "scrape" or self.last_result_sequence is not None:
                _fail("wrong_frame_kind")
            self.counts.outputs += 1
            self.last_result_sequence = sequence
        elif payload_type == "browser_result":
            if kind != "browser" or self.last_result_sequence is not None:
                _fail("wrong_frame_kind")
            self.counts.outputs += 1
            self.last_result_sequence = sequence
        elif payload_type == "artifact":
            artifact = _object(
                payload.get("artifact"),
                allowed={"handle", "size_bytes"},
                required={"handle", "size_bytes"},
            )
            handle = _string(artifact["handle"])
            if handle in self.artifact_handles:
                _fail("artifact_identity_reused")
            self.artifact_handles.add(handle)
            self.counts.artifacts += 1
            self.counts.artifact_bytes += _integer(artifact["size_bytes"])
            if self.counts.artifacts > self.limits["max_artifact_count"]:
                _fail("artifact_count_limit_exceeded")
            if self.counts.artifact_bytes > self.limits["max_artifact_total_bytes"]:
                _fail("artifact_total_bytes_limit_exceeded")
        elif payload_type == "error":
            if payload.get("code") not in _RUNTIME_ERROR_CODES:
                _fail("invalid_corpus")
            self.counts.errors += 1
            if self.counts.errors > LOCAL_MAX_ERRORS:
                _fail("error_local_cap_exceeded")
        elif payload_type == "terminal":
            self._validate_terminal(payload)
        else:
            _fail("invalid_corpus")
        if self.counts.outputs > self.limits["max_output_items"]:
            _fail("output_limit_exceeded")

    def _validate_terminal(self, payload: dict[str, Any]) -> None:
        required = {
            "active_duration_ms",
            "artifact_count",
            "eligible_for_commit",
            "frame_count",
            "monitor_batches",
            "origin_operation_count",
            "output_items",
            "status",
            "type",
        }
        if set(payload) != required:
            _fail("invalid_corpus")
        status = payload["status"]
        if status not in {"success", "error", "cancelled"}:
            _fail("invalid_corpus")
        observed_counts = {
            key: _integer(payload[key])
            for key in {
                "artifact_count",
                "frame_count",
                "monitor_batches",
                "origin_operation_count",
                "output_items",
            }
        }
        active_duration = _integer(payload["active_duration_ms"])
        expected = {
            "artifact_count": self.counts.artifacts,
            "frame_count": self.counts.frames,
            "monitor_batches": self.counts.monitor_batches,
            "origin_operation_count": len(self.ledger),
            "output_items": self.counts.outputs,
        }
        if observed_counts != expected:
            _fail("terminal_count_mismatch")
        if active_duration > self.limits["max_active_duration_ms"]:
            _fail("active_duration_limit_exceeded")
        unresolved = any(entry.state == "ambiguous" for entry in self.ledger.values())
        kind_complete = (self.request["kind"] == "monitor" and self.counts.monitor_batches > 0) or (
            self.request["kind"] in {"scrape", "browser"} and self.counts.outputs == 1
        )
        should_commit = (
            status == "success" and self.counts.errors == 0 and not unresolved and kind_complete
        )
        eligible = _boolean(payload["eligible_for_commit"])
        if eligible != should_commit:
            _fail("terminal_count_mismatch")
        if status == "success" and not should_commit:
            _fail("terminal_count_mismatch")
        if status == "error" and self.counts.errors == 0:
            _fail("terminal_count_mismatch")
        if (status == "cancelled") != self.cancelled:
            _fail("terminal_count_mismatch")
        self.terminal = {key: payload[key] for key in sorted(required - {"type"})}

    def _handle_server(self, event: dict[str, Any]) -> None:
        if "server_hello" in event:
            if set(event) != {"direction", "server_hello"}:
                _fail("invalid_corpus")
            self._handle_server_hello(event["server_hello"])
            return
        if not self.request:
            _fail("invalid_corpus")
        if set(event) != {"direction", "frame", "measurements"}:
            _fail("invalid_corpus")
        frame, measurements, payload_type = self._validate_frame_common(event)
        if self.cancelled and payload_type != "terminal":
            _fail("cancelled")
        sequence = frame["sequence"]
        payload = frame["payload"]
        if self.replay_cursor is not None:
            if sequence < self.replay_cursor:
                _fail("sequence_rewind")
            if sequence > self.replay_cursor:
                _fail("sequence_gap")
            signature = self._frame_signature(frame, measurements)
            if signature != self.history.get(sequence):
                _fail("divergent_sequence_reuse")
            self.credit -= 1
            self.counts.replayed_frames += 1
            self.last_physical_sequence = sequence
            self.last_physical_payload_type = payload_type
            if sequence == self.replay_to:
                self.replay_cursor = None
            else:
                self.replay_cursor += 1
            return
        if self.terminal is not None:
            if payload_type == "terminal":
                _fail("terminal_duplicate")
            _fail("frame_after_terminal")
        expected_sequence = self.last_sequence + 1
        if sequence < expected_sequence:
            _fail("sequence_rewind")
        if sequence > expected_sequence:
            _fail("sequence_gap")
        if (
            payload_type != "terminal"
            and self.counts.frames + 1 > self.limits["max_execution_frames"]
        ):
            _fail("frame_limit_exceeded")
        self.credit -= 1
        self._apply_payload(payload, payload_type, sequence, measurements)
        if payload_type != "terminal":
            self.counts.frames += 1
        self.last_sequence = sequence
        self.last_payload_type = payload_type
        self.last_physical_sequence = sequence
        self.last_physical_payload_type = payload_type
        self.history[sequence] = self._frame_signature(frame, measurements)

    def run(self, events: list[Any]) -> ProtocolResult:
        for event_index, raw_event in enumerate(events):
            event = _object(
                raw_event,
                allowed={
                    "cancel",
                    "client_hello",
                    "direction",
                    "fault",
                    "frame",
                    "measurements",
                    "resume",
                    "server_hello",
                    "start",
                    "window_update",
                },
                required={"direction"},
            )
            direction = event["direction"]
            if (
                self.request
                and event_index > self.durable_cut_event_index
                and direction != "fault"
                and self._deadline_expired()
            ):
                _fail("deadline_exceeded")
            if direction == "client":
                if (
                    "measurements" in event
                    or "frame" in event
                    or "fault" in event
                    or "server_hello" in event
                ):
                    _fail("invalid_corpus")
                self._handle_client(event)
            elif direction == "server":
                self._handle_server(event)
            elif direction == "fault":
                if set(event) != {"direction", "fault"}:
                    _fail("invalid_corpus")
                self._handle_fault(event)
            else:
                _fail("invalid_corpus")
        if self.replay_cursor is not None:
            _fail("sequence_gap")
        if self.terminal is None:
            _fail("terminal_missing")
        return self._result(True, "ok")


def validate_case(case: Any) -> ProtocolResult:
    case_id = "<invalid>"
    validator: Validator | None = None
    try:
        parsed = _object(
            case,
            allowed={
                "events",
                "expected",
                "id",
                "metadata",
            },
            required={
                "events",
                "expected",
                "id",
                "metadata",
            },
        )
        case_id = _string(parsed["id"])
        events = parsed["events"]
        if not isinstance(events, list):
            _fail("invalid_corpus")
        metadata = _object(
            parsed["metadata"],
            allowed={"durable_cut_event_index", "injection_phase", "logical_time_rfc3339"},
            required={"durable_cut_event_index", "injection_phase", "logical_time_rfc3339"},
        )
        cut = _integer(metadata["durable_cut_event_index"])
        _string(metadata["injection_phase"])
        _parse_rfc3339(metadata["logical_time_rfc3339"])
        if not events or cut >= len(events):
            _fail("invalid_corpus")
        cut_event = events[cut]
        if not isinstance(cut_event, dict) or cut_event.get("direction") == "fault":
            _fail("invalid_corpus")
        fault_indexes = [
            index
            for index, event in enumerate(events)
            if isinstance(event, dict) and event.get("direction") == "fault"
        ]
        phase = metadata["injection_phase"]
        if fault_indexes:
            first_fault = fault_indexes[0]
            if cut + 1 != first_fault:
                _fail("fixture_cut_mismatch")
            fault_event = events[first_fault]
            if (
                not isinstance(fault_event, dict)
                or not isinstance(fault_event.get("fault"), dict)
                or phase != fault_event["fault"].get("point")
            ):
                _fail("fixture_injection_phase_mismatch")
        elif phase == "none":
            if cut != 2 or "start" not in cut_event:
                _fail("fixture_cut_mismatch")
        elif phase == "cancel":
            if cut + 1 >= len(events) or "cancel" not in events[cut + 1]:
                _fail("fixture_injection_phase_mismatch")
        elif phase == "deadline":
            if cut + 1 >= len(events):
                _fail("fixture_cut_mismatch")
        else:
            _fail("fixture_injection_phase_mismatch")
        validator = Validator.create(parsed)
        return validator.run(events)
    except ProtocolFailure as error:
        if validator is None:
            return ProtocolResult(
                accepted=False,
                binding_sha256="",
                case_id=case_id,
                code=error.code,
                counts=Counts().result(0),
                ledger=[],
                terminal=None,
            )
        return validator._result(False, error.code)


def load_corpus(root: Path) -> dict[str, Any]:
    path = root / "fixtures" / "control" / "manifest.json"
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ProtocolFailure("invalid_corpus") from error
    if raw != _canonical_pretty(document):
        _fail("invalid_corpus")
    manifest = _object(
        document,
        allowed={"cases", "format", "required_case_ids"},
        required={"cases", "format", "required_case_ids"},
    )
    if manifest["format"] != FORMAT:
        _fail("invalid_corpus")
    required_ids = manifest["required_case_ids"]
    if (
        not isinstance(required_ids, list)
        or required_ids != sorted(MANDATORY_CASE_IDS)
        or len(set(required_ids)) != len(required_ids)
    ):
        _fail("invalid_corpus")
    cases = manifest["cases"]
    if not isinstance(cases, list) or not cases:
        _fail("invalid_corpus")
    ids: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            _fail("invalid_corpus")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            _fail("invalid_corpus")
        ids.append(case_id)
    if ids != sorted(ids) or len(set(ids)) != len(ids) or not set(ids) >= MANDATORY_CASE_IDS:
        _fail("invalid_corpus")
    expected_hash = (path.parent / "manifest.sha256").read_text(encoding="ascii")
    if expected_hash != f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n":
        _fail("invalid_corpus")
    return manifest


def _canonical_pretty(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, allow_nan=False, indent=2, sort_keys=True) + "\n"
    ).encode("ascii")


def validate_corpus(root: Path) -> list[ProtocolResult]:
    manifest = load_corpus(root)
    results: list[ProtocolResult] = []
    for case in manifest["cases"]:
        expected = _object(
            case["expected"],
            allowed={"accepted", "code"},
            required={"accepted", "code"},
        )
        expected_accepted = _boolean(expected["accepted"])
        expected_code = _string(expected["code"])
        if expected_code not in ERROR_CODES:
            _fail("invalid_corpus")
        result = validate_case(case)
        if result.accepted != expected_accepted or result.code != expected_code:
            raise AssertionError(
                f"{result.case_id}: expected {expected_accepted}/{expected_code}, "
                f"got {result.accepted}/{result.code}"
            )
        results.append(result)
    return results


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="runtime v1 contract root",
    )
    parser.add_argument("--json", action="store_true", help="emit deterministic result JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        results = validate_corpus(arguments.root.resolve())
    except (AssertionError, OSError, ProtocolFailure) as error:
        print(f"runtime v1 control protocol: failed: {error}", file=sys.stderr)
        return 1
    if arguments.json:
        print(
            json.dumps(
                [result.to_dict() for result in results], sort_keys=True, separators=(",", ":")
            )
        )
    else:
        print(f"runtime v1 control protocol: ok ({len(results)} cases)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
