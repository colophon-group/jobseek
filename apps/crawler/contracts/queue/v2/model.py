"""Deterministic, offline queue protocol v2 reference state machine.

This module is deliberately not imported by the production crawler.  It freezes
fencing and conservation semantics for later Redis/Lua and Postgres adapters.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

FORMAT = "jobseek.queue.v2.conformance/v1"
ENGINE_OWNERS = frozenset({"python", "go"})
STATES = frozenset({"ready", "inflight", "dead_letter", "terminal"})
OPERATION_KEYS = {
    "claim": frozenset({"kind", "task_id", "fence", "lease_until"}),
    "heartbeat": frozenset({"kind", "task_id", "fence", "lease_until"}),
    "authorize_write": frozenset({"kind", "task_id", "fence"}),
    "complete": frozenset({"kind", "task_id", "fence"}),
    "reschedule": frozenset({"kind", "task_id", "fence"}),
    "reap": frozenset({"kind", "task_id", "fence", "now", "max_failures"}),
    "fail": frozenset({"kind", "task_id", "fence", "max_failures"}),
}
FENCE_KEYS = frozenset(
    {"shard_id", "routing_epoch", "engine_owner", "config_revision", "claim_token"}
)
ROUTE_KEYS = frozenset({"shard_id", "routing_epoch", "engine_owner"})
RECORD_KEYS = frozenset(
    {
        "task_id",
        "state",
        "shard_id",
        "routing_epoch",
        "engine_owner",
        "config_revision",
        "claim_token",
        "lease_until",
        "failures",
    }
)
SNAPSHOT_KEYS = frozenset({"route", "configs", "issued_tokens", "records"})
CASE_KEYS = frozenset({"id", "initial", "operations"})


class ContractError(ValueError):
    """Raised when a corpus or state-machine input violates the contract."""


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON: {exc}") from exc


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _expect_exact_keys(value: dict[str, Any], expected: frozenset[str], context: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ContractError(f"{context}: keys missing={missing} unknown={unknown}")


def _expect_nonempty_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError(f"{context}: expected non-empty string")
    return value


def _expect_positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(f"{context}: expected positive integer")
    return value


def _expect_nonnegative_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{context}: expected non-negative integer")
    return value


def _validate_route(route: Any, context: str) -> None:
    if not isinstance(route, dict):
        raise ContractError(f"{context}: expected object")
    _expect_exact_keys(route, ROUTE_KEYS, context)
    _expect_nonempty_string(route["shard_id"], f"{context}.shard_id")
    _expect_positive_int(route["routing_epoch"], f"{context}.routing_epoch")
    if route["engine_owner"] not in ENGINE_OWNERS:
        raise ContractError(f"{context}.engine_owner: unsupported owner")


def _validate_fence(fence: Any, context: str) -> None:
    if not isinstance(fence, dict):
        raise ContractError(f"{context}: expected object")
    _expect_exact_keys(fence, FENCE_KEYS, context)
    _expect_nonempty_string(fence["shard_id"], f"{context}.shard_id")
    _expect_positive_int(fence["routing_epoch"], f"{context}.routing_epoch")
    if fence["engine_owner"] not in ENGINE_OWNERS:
        raise ContractError(f"{context}.engine_owner: unsupported owner")
    _expect_positive_int(fence["config_revision"], f"{context}.config_revision")
    _expect_nonempty_string(fence["claim_token"], f"{context}.claim_token")


def _validate_record(record: Any, context: str) -> None:
    if not isinstance(record, dict):
        raise ContractError(f"{context}: expected object")
    _expect_exact_keys(record, RECORD_KEYS, context)
    _expect_nonempty_string(record["task_id"], f"{context}.task_id")
    if record["state"] not in STATES:
        raise ContractError(f"{context}.state: unsupported state")
    _expect_nonempty_string(record["shard_id"], f"{context}.shard_id")
    _expect_positive_int(record["routing_epoch"], f"{context}.routing_epoch")
    if record["engine_owner"] not in ENGINE_OWNERS:
        raise ContractError(f"{context}.engine_owner: unsupported owner")
    _expect_positive_int(record["config_revision"], f"{context}.config_revision")
    if record["claim_token"] is not None:
        _expect_nonempty_string(record["claim_token"], f"{context}.claim_token")
    if record["lease_until"] is not None:
        _expect_nonnegative_int(record["lease_until"], f"{context}.lease_until")
    _expect_nonnegative_int(record["failures"], f"{context}.failures")


def validate_snapshot(snapshot: Any, context: str = "snapshot") -> None:
    if not isinstance(snapshot, dict):
        raise ContractError(f"{context}: expected object")
    _expect_exact_keys(snapshot, SNAPSHOT_KEYS, context)
    _validate_route(snapshot["route"], f"{context}.route")
    configs = snapshot["configs"]
    if not isinstance(configs, dict):
        raise ContractError(f"{context}.configs: expected object")
    for task_id, revision in configs.items():
        _expect_nonempty_string(task_id, f"{context}.configs key")
        _expect_positive_int(revision, f"{context}.configs[{task_id}]")
    issued_tokens = snapshot["issued_tokens"]
    if not isinstance(issued_tokens, list):
        raise ContractError(f"{context}.issued_tokens: expected array")
    for index, token in enumerate(issued_tokens):
        _expect_nonempty_string(token, f"{context}.issued_tokens[{index}]")
    records = snapshot["records"]
    if not isinstance(records, list):
        raise ContractError(f"{context}.records: expected array")
    for index, record in enumerate(records):
        _validate_record(record, f"{context}.records[{index}]")


def validate_operation(operation: Any, context: str) -> None:
    if not isinstance(operation, dict):
        raise ContractError(f"{context}: expected object")
    kind = operation.get("kind")
    if kind not in OPERATION_KEYS:
        raise ContractError(f"{context}.kind: unsupported operation")
    _expect_exact_keys(operation, OPERATION_KEYS[kind], context)
    _expect_nonempty_string(operation["task_id"], f"{context}.task_id")
    _validate_fence(operation["fence"], f"{context}.fence")
    if kind in {"claim", "heartbeat"}:
        _expect_nonnegative_int(operation["lease_until"], f"{context}.lease_until")
    if kind == "reap":
        _expect_nonnegative_int(operation["now"], f"{context}.now")
    if kind in {"reap", "fail"}:
        _expect_positive_int(operation["max_failures"], f"{context}.max_failures")


def validate_case(case: Any, context: str = "case") -> None:
    if not isinstance(case, dict):
        raise ContractError(f"{context}: expected object")
    _expect_exact_keys(case, CASE_KEYS, context)
    _expect_nonempty_string(case["id"], f"{context}.id")
    validate_snapshot(case["initial"], f"{context}.initial")
    if not isinstance(case["operations"], list):
        raise ContractError(f"{context}.operations: expected array")
    for index, operation in enumerate(case["operations"]):
        validate_operation(operation, f"{context}.operations[{index}]")


def normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(snapshot)
    normalized["issued_tokens"].sort()
    normalized["records"].sort(
        key=lambda record: (
            record["task_id"],
            record["state"],
            record["claim_token"] or "",
            record["config_revision"],
            record["routing_epoch"],
        )
    )
    return normalized


def audit(snapshot: dict[str, Any]) -> dict[str, Any]:
    validate_snapshot(snapshot)
    route = snapshot["route"]
    configs = snapshot["configs"]
    records = snapshot["records"]
    counts = Counter(record["task_id"] for record in records)
    violations: list[dict[str, str]] = []

    def add(code: str, task_id: str, detail: str) -> None:
        violations.append({"code": code, "detail": detail, "task_id": task_id})

    for task_id in sorted(configs):
        count = counts.get(task_id, 0)
        if count == 0:
            add("loss", task_id, "configured task has no lifecycle record")
        elif count > 1:
            add("duplication", task_id, "task occupies multiple lifecycle records")

    token_counts = Counter(
        record["claim_token"]
        for record in records
        if record["state"] == "inflight" and record["claim_token"] is not None
    )
    issued_counts = Counter(snapshot["issued_tokens"])
    for token in sorted(issued_counts):
        if issued_counts[token] > 1:
            add("issued_token_duplication", token, "issued-token ledger contains a duplicate")
    for record in records:
        task_id = record["task_id"]
        if task_id not in configs:
            add("orphan_config", task_id, "lifecycle record has no configuration")
        elif record["config_revision"] != configs[task_id]:
            add(
                "config_revision_mismatch",
                task_id,
                "record revision differs from configured revision",
            )
        if record["shard_id"] != route["shard_id"]:
            add("shard_mismatch", task_id, "record shard differs from active route")
        if record["routing_epoch"] != route["routing_epoch"]:
            add("routing_epoch_mismatch", task_id, "record epoch differs from active route")
        if record["engine_owner"] != route["engine_owner"]:
            add("engine_owner_mismatch", task_id, "record owner differs from active route")
        if record["state"] == "inflight":
            if record["claim_token"] is None or record["lease_until"] is None:
                add("invalid_inflight", task_id, "inflight record lacks token or lease")
            elif record["claim_token"] not in issued_counts:
                add(
                    "unregistered_claim_token",
                    task_id,
                    "inflight token is absent from issued-token ledger",
                )
            elif token_counts[record["claim_token"]] > 1:
                add("token_collision", task_id, "claim token is reused by another task")
        elif record["claim_token"] is not None or record["lease_until"] is not None:
            add("invalid_non_inflight", task_id, "non-inflight record retains token or lease")

    violations.sort(key=lambda item: (item["code"], item["task_id"], item["detail"]))
    return {"ok": not violations, "violations": violations}


def _records_for(snapshot: dict[str, Any], task_id: str) -> list[dict[str, Any]]:
    return [record for record in snapshot["records"] if record["task_id"] == task_id]


def _fence_reason(
    snapshot: dict[str, Any], record: dict[str, Any], fence: dict[str, Any], *, claim: bool
) -> str | None:
    route = snapshot["route"]
    if any(fence[key] != route[key] for key in ("shard_id", "routing_epoch", "engine_owner")):
        return "route_mismatch"
    configured_revision = snapshot["configs"].get(record["task_id"])
    if configured_revision is None:
        return "config_missing"
    if fence["config_revision"] != configured_revision:
        return "config_revision_mismatch"
    if any(
        record[key] != fence[key]
        for key in ("shard_id", "routing_epoch", "engine_owner", "config_revision")
    ):
        return "record_fence_mismatch"
    if not claim and record["claim_token"] != fence["claim_token"]:
        return "claim_mismatch"
    return None


def _set_partition(record: dict[str, Any], state: str) -> None:
    record["state"] = state
    record["claim_token"] = None
    record["lease_until"] = None


def _apply(snapshot: dict[str, Any], operation: dict[str, Any]) -> tuple[str, str, bool]:
    task_id = operation["task_id"]
    kind = operation["kind"]
    records = _records_for(snapshot, task_id)
    if len(records) != 1:
        return "rejected", "state_not_unique", False
    record = records[0]
    fence = operation["fence"]

    if kind == "claim":
        if record["state"] != "ready":
            return "rejected", "not_ready", False
        reason = _fence_reason(snapshot, record, fence, claim=True)
        if reason is not None:
            return "fenced", reason, False
        if fence["claim_token"] in snapshot["issued_tokens"]:
            return "rejected", "claim_token_reused", False
        record["state"] = "inflight"
        record["claim_token"] = fence["claim_token"]
        record["lease_until"] = operation["lease_until"]
        snapshot["issued_tokens"].append(fence["claim_token"])
        return "accepted", "claimed", False

    if record["state"] != "inflight":
        return "rejected", "not_inflight", False
    reason = _fence_reason(snapshot, record, fence, claim=False)
    if reason is not None:
        return "fenced", reason, False

    if kind == "heartbeat":
        if operation["lease_until"] <= record["lease_until"]:
            return "rejected", "lease_not_extended", False
        record["lease_until"] = operation["lease_until"]
        return "accepted", "lease_extended", False
    if kind == "authorize_write":
        return "accepted", "write_authorized", True
    if kind == "complete":
        _set_partition(record, "terminal")
        return "accepted", "completed", False
    if kind == "reschedule":
        _set_partition(record, "ready")
        return "accepted", "rescheduled", False
    if kind == "reap" and operation["now"] < record["lease_until"]:
        return "rejected", "lease_not_expired", False
    if kind in {"reap", "fail"}:
        record["failures"] += 1
        if record["failures"] >= operation["max_failures"]:
            _set_partition(record, "dead_letter")
            return "accepted", "dead_lettered", False
        _set_partition(record, "ready")
        return "accepted", "requeued", False
    raise AssertionError(f"validated operation not implemented: {kind}")


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    validate_case(case)
    snapshot = copy.deepcopy(case["initial"])
    trace: list[dict[str, Any]] = []
    for index, operation in enumerate(case["operations"]):
        decision, reason, write_authorized = _apply(snapshot, operation)
        normalized = normalize_snapshot(snapshot)
        trace.append(
            {
                "decision": decision,
                "index": index,
                "kind": operation["kind"],
                "reason": reason,
                "snapshot_digest": digest(normalized),
                "write_authorized": write_authorized,
            }
        )
    final = normalize_snapshot(snapshot)
    return {"audit": audit(final), "final": final, "trace": trace}
