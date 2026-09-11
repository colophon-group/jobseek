"""Pure validation and admission math for the frozen Stage B2 protocol.

The process/controller layer emits one normalized record for every scheduled
arm.  This module has no process, Docker, clock, or network access: given the
frozen schedule and 22 arm records, it either rejects malformed evidence or
derives the admission result with exact integer/Fraction comparisons.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from fractions import Fraction
from pathlib import Path
from typing import Any

GIB = 1024**3
EXPECTED_SCHEDULE_SHA256 = (
    "65698192e1264490f2e0ecbc8fbf8ca3a0180e702736f8a5a6a6bb08cdb5b820"
)

STATUSES = frozenset({"admitted", "not_admitted", "inconclusive", "invalid"})
REASON_IDS = frozenset(
    {
        "admission_thresholds_met",
        "memory_pressure_fallback_met",
        "schedule_invalid",
        "schedule_hash_invalid",
        "evidence_count_invalid",
        "evidence_shape_invalid",
        "evidence_order_invalid",
        "evidence_duplicate_invalid",
        "evidence_not_run",
        "protocol_integrity_failure",
        "c4_correctness_gate_failed",
        "c4_measurement_inconclusive",
        "go_c8_correctness_gate_failed",
        "go_c8_measurement_inconclusive",
        "python_c8_mixed_failures",
        "python_c8_memory_pressure_insufficient",
        "python_c8_memory_pressure_unconfirmed",
        "c8_ram_ceiling_unreached",
        "go_rate_median_below_threshold",
        "go_rate_wins_below_threshold",
        "go_memory_median_above_threshold",
        "go_memory_wins_below_threshold",
        "go_memory_pressure_peak_too_high",
    }
)

# These are normalized evidence IDs, not raw stderr or exception strings.
# The disjoint sets determine whether a complete arm can reject correctness,
# enter the memory-pressure branch, make measurement inconclusive, or
# invalidate the protocol. ``not_run`` preserves an unmeasured scheduled slot
# after an executor abort and also invalidates the protocol.
CORRECTNESS_FAILURE_IDS = frozenset(
    {
        "concurrency",
        "conservation",
        "oracle",
        "go_runner_invariant_failure",
    }
)
MEMORY_FAILURE_IDS = frozenset({"oom", "memory_pressure"})
PROTOCOL_FAILURE_IDS = frozenset({"cleanup", "containment", "process_residue"})
NOT_RUN_FAILURE_ID = "not_run"
INCONCLUSIVE_FAILURE_IDS = frozenset(
    {
        "command",
        "image",
        "inspect",
        "malformed_output",
        "nonzero",
        "missing_cgroup",
        "missing_cgroup_membership",
        "missing_cgroup_path",
        "missing_cgroup_pid",
        "missing_cgroup_memory_current",
        "missing_cgroup_memory_peak",
        "missing_cgroup_memory_max",
        "missing_cgroup_memory_swap_max",
        "missing_cgroup_memory_events",
        "missing_cgroup_cpu_max",
        "missing_cgroup_cpu_stat",
        "missing_cgroup_pids_max",
        "missing_cgroup_pids_events",
        "missing_cgroup_pids_current",
        "missing_cgroup_pids_peak",
        "missing_cgroup_cgroup_procs",
        "process_identity",
        "process_state",
        "start_timeout",
        "timeout",
        "transcript",
        "pid_limit",
        "fd_limit",
        "missing_artifact",
        "infrastructure",
        "start_failure",
        "cgroup_failure",
        "transcript_failure",
        "go_runner_arguments_invalid",
        "go_runner_start_gate",
        "go_runner_manifest_invalid",
        "go_runner_adapter_initialization",
        "go_runner_configuration_invalid",
        "go_runner_pool_initialization",
    }
)
FAILURE_IDS = (
    CORRECTNESS_FAILURE_IDS
    | MEMORY_FAILURE_IDS
    | INCONCLUSIVE_FAILURE_IDS
    | PROTOCOL_FAILURE_IDS
    | {NOT_RUN_FAILURE_ID}
)
GO_ONLY_FAILURE_IDS = frozenset(
    failure_id for failure_id in FAILURE_IDS if failure_id.startswith("go_runner_")
)

_RECORD_KEYS = {
    "schema_version",
    "pair_id",
    "arm_index",
    "implementation",
    "concurrency",
    "outcome",
    "failure_id",
    "elapsed_ns",
    "resources",
}
_RESOURCE_KEYS = {
    "final_sample",
    "observation",
    "memory_current",
    "memory_peak",
    "memory_limit",
    "memory_swap_limit",
    "host_page_size",
    "memory_events",
    "cpu_max",
    "cpu_stat",
    "pids_limit",
    "pids_current",
    "pids_peak",
    "pids_events",
    "cgroup_process_count",
}
_MEMORY_EVENT_KEYS = {"low", "high", "max", "oom", "oom_kill", "oom_group_kill"}
_PIDS_EVENT_KEYS = {"max"}
_CPU_STAT_REQUIRED_KEYS = {"usage_usec", "user_usec", "system_usec"}
_CPU_STAT_KEYS = _CPU_STAT_REQUIRED_KEYS | {
    "nr_periods",
    "nr_throttled",
    "throttled_usec",
    "nr_bursts",
    "burst_usec",
}


class ProtocolError(ValueError):
    """A bounded, public failure that is safe to copy to an evidence report."""

    def __init__(self, reason_id: str):
        if reason_id not in REASON_IDS:
            reason_id = "evidence_shape_invalid"
        self.reason_id = reason_id
        super().__init__(reason_id)


def _expected_schedule() -> dict[str, Any]:
    pairs: list[dict[str, Any]] = [
        {
            "id": "c1-p01",
            "concurrency": 1,
            "diagnostic": True,
            "order": ["python", "go"],
        }
    ]
    c4_orders = (
        ("go", "python"),
        ("python", "go"),
        ("go", "python"),
        ("python", "go"),
        ("go", "python"),
    )
    c8_orders = (
        ("python", "go"),
        ("go", "python"),
        ("python", "go"),
        ("go", "python"),
        ("python", "go"),
    )
    for concurrency, orders in ((4, c4_orders), (8, c8_orders)):
        for index, order in enumerate(orders, start=1):
            pairs.append(
                {
                    "id": f"c{concurrency}-p{index:02d}",
                    "concurrency": concurrency,
                    "diagnostic": False,
                    "order": list(order),
                }
            )
    return {
        "schema_version": 1,
        "protocol_id": "go-lightpanda-fixed-ram-density-b2-v1",
        "workload_sha256": "3db365d2a484b932049313d53469d07ffb2c5d9fdfe05821fd87cf67b1557740",
        "expected_arm_records": 22,
        "tasks_per_arm": 16,
        "resource_envelope": {
            "cpu_nanos": 1_000_000_000,
            "cpu_max": [100_000, 100_000],
            "memory_bytes": GIB,
            "memory_swap_bytes": 0,
            "host_page_size_min_bytes": 4096,
            "host_page_size_max_bytes": 65536,
            "pids_limit": 768,
            "nofile_soft": 256,
            "nofile_hard": 256,
        },
        "admission": {
            "c4_required_passes_per_implementation": 5,
            "go_c8_required_passes": 5,
            "go_rate_median_min": {"numerator": 5, "denominator": 4},
            "go_rate_wins_min": 4,
            "go_memory_median_max": {"numerator": 4, "denominator": 5},
            "go_memory_wins_min": 4,
            "ram_relevance_floor": {"numerator": 7, "denominator": 10},
            "python_memory_pressure_min": 3,
            "go_pressure_peak_max_exclusive": {"numerator": 4, "denominator": 5},
        },
        "pairs": pairs,
    }


def _strict_equal(value: Any, expected: Any) -> bool:
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(value) == set(expected) and all(
            _strict_equal(value[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(value) == len(expected) and all(
            _strict_equal(item, expected_item)
            for item, expected_item in zip(value, expected, strict=True)
        )
    return bool(value == expected)


def validate_schedule(schedule: Any) -> dict[str, Any]:
    """Require the exact frozen schedule, thresholds, and resource envelope."""

    if not isinstance(schedule, dict) or not _strict_equal(
        schedule, _expected_schedule()
    ):
        raise ProtocolError("schedule_invalid")
    return schedule


def load_schedule(path: Path) -> tuple[dict[str, Any], str]:
    """Load the frozen source file and attest its exact raw-byte digest."""

    try:
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != EXPECTED_SCHEDULE_SHA256:
            raise ProtocolError("schedule_hash_invalid")
        decoder = json.JSONDecoder(
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError())
        )
        text = raw.decode("utf-8", "strict")
        schedule, end = decoder.raw_decode(text.lstrip())
        if text.lstrip()[end:].strip():
            raise ValueError
    except ProtocolError:
        raise
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise ProtocolError("schedule_invalid") from None
    return validate_schedule(schedule), digest


def _integer(value: Any, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProtocolError("evidence_shape_invalid")
    return value


def _closed_object(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ProtocolError("evidence_shape_invalid")
    return value


def _validate_resources(value: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    resources = _closed_object(value, _RESOURCE_KEYS)
    if (
        not isinstance(resources["observation"], str)
        or resources["observation"] not in {"final", "sampled_not_final"}
        or resources["final_sample"] is not (resources["observation"] == "final")
    ):
        raise ProtocolError("evidence_shape_invalid")
    memory_current = _integer(resources["memory_current"])
    memory_peak = _integer(resources["memory_peak"], minimum=1)
    memory_limit = _integer(resources["memory_limit"], minimum=1)
    memory_swap_limit = _integer(resources["memory_swap_limit"])
    host_page_size = _integer(resources["host_page_size"], minimum=1)
    pids_limit = _integer(resources["pids_limit"], minimum=1)
    pids_current = _integer(resources["pids_current"])
    pids_peak = _integer(resources["pids_peak"], minimum=1)
    process_count = _integer(resources["cgroup_process_count"])
    if (
        memory_limit != envelope["memory_bytes"]
        or memory_swap_limit != envelope["memory_swap_bytes"]
        or host_page_size < envelope["host_page_size_min_bytes"]
        or host_page_size > envelope["host_page_size_max_bytes"]
        or host_page_size & (host_page_size - 1)
        or pids_limit != envelope["pids_limit"]
        or memory_current > envelope["memory_bytes"]
        or memory_current > memory_peak
        or memory_peak > envelope["memory_bytes"] + host_page_size
        or pids_current > envelope["pids_limit"]
        or pids_current > pids_peak
        or pids_peak > envelope["pids_limit"]
        or process_count > pids_current
        or process_count > envelope["pids_limit"]
    ):
        raise ProtocolError("evidence_shape_invalid")
    events = _closed_object(resources["memory_events"], _MEMORY_EVENT_KEYS)
    pids_events = _closed_object(resources["pids_events"], _PIDS_EVENT_KEYS)
    for count in (*events.values(), *pids_events.values()):
        _integer(count)
    cpu_max = resources["cpu_max"]
    if (
        not isinstance(cpu_max, list)
        or len(cpu_max) != 2
        or any(_integer(value, minimum=1) != value for value in cpu_max)
        or cpu_max != envelope["cpu_max"]
    ):
        raise ProtocolError("evidence_shape_invalid")
    cpu_stat = resources["cpu_stat"]
    if (
        not isinstance(cpu_stat, dict)
        or not _CPU_STAT_REQUIRED_KEYS <= set(cpu_stat) <= _CPU_STAT_KEYS
    ):
        raise ProtocolError("evidence_shape_invalid")
    for count in cpu_stat.values():
        _integer(count)
    return resources


def _validate_record(
    record: Any, pair: dict[str, Any], arm_index: int, envelope: dict[str, Any]
) -> dict[str, Any]:
    record = _closed_object(record, _RECORD_KEYS)
    implementation = pair["order"][arm_index]
    if (
        _integer(record["schema_version"], minimum=1) != 1
        or not isinstance(record["pair_id"], str)
        or record["pair_id"] != pair["id"]
        or _integer(record["arm_index"]) != arm_index
        or not isinstance(record["implementation"], str)
        or record["implementation"] != implementation
        or _integer(record["concurrency"], minimum=1) != pair["concurrency"]
        or not isinstance(record["outcome"], str)
        or record["outcome"] not in {"pass", "failure"}
    ):
        raise ProtocolError("evidence_order_invalid")
    if record["outcome"] == "pass":
        if record["failure_id"] is not None:
            raise ProtocolError("evidence_shape_invalid")
        _integer(record["elapsed_ns"], minimum=1)
        resources = _validate_resources(record["resources"], envelope)
        events = resources["memory_events"]
        if (
            resources["observation"] != "final"
            or resources["cgroup_process_count"] != 1
            or resources["memory_peak"] > envelope["memory_bytes"]
            or events["oom"]
            or events["oom_kill"]
            or events["oom_group_kill"]
            or resources["pids_events"]["max"]
        ):
            raise ProtocolError("evidence_shape_invalid")
    else:
        if (
            not isinstance(record["failure_id"], str)
            or record["failure_id"] not in FAILURE_IDS
            or (implementation != "go" and record["failure_id"] in GO_ONLY_FAILURE_IDS)
            or record["elapsed_ns"] is not None
            or (
                record["failure_id"] == NOT_RUN_FAILURE_ID
                and record["resources"] is not None
            )
            or (
                record["failure_id"] == "pid_limit"
                and record["resources"] is None
            )
        ):
            raise ProtocolError("evidence_shape_invalid")
        if record["resources"] is not None:
            resources = _validate_resources(record["resources"], envelope)
            pid_pressure = resources["pids_events"]["max"] > 0
            if pid_pressure is not (record["failure_id"] == "pid_limit"):
                raise ProtocolError("evidence_shape_invalid")
            if (
                resources["memory_peak"] > envelope["memory_bytes"]
                and record["failure_id"] not in MEMORY_FAILURE_IDS
                and not (
                    record["failure_id"] == "pid_limit"
                    and any(
                        resources["memory_events"][key] > 0
                        for key in ("max", "oom", "oom_kill", "oom_group_kill")
                    )
                )
            ):
                raise ProtocolError("evidence_shape_invalid")
    return record


def _index_records(
    schedule: dict[str, Any], records: Any
) -> dict[tuple[str, str], dict[str, Any]]:
    if (
        not isinstance(records, list)
        or len(records) != schedule["expected_arm_records"]
    ):
        raise ProtocolError("evidence_count_invalid")
    expected_positions = [
        (pair, arm_index) for pair in schedule["pairs"] for arm_index in range(2)
    ]
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for record, (pair, arm_index) in zip(records, expected_positions, strict=True):
        validated = _validate_record(
            record, pair, arm_index, schedule["resource_envelope"]
        )
        key = (validated["pair_id"], validated["implementation"])
        if key in result:
            raise ProtocolError("evidence_duplicate_invalid")
        result[key] = validated
    page_sizes = {
        record["resources"]["host_page_size"]
        for record in result.values()
        if record["resources"] is not None
    }
    if any(record["failure_id"] in PROTOCOL_FAILURE_IDS for record in result.values()):
        raise ProtocolError("protocol_integrity_failure")
    if any(record["failure_id"] == NOT_RUN_FAILURE_ID for record in result.values()):
        raise ProtocolError("evidence_not_run")
    if len(page_sizes) != 1:
        raise ProtocolError("evidence_shape_invalid")
    return result


def _fraction_json(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


def _median(values: Iterable[Fraction]) -> Fraction:
    ordered = sorted(values)
    if len(ordered) != 5:
        raise ProtocolError("evidence_shape_invalid")
    return ordered[2]


def _kernel_memory_pressure(record: dict[str, Any]) -> bool:
    resources = record["resources"]
    if resources is None:
        return False
    events = resources["memory_events"]
    return any(events[key] > 0 for key in ("max", "oom", "oom_kill", "oom_group_kill"))


def _result(
    schedule: dict[str, Any] | None,
    status: str,
    reason_ids: list[str],
    *,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if (
        status not in STATUSES
        or not reason_ids
        or any(reason not in REASON_IDS for reason in reason_ids)
    ):
        raise AssertionError("internal evidence result is not closed")
    result: dict[str, Any] = {
        "schema_version": 1,
        "protocol_id": schedule["protocol_id"] if schedule is not None else None,
        "status": status,
        "reason_ids": reason_ids,
        "metrics": metrics or {},
    }
    # This catches accidental Fraction/float leakage before callers serialize it.
    json.dumps(result, allow_nan=False, sort_keys=True)
    return result


def _failed(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record["outcome"] != "pass"]


def _gate_failure_result(
    schedule: dict[str, Any],
    failures: list[dict[str, Any]],
    correctness_reason: str,
    inconclusive_reason: str,
) -> dict[str, Any]:
    # A correctness failure is conclusive only when every gate failure is a
    # correctness failure. Mixed failure modes do not support attribution.
    if failures and all(
        record["failure_id"] in CORRECTNESS_FAILURE_IDS for record in failures
    ):
        return _result(schedule, "not_admitted", [correctness_reason])
    return _result(schedule, "inconclusive", [inconclusive_reason])


def summarize_evidence(schedule: Any, records: Any) -> dict[str, Any]:
    """Validate complete evidence and apply the frozen Stage B2 admission rule."""

    try:
        schedule = validate_schedule(schedule)
        indexed = _index_records(schedule, records)
    except ProtocolError as error:
        return _result(
            schedule
            if isinstance(schedule, dict)
            and schedule.get("protocol_id") == _expected_schedule()["protocol_id"]
            else None,
            "invalid",
            [error.reason_id],
        )

    c4 = [
        indexed[(pair["id"], implementation)]
        for pair in schedule["pairs"]
        if pair["concurrency"] == 4
        for implementation in ("go", "python")
    ]
    c4_failures = _failed(c4)
    if c4_failures:
        return _gate_failure_result(
            schedule,
            c4_failures,
            "c4_correctness_gate_failed",
            "c4_measurement_inconclusive",
        )

    go_c8 = [
        indexed[(pair["id"], "go")]
        for pair in schedule["pairs"]
        if pair["concurrency"] == 8
    ]
    go_c8_failures = _failed(go_c8)
    if go_c8_failures:
        return _gate_failure_result(
            schedule,
            go_c8_failures,
            "go_c8_correctness_gate_failed",
            "go_c8_measurement_inconclusive",
        )

    python_c8 = [
        indexed[(pair["id"], "python")]
        for pair in schedule["pairs"]
        if pair["concurrency"] == 8
    ]
    python_c8_failures = _failed(python_c8)
    admission = schedule["admission"]

    if not python_c8_failures:
        rate_ratios: list[Fraction] = []
        memory_ratios: list[Fraction] = []
        go_peaks: list[int] = []
        python_peaks: list[int] = []
        for pair in (pair for pair in schedule["pairs"] if pair["concurrency"] == 8):
            go_record = indexed[(pair["id"], "go")]
            python_record = indexed[(pair["id"], "python")]
            rate_ratios.append(
                Fraction(python_record["elapsed_ns"], go_record["elapsed_ns"])
            )
            go_peak = go_record["resources"]["memory_peak"]
            python_peak = python_record["resources"]["memory_peak"]
            go_peaks.append(go_peak)
            python_peaks.append(python_peak)
            memory_ratios.append(Fraction(go_peak, python_peak))

        median_rate = _median(rate_ratios)
        median_memory = _median(memory_ratios)
        median_go_peak = sorted(go_peaks)[2]
        median_python_peak = sorted(python_peaks)[2]
        rate_wins = sum(ratio > 1 for ratio in rate_ratios)
        memory_wins = sum(ratio < 1 for ratio in memory_ratios)
        metrics = {
            "branch": "paired_c8",
            "rate_ratios": [_fraction_json(value) for value in rate_ratios],
            "median_rate_ratio": _fraction_json(median_rate),
            "rate_wins": rate_wins,
            "memory_ratios": [_fraction_json(value) for value in memory_ratios],
            "median_memory_ratio": _fraction_json(median_memory),
            "memory_wins": memory_wins,
            "median_go_memory_peak": median_go_peak,
            "median_python_memory_peak": median_python_peak,
        }
        floor = Fraction(
            admission["ram_relevance_floor"]["numerator"],
            admission["ram_relevance_floor"]["denominator"],
        )
        if (
            Fraction(median_go_peak, GIB) < floor
            and Fraction(median_python_peak, GIB) < floor
        ):
            return _result(
                schedule, "inconclusive", ["c8_ram_ceiling_unreached"], metrics=metrics
            )

        reasons: list[str] = []
        rate_min = Fraction(
            admission["go_rate_median_min"]["numerator"],
            admission["go_rate_median_min"]["denominator"],
        )
        memory_max = Fraction(
            admission["go_memory_median_max"]["numerator"],
            admission["go_memory_median_max"]["denominator"],
        )
        if median_rate < rate_min:
            reasons.append("go_rate_median_below_threshold")
        if rate_wins < admission["go_rate_wins_min"]:
            reasons.append("go_rate_wins_below_threshold")
        if median_memory > memory_max:
            reasons.append("go_memory_median_above_threshold")
        if memory_wins < admission["go_memory_wins_min"]:
            reasons.append("go_memory_wins_below_threshold")
        if reasons:
            return _result(schedule, "not_admitted", reasons, metrics=metrics)
        return _result(
            schedule, "admitted", ["admission_thresholds_met"], metrics=metrics
        )

    if any(
        record["failure_id"] not in MEMORY_FAILURE_IDS for record in python_c8_failures
    ):
        return _result(schedule, "inconclusive", ["python_c8_mixed_failures"])
    if len(python_c8_failures) < admission["python_memory_pressure_min"]:
        return _result(
            schedule, "inconclusive", ["python_c8_memory_pressure_insufficient"]
        )
    confirmed_pressure = sum(
        _kernel_memory_pressure(record) for record in python_c8_failures
    )
    if confirmed_pressure < admission["python_memory_pressure_min"]:
        return _result(
            schedule, "inconclusive", ["python_c8_memory_pressure_unconfirmed"]
        )

    go_peaks = [record["resources"]["memory_peak"] for record in go_c8]
    peak_limit = Fraction(
        schedule["resource_envelope"]["memory_bytes"]
        * admission["go_pressure_peak_max_exclusive"]["numerator"],
        admission["go_pressure_peak_max_exclusive"]["denominator"],
    )
    metrics = {
        "branch": "python_memory_pressure",
        "python_memory_pressure_failures": len(python_c8_failures),
        "python_kernel_confirmed_pressure_failures": confirmed_pressure,
        "go_memory_peaks": go_peaks,
        "go_peak_limit_exclusive": _fraction_json(peak_limit),
    }
    if any(Fraction(peak, 1) >= peak_limit for peak in go_peaks):
        return _result(
            schedule,
            "not_admitted",
            ["go_memory_pressure_peak_too_high"],
            metrics=metrics,
        )
    return _result(
        schedule, "admitted", ["memory_pressure_fallback_met"], metrics=metrics
    )
