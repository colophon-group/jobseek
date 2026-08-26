"""Reproducible analytical and synthetic capacity evidence for issue #7936.

The analytical model deliberately does not impersonate a Go/Redis/Postgres/
Lightpanda benchmark. It turns the reviewed envelope into executable
arithmetic and hard assertions. The routing benchmark separately measures
the deterministic, streaming ownership/recovery algorithm at the full board
cardinality without allocating one Python object per board.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import platform
import resource
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPEC = ROOT / "capacity" / "crawler" / "global-v1.json"
GIB = 1024**3
TIB = 1024**4


class CapacityError(AssertionError):
    """The checked-in capacity contract is internally inconsistent."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return the stable byte representation used for evidence digests."""

    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def load_spec(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CapacityError("capacity spec must contain a JSON object")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CapacityError(message)


def _pct(numerator: float, denominator: float) -> float:
    return 100.0 * numerator / denominator


def _round(value: float, digits: int = 4) -> float:
    return round(value, digits)


def partition_for_board(board_id: str, logical_partitions: int) -> int:
    """Map a canonical board ID to one stable logical partition.

    The power-of-two modulo and exact byte order are part of global-v1.
    Production routing remains manifest driven: this function identifies the
    partition, while an epoch-fenced owner manifest identifies its cell/shard.
    """

    digest = hashlib.sha256(f"board:{board_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % logical_partitions


def primary_slot(partition: int, total_queue_shards: int) -> int:
    """Return the balanced v1 manifest slot for a logical partition."""

    return partition % total_queue_shards


def recovery_slot(
    partition: int,
    *,
    failed_slot: int,
    queue_shards_per_cell: int,
) -> int:
    """Pick a same-cell recovery shard with deterministic rendezvous hashing."""

    failed_cell = failed_slot // queue_shards_per_cell
    first_slot = failed_cell * queue_shards_per_cell
    candidates = range(first_slot, first_slot + queue_shards_per_cell)
    available = (slot for slot in candidates if slot != failed_slot)

    def score(slot: int) -> int:
        value = f"recovery:v1:{partition}:{slot}".encode()
        return int.from_bytes(hashlib.sha256(value).digest()[:8], "big")

    return max(available, key=score)


def recovery_partition_counts(
    spec: Mapping[str, Any], failed_slot: int = 0
) -> list[int]:
    topology = spec["topology"]
    logical_partitions = int(topology["logical_partitions"])
    total_shards = int(topology["data_cells"]) * int(topology["queue_shards_per_cell"])
    counts = [0] * total_shards
    for partition in range(logical_partitions):
        owner = primary_slot(partition, total_shards)
        if owner == failed_slot:
            owner = recovery_slot(
                partition,
                failed_slot=failed_slot,
                queue_shards_per_cell=int(topology["queue_shards_per_cell"]),
            )
        counts[owner] += 1
    return counts


def growth_owner(partition: int, capacity_weights: Sequence[float]) -> int:
    """Return a deterministic capacity-weighted rendezvous owner for a manifest."""

    _require(bool(capacity_weights), "growth manifest needs at least one cell")
    _require(
        all(weight > 0 for weight in capacity_weights), "cell weights must be positive"
    )
    best: tuple[float, int] | None = None
    for cell, weight in enumerate(capacity_weights):
        digest = hashlib.sha256(f"growth:v1:{partition}:{cell}".encode()).digest()
        uniform = (int.from_bytes(digest[:8], "big") + 1) / (2**64 + 1)
        score = float(weight) / -math.log(uniform)
        candidate = (score, -cell)
        if best is None or candidate > best:
            best = candidate
    assert best is not None
    return -best[1]


@dataclass(frozen=True)
class WorkClass:
    name: str
    cycles_per_hour: float
    browser_share: float
    worker_cpu_ms: float
    db_commits: float
    mean_response_bytes: float
    proxy_share: float


def _work_classes(spec: Mapping[str, Any]) -> tuple[WorkClass, WorkClass]:
    workload = spec["workload"]
    browser_bytes = float(workload["browser_transfer_mean_bytes"])
    monitor_browser_share = float(workload["monitor_browser_share"])
    detail_browser_share = float(workload["detail_browser_share"])
    return (
        WorkClass(
            name="board",
            cycles_per_hour=float(workload["monitor_cycles_per_hour"]),
            browser_share=monitor_browser_share,
            worker_cpu_ms=float(workload["monitor_worker_cpu_ms"]),
            db_commits=float(workload["monitor_db_commits_per_cycle"]),
            proxy_share=float(workload["monitor_proxy_share"]),
            mean_response_bytes=(
                (1 - monitor_browser_share)
                * float(workload["monitor_http_response_mean_bytes"])
                + monitor_browser_share * browser_bytes
            ),
        ),
        WorkClass(
            name="detail",
            cycles_per_hour=float(workload["detail_cycles_per_hour"]),
            browser_share=detail_browser_share,
            worker_cpu_ms=float(workload["detail_worker_cpu_ms"]),
            db_commits=float(workload["detail_db_commits_per_cycle"]),
            proxy_share=float(workload["detail_proxy_share"]),
            mean_response_bytes=(
                (1 - detail_browser_share)
                * float(workload["detail_http_response_mean_bytes"])
                + detail_browser_share * browser_bytes
            ),
        ),
    )


def _weighted(classes: Sequence[WorkClass], field: str) -> float:
    total = sum(item.cycles_per_hour for item in classes)
    return (
        sum(item.cycles_per_hour * float(getattr(item, field)) for item in classes)
        / total
    )


def _surviving_shards(
    scenario: Mapping[str, Any], tier: str, provisioned: int
) -> tuple[int, int]:
    lost = int(scenario.get("failed_shards_by_tier", {}).get(tier, 0))
    _require(lost >= 0, f"{tier} lost-shard count cannot be negative")
    _require(lost < provisioned, f"{tier} has no surviving shard")
    return provisioned - lost, lost


def _scenario_report(
    spec: Mapping[str, Any], name: str, classes: Sequence[WorkClass]
) -> dict[str, Any]:
    scenario = spec["scenarios"][name]
    topology = spec["topology"]
    profiles = spec["profiles"]
    workload = spec["workload"]
    cells = int(topology["data_cells"])
    queue_per_cell = int(topology["queue_shards_per_cell"])
    queue_shards = cells * queue_per_cell
    has_failure = bool(scenario.get("failed_shards_by_tier", {}))
    processing_rate = float(scenario["required_processing_cycles_per_second"])
    attempt_multiplier = 1 + float(workload["retry_attempts_per_success_max"])
    attempted_processing_rate = processing_rate * attempt_multiplier
    queue_shards_available, failed_queue_shards = _surviving_shards(
        scenario, "queue", queue_per_cell
    )
    recovery_factor = (
        float(topology["failed_board_recovery_max_load_factor"])
        if failed_queue_shards
        else 1.0
    )

    weighted_worker_ms = _weighted(classes, "worker_cpu_ms")
    weighted_browser_share = _weighted(classes, "browser_share")
    weighted_commits = _weighted(classes, "db_commits")
    weighted_bytes = _weighted(classes, "mean_response_bytes")
    browser_orchestration_ms = float(workload["browser_orchestration_cpu_ms"])

    queue_rate_per_peak_shard = (
        attempted_processing_rate / queue_shards * recovery_factor
    )
    queue_cpu_cores = (
        queue_rate_per_peak_shard
        * float(workload["queue_operations_per_cycle"])
        * float(workload["queue_cpu_us_per_operation"])
        / 1_000_000
    )
    queue_cpu_pct = _pct(
        queue_cpu_cores, float(profiles["queue"]["cpu_cores_per_primary"])
    )
    queue_entries_per_peak_shard = (
        float(scenario["max_total_queue_entries"]) / queue_shards * recovery_factor
    )
    queue_rss_gib = float(profiles["queue"]["baseline_rss_gib"]) + (
        queue_entries_per_peak_shard
        * float(workload["queue_bytes_per_task"])
        * float(workload["queue_allocator_multiplier"])
        / GIB
    )
    queue_rss_pct = _pct(queue_rss_gib, float(profiles["queue"]["memory_gib_per_node"]))
    queue_capacity_pct = _pct(
        queue_rate_per_peak_shard,
        float(profiles["queue"]["modeled_cycles_per_second_per_primary"]),
    )

    worker_cpu_seconds_per_cycle = (
        weighted_worker_ms + weighted_browser_share * browser_orchestration_ms
    ) / 1000
    worker_cpu_cores_per_cell = (
        attempted_processing_rate / cells * worker_cpu_seconds_per_cycle
    )
    worker_shards_provisioned = int(topology["worker_shards_per_cell"])
    worker_shards_available, failed_worker_shards = _surviving_shards(
        scenario, "worker", worker_shards_provisioned
    )
    worker_cpu_pct = _pct(
        worker_cpu_cores_per_cell,
        worker_shards_available * float(profiles["worker"]["cpu_cores_per_shard"]),
    )
    worker_rss_key = (
        "overload_rss_gib_per_shard_max"
        if has_failure
        else "steady_rss_gib_per_shard_max"
    )
    worker_rss_pct = _pct(
        float(profiles["worker"][worker_rss_key]),
        float(profiles["worker"]["memory_gib_per_shard"]),
    )

    browser_sessions_global = (
        attempted_processing_rate
        * weighted_browser_share
        * float(workload["browser_session_seconds_mean"])
    )
    browser_shards_provisioned = int(topology["browser_shards_per_cell"])
    browser_shards_available, failed_browser_shards = _surviving_shards(
        scenario, "browser", browser_shards_provisioned
    )
    browser_sessions_per_survivor = (
        browser_sessions_global / cells / browser_shards_available
    )
    browser_slot_pct = _pct(
        browser_sessions_per_survivor,
        float(profiles["browser"]["session_slots_per_shard"]),
    )
    browser_cpu_cores_per_cell = (
        attempted_processing_rate
        / cells
        * weighted_browser_share
        * float(workload["browser_cpu_ms"])
        / 1000
    )
    browser_cpu_pct = _pct(
        browser_cpu_cores_per_cell,
        browser_shards_available * float(profiles["browser"]["cpu_cores_per_shard"]),
    )
    browser_rss_gib = float(profiles["browser"]["baseline_rss_gib"]) + (
        browser_sessions_per_survivor
        * float(profiles["browser"]["rss_mib_per_session"])
        / 1024
    )
    browser_rss_pct = _pct(
        browser_rss_gib, float(profiles["browser"]["memory_gib_per_shard"])
    )

    postgres_writer_shards_provisioned = int(
        topology["postgres_writer_shards_per_cell"]
    )
    postgres_writer_shards, failed_postgres_shards = _surviving_shards(
        scenario, "postgres", postgres_writer_shards_provisioned
    )
    retry_commits_per_second = (attempted_processing_rate - processing_rate) * float(
        workload["retry_db_commits_per_failed_attempt"]
    )
    db_commits_per_second = (
        (processing_rate * weighted_commits + retry_commits_per_second)
        / cells
        / postgres_writer_shards
    )
    db_transaction_pct = _pct(
        db_commits_per_second,
        float(profiles["postgres"]["modeled_commits_per_second_per_writer"]),
    )
    db_cpu_pct = _pct(
        db_commits_per_second * float(workload["db_cpu_ms_per_commit"]) / 1000,
        float(profiles["postgres"]["cpu_cores_per_writer"]),
    )
    db_rss_key = (
        "overload_rss_gib_per_writer_max"
        if has_failure
        else "steady_rss_gib_per_writer_max"
    )
    db_rss_pct = _pct(
        float(profiles["postgres"][db_rss_key]),
        float(profiles["postgres"]["memory_gib_per_writer"]),
    )

    telemetry = profiles["telemetry"]
    telemetry_provisioned = int(topology["telemetry_collectors_per_cell"])
    telemetry_available, failed_telemetry_shards = _surviving_shards(
        scenario, "telemetry", telemetry_provisioned
    )
    telemetry_mode = "overload" if has_failure else "steady"
    telemetry_cpu_pct = _pct(
        float(telemetry[f"{telemetry_mode}_cpu_cores_per_cell"]),
        telemetry_available * float(telemetry["cpu_cores_per_collector"]),
    )
    telemetry_rss_pct = _pct(
        float(telemetry[f"{telemetry_mode}_working_set_gib_per_cell"]),
        telemetry_available * float(telemetry["memory_gib_per_collector"]),
    )

    typesense = profiles["typesense"]
    typesense_index_shards = int(topology["typesense_index_shards"])
    typesense_ha_groups = int(typesense["ha_replica_groups_per_index_shard"])
    typesense_nodes_per_ha_group = int(typesense["nodes_per_ha_replica_group"])
    typesense_nodes_provisioned = typesense_ha_groups * typesense_nodes_per_ha_group
    typesense_nodes, failed_typesense_nodes = _surviving_shards(
        scenario, "typesense", typesense_nodes_provisioned
    )
    typesense_upserts = (
        processing_rate
        / typesense_index_shards
        * float(workload["typesense_upserts_per_successful_cycle"])
    )
    # Every user search fans out to every independent index shard, so every
    # shard receives the full global query rate. Sharding distributes writes
    # and data, but it does not divide search work.
    typesense_search_qps = float(workload["typesense_search_qps_global"])
    # Each node contains the full index-shard replica. Writes are serialized by
    # the leader and applied by every replica; only reads spread across nodes.
    # Therefore the hottest surviving node carries all writes plus its read share.
    typesense_write_cpu_cores_per_node = (
        typesense_upserts * float(workload["typesense_upsert_cpu_ms"]) / 1000
    )
    typesense_search_cpu_cores_per_node = (
        typesense_search_qps
        / typesense_nodes
        * float(workload["typesense_search_cpu_ms"])
        / 1000
    )
    typesense_cpu_pct = _pct(
        typesense_write_cpu_cores_per_node + typesense_search_cpu_cores_per_node,
        float(typesense["cpu_cores_per_node"]),
    )
    typesense_write_pct = _pct(
        typesense_upserts,
        float(typesense["modeled_upserts_per_second_per_leader"]),
    )
    typesense_search_pct = _pct(
        typesense_search_qps,
        typesense_nodes * float(typesense["modeled_search_qps_per_node"]),
    )
    typesense_throughput_pct = max(typesense_write_pct, typesense_search_pct)
    typesense_working_set_gib_per_replica = (
        float(typesense["working_set_gib_global"]) / typesense_index_shards
    )
    typesense_rss_pct = _pct(
        typesense_working_set_gib_per_replica,
        float(typesense["memory_gib_per_node"]),
    )

    service_mode = "overload" if has_failure else "steady"
    cell_service = profiles["cell_service"]
    cell_service_provisioned = int(topology["cell_service_shards_per_cell"])
    cell_service_available, failed_cell_services = _surviving_shards(
        scenario, "cell_service", cell_service_provisioned
    )
    cell_service_cpu_pct = _pct(
        float(cell_service[f"{service_mode}_cpu_cores_per_cell"]),
        cell_service_available * float(cell_service["cpu_cores_per_shard"]),
    )
    cell_service_rss_pct = _pct(
        float(cell_service[f"{service_mode}_working_set_gib_per_cell"]),
        cell_service_available * float(cell_service["memory_gib_per_shard"]),
    )

    routing = profiles["routing"]
    routing_provisioned = int(topology["routing_shards_per_cell"])
    routing_available, failed_routing_shards = _surviving_shards(
        scenario, "routing", routing_provisioned
    )
    routing_cpu_pct = _pct(
        float(routing[f"{service_mode}_cpu_cores_per_cell"]),
        routing_available * float(routing["cpu_cores_per_shard"]),
    )
    routing_rss_pct = _pct(
        float(routing[f"{service_mode}_working_set_gib_per_cell"]),
        routing_available * float(routing["memory_gib_per_shard"]),
    )

    load_balancers_provisioned = int(topology["load_balancers_per_cell"])
    load_balancers_available, failed_load_balancers = _surviving_shards(
        scenario, "load_balancer", load_balancers_provisioned
    )
    load_balancer_throughput_pct = _pct(
        attempted_processing_rate / cells,
        load_balancers_available
        * float(profiles["load_balancer"]["modeled_requests_per_second_per_instance"]),
    )

    telemetry_backends_provisioned = int(topology["telemetry_backends"])
    telemetry_backends_available, failed_telemetry_backends = _surviving_shards(
        scenario, "telemetry_backend", telemetry_backends_provisioned
    )
    active_series = int(spec["storage_budgets"]["telemetry_active_series_global"])
    critical_series = int(topology["critical_telemetry_series_dual_written"])
    series_to_survivor = (
        active_series
        if failed_telemetry_backends
        else (active_series + critical_series) / telemetry_backends_available
    )
    telemetry_backend_series_pct = _pct(
        series_to_survivor,
        float(profiles["telemetry_backend"]["modeled_active_series_per_backend"]),
    )
    monthly_signal_gib = sum(
        int(spec["storage_budgets"][field])
        for field in (
            "telemetry_logs_gib_per_month",
            "telemetry_traces_gib_per_month",
            "telemetry_profiles_gib_per_month",
        )
    )
    critical_signal_gib = int(
        spec["storage_budgets"]["critical_telemetry_signal_gib_dual_written"]
    )
    signal_gib_to_survivor = (
        monthly_signal_gib
        if failed_telemetry_backends
        else (monthly_signal_gib + critical_signal_gib) / telemetry_backends_available
    )
    telemetry_backend_signal_pct = _pct(
        signal_gib_to_survivor,
        float(
            profiles["telemetry_backend"]["modeled_signal_gib_per_month_per_backend"]
        ),
    )

    network_gbps = (
        processing_rate
        * weighted_bytes
        * (1 + float(workload["retry_attempts_per_success_max"]))
        * 8
        / 1_000_000_000
    )
    arrival_cycles_per_hour = sum(item.cycles_per_hour for item in classes) * float(
        scenario["load_multiplier"]
    )
    arrival_cycles_per_second = arrival_cycles_per_hour / 3600
    due_backlog = arrival_cycles_per_second * float(scenario["due_burst_seconds"])
    catch_up_service = float(scenario["catch_up_service_cycles_per_second"])
    _require(
        catch_up_service > arrival_cycles_per_second,
        f"{name} catch-up service must exceed continuing arrivals",
    )
    backlog_drain_seconds = due_backlog / (catch_up_service - arrival_cycles_per_second)
    recovery_seconds = (
        sum(float(value) for value in topology["recovery_phases_seconds"].values())
        if failed_queue_shards
        else 0.0
    )
    oldest_due_seconds = recovery_seconds + backlog_drain_seconds
    computed_total_queue_entries = int(workload["configured_boards"] + due_backlog)
    worker_resident_payload_gib = (
        int(profiles["worker"]["concurrent_resident_payloads_per_shard_max"])
        * int(workload["max_artifact_chunk_bytes"])
        / GIB
    )
    browser_resident_payload_gib = (
        int(profiles["browser"]["concurrent_resident_artifact_chunks_per_shard_max"])
        * int(workload["max_artifact_chunk_bytes"])
        / GIB
    )
    _require(
        worker_resident_payload_gib <= float(profiles["worker"][worker_rss_key]),
        "worker resident artifact chunks exceed its RSS envelope",
    )
    _require(
        browser_resident_payload_gib
        <= float(profiles["browser"]["memory_gib_per_shard"]),
        "browser resident artifact chunks exceed its RSS envelope",
    )

    tiers = {
        "queue": {
            "peak_cpu_pct": _round(queue_cpu_pct),
            "peak_rss_pct": _round(queue_rss_pct),
            "modeled_throughput_pct": _round(queue_capacity_pct),
            "cycles_per_second_peak_survivor": _round(queue_rate_per_peak_shard),
            "entries_peak_survivor": math.ceil(queue_entries_per_peak_shard),
            "provisioned_shards_in_affected_cell": queue_per_cell,
            "lost_shards": failed_queue_shards,
            "surviving_shards_in_affected_cell": queue_shards_available,
        },
        "worker": {
            "peak_cpu_pct": _round(worker_cpu_pct),
            "peak_rss_pct": _round(worker_rss_pct),
            "resident_artifact_chunk_gib_max": _round(worker_resident_payload_gib),
            "provisioned_shards_in_affected_cell": worker_shards_provisioned,
            "lost_shards": failed_worker_shards,
            "surviving_shards_in_affected_cell": worker_shards_available,
        },
        "browser": {
            "peak_cpu_pct": _round(browser_cpu_pct),
            "peak_rss_pct": _round(browser_rss_pct),
            "session_slot_pct": _round(browser_slot_pct),
            "sessions_global": _round(browser_sessions_global),
            "sessions_peak_survivor": _round(browser_sessions_per_survivor),
            "resident_artifact_chunk_gib_max": _round(browser_resident_payload_gib),
            "provisioned_shards_in_affected_cell": browser_shards_provisioned,
            "lost_shards": failed_browser_shards,
            "surviving_shards_in_affected_cell": browser_shards_available,
        },
        "postgres": {
            "peak_cpu_pct": _round(db_cpu_pct),
            "peak_rss_pct": _round(db_rss_pct),
            "modeled_commit_throughput_pct": _round(db_transaction_pct),
            "commits_per_second_per_writer": _round(db_commits_per_second),
            "provisioned_shards_in_affected_cell": postgres_writer_shards_provisioned,
            "lost_shards": failed_postgres_shards,
            "surviving_shards_in_affected_cell": postgres_writer_shards,
        },
        "telemetry": {
            "peak_cpu_pct": _round(telemetry_cpu_pct),
            "peak_rss_pct": _round(telemetry_rss_pct),
            "provisioned_shards_in_affected_cell": telemetry_provisioned,
            "lost_shards": failed_telemetry_shards,
            "surviving_shards_in_affected_cell": telemetry_available,
        },
        "typesense": {
            "peak_cpu_pct": _round(typesense_cpu_pct),
            "peak_rss_pct": _round(typesense_rss_pct),
            "modeled_throughput_pct": _round(typesense_throughput_pct),
            "write_leader_utilization_pct_per_ha_group": _round(typesense_write_pct),
            "search_replica_utilization_pct": _round(typesense_search_pct),
            "replicated_write_cpu_cores_per_node": _round(
                typesense_write_cpu_cores_per_node
            ),
            "search_cpu_cores_per_surviving_node": _round(
                typesense_search_cpu_cores_per_node
            ),
            "working_set_gib_per_replica": _round(
                typesense_working_set_gib_per_replica
            ),
            "upserts_per_second_per_ha_group": _round(typesense_upserts),
            "search_qps_per_index_shard": _round(typesense_search_qps),
            "provisioned_shards_in_affected_index_shard": typesense_nodes_provisioned,
            "lost_shards": failed_typesense_nodes,
            "surviving_shards_in_affected_index_shard": typesense_nodes,
        },
        "cell_service": {
            "peak_cpu_pct": _round(cell_service_cpu_pct),
            "peak_rss_pct": _round(cell_service_rss_pct),
            "provisioned_shards_in_affected_cell": cell_service_provisioned,
            "lost_shards": failed_cell_services,
            "surviving_shards_in_affected_cell": cell_service_available,
        },
        "routing": {
            "peak_cpu_pct": _round(routing_cpu_pct),
            "peak_rss_pct": _round(routing_rss_pct),
            "provisioned_shards_in_affected_cell": routing_provisioned,
            "lost_shards": failed_routing_shards,
            "surviving_shards_in_affected_cell": routing_available,
        },
        "load_balancer": {
            "modeled_throughput_pct": _round(load_balancer_throughput_pct),
            "provisioned_shards_in_affected_cell": load_balancers_provisioned,
            "lost_shards": failed_load_balancers,
            "surviving_shards_in_affected_cell": load_balancers_available,
        },
        "telemetry_backend": {
            "modeled_throughput_pct": _round(
                max(telemetry_backend_series_pct, telemetry_backend_signal_pct)
            ),
            "modeled_series_throughput_pct": _round(telemetry_backend_series_pct),
            "modeled_signal_throughput_pct": _round(telemetry_backend_signal_pct),
            "provisioned_shards_global": telemetry_backends_provisioned,
            "lost_shards": failed_telemetry_backends,
            "surviving_shards_global": telemetry_backends_available,
        },
    }
    return {
        "arrival_cycles_per_hour": int(arrival_cycles_per_hour),
        "required_processing_cycles_per_second": _round(processing_rate),
        "attempted_processing_cycles_per_second": _round(attempted_processing_rate),
        "continuing_arrival_cycles_per_second": _round(arrival_cycles_per_second),
        "computed_policy_ready_queue_depth": math.ceil(due_backlog),
        "computed_total_queue_entries": computed_total_queue_entries,
        "computed_shard_recovery_seconds": _round(recovery_seconds),
        "computed_backlog_drain_seconds": _round(backlog_drain_seconds),
        "computed_oldest_policy_ready_due_seconds": _round(oldest_due_seconds),
        "network_gbps": _round(network_gbps),
        "tiers": tiers,
    }


def _storage_report(spec: Mapping[str, Any]) -> dict[str, Any]:
    workload = spec["workload"]
    topology = spec["topology"]
    budgets = spec["storage_budgets"]
    database_payload = int(workload["retained_postings"]) * int(
        workload["database_bytes_per_retained_posting"]
    ) + int(workload["configured_boards"]) * int(workload["database_bytes_per_board"])
    r2_payload = int(workload["retained_postings"]) * int(
        workload["description_object_mean_bytes"]
    )
    typesense_payload = int(workload["active_postings"]) * int(
        workload["typesense_bytes_per_active_posting"]
    )
    typesense_replicated = (
        typesense_payload
        * int(spec["profiles"]["typesense"]["ha_replica_groups_per_index_shard"])
        * int(spec["profiles"]["typesense"]["nodes_per_ha_replica_group"])
    )
    control_payload = (
        int(workload["configured_boards"]) * int(workload["catalog_bytes_per_board"])
        + int(workload["policy_keys"]) * int(workload["policy_bytes_per_key"])
        + int(workload["active_postings"])
        * (
            int(workload["posting_ownership_bytes_per_active_posting"])
            + int(
                workload[
                    "detail_schedule_and_downstream_cursor_bytes_per_active_posting"
                ]
            )
        )
    )
    control_replicated = control_payload * int(workload["control_metadata_copies"])
    overload_entries = int(
        spec["scenarios"]["overload_shard_loss"]["max_total_queue_entries"]
    )
    queue_primary = (
        overload_entries
        * int(workload["queue_bytes_per_task"])
        * float(workload["queue_allocator_multiplier"])
    )
    queue_replicated = queue_primary * (1 + int(topology["queue_replicas_per_primary"]))
    return {
        "database_payload_tib": _round(database_payload / TIB),
        "database_primary_budget_tib": float(budgets["database_primary_global_tib"]),
        "database_wal_and_scratch_budget_tib": float(
            budgets["database_wal_and_scratch_global_tib"]
        ),
        "r2_description_payload_tib": _round(r2_payload / TIB),
        "r2_description_budget_tib": float(budgets["r2_description_global_tib"]),
        "typesense_index_payload_tib": _round(typesense_payload / TIB),
        "typesense_index_replicated_tib": _round(typesense_replicated / TIB),
        "typesense_index_replicated_budget_tib": float(
            budgets["typesense_index_replicated_global_tib"]
        ),
        "control_metadata_logical_gib": _round(control_payload / GIB),
        "control_metadata_replicated_gib": _round(control_replicated / GIB),
        "control_metadata_budget_gib": float(
            budgets["catalog_policy_and_cursor_global_gib"]
        ),
        "policy_keys_peak_partition": math.ceil(
            int(workload["policy_keys"])
            / int(topology["policy_partitions"])
            * float(topology["failed_board_recovery_max_load_factor"])
        ),
        "active_postings_peak_ownership_partition": math.ceil(
            int(workload["active_postings"])
            / int(topology["posting_ownership_partitions"])
            * float(topology["failed_board_recovery_max_load_factor"])
        ),
        "queue_primary_peak_gib": _round(queue_primary / GIB),
        "queue_replicated_peak_gib": _round(queue_replicated / GIB),
        "queue_aof_and_snapshot_budget_tib": float(
            budgets["queue_aof_and_snapshot_global_tib"]
        ),
        "backup_copies": int(budgets["backup_copies"]),
        "backup_storage_budget_tib": float(budgets["backup_storage_global_tib"]),
        "telemetry_active_series_global": int(
            budgets["telemetry_active_series_global"]
        ),
        "telemetry_logs_traces_profiles_gib_per_month": int(
            budgets["telemetry_logs_gib_per_month"]
            + budgets["telemetry_traces_gib_per_month"]
            + budgets["telemetry_profiles_gib_per_month"]
        ),
    }


def _regional_cell_loss_report(
    spec: Mapping[str, Any], classes: Sequence[WorkClass]
) -> dict[str, Any]:
    scenario = spec["scenarios"]["regional_cell_loss"]
    steady = spec["scenarios"]["steady"]
    cells = int(spec["topology"]["data_cells"])
    global_arrival = sum(item.cycles_per_hour for item in classes) / 3600
    cell_arrival = global_arrival / cells
    restore_seconds = float(scenario["max_freeze_and_restore_seconds"])
    frozen_backlog = cell_arrival * restore_seconds
    recovery_cell_service = float(steady["catch_up_service_cycles_per_second"]) / cells
    paired_continuing_arrivals = cell_arrival * 2
    _require(
        recovery_cell_service > paired_continuing_arrivals,
        "paired cell cannot absorb a lost cell's continuing arrivals",
    )
    drain_seconds = frozen_backlog / (
        recovery_cell_service - paired_continuing_arrivals
    )
    oldest_due = restore_seconds + drain_seconds
    steady_tiers = _scenario_report(spec, "steady", classes)["tiers"]
    return {
        "failed_cells": int(scenario["failed_cells"]),
        "frozen_policy_ready_tasks": math.ceil(frozen_backlog),
        "restore_seconds": _round(restore_seconds),
        "backlog_drain_seconds": _round(drain_seconds),
        "computed_oldest_policy_ready_due_seconds": _round(oldest_due),
        "authoritative_rpo_seconds_max": int(scenario["max_authoritative_rpo_seconds"]),
        "paired_cell_modeled_utilization_pct": {
            "worker_cpu": _round(steady_tiers["worker"]["peak_cpu_pct"] * 2),
            "browser_cpu": _round(steady_tiers["browser"]["peak_cpu_pct"] * 2),
            "queue_throughput": _round(
                steady_tiers["queue"]["modeled_throughput_pct"] * 2
            ),
            "postgres_commit_per_promoted_writer": steady_tiers["postgres"][
                "modeled_commit_throughput_pct"
            ],
            "typesense_cpu": _round(steady_tiers["typesense"]["peak_cpu_pct"] * 2),
        },
    }


def _allocate_pool(
    pool_eur: float,
    volumes: Mapping[str, float],
    weights: Mapping[str, float],
) -> dict[str, float]:
    denominator = sum(volumes[name] * weights[name] for name in volumes)
    _require(denominator > 0, "cost allocation denominator must be positive")
    return {
        name: pool_eur * volumes[name] * weights[name] / denominator for name in volumes
    }


def _cost_report(
    spec: Mapping[str, Any], classes: Sequence[WorkClass], *, sensitivity: bool = False
) -> dict[str, Any]:
    prices = spec["prices"]
    workload = spec["workload"]
    topology = spec["topology"]
    budgets = spec["storage_budgets"]
    exchange = float(spec["provenance"]["planning_exchange_rate_usd_to_eur"])
    hours = float(prices["hours_per_month"])
    volumes = {item.name: item.cycles_per_hour * hours for item in classes}
    multipliers = {
        "price": 1.0,
        "volume": 1.0,
        "browser": 1.0,
        "proxy": 1.0,
        "telemetry": 1.0,
        "backup": 1.0,
        "origin_transfer": float(prices["origin_transfer_eur_per_tib"]),
    }
    if sensitivity:
        stress = spec["cost_sensitivity"]
        multipliers = {
            "price": float(stress["price_multiplier"]),
            "volume": float(stress["sustained_volume_multiplier"]),
            "browser": float(stress["browser_share_multiplier"]),
            "proxy": float(stress["proxy_cost_multiplier"]),
            "telemetry": float(stress["telemetry_cost_multiplier"]),
            "backup": float(stress["backup_copy_multiplier"]),
            "origin_transfer": float(stress["paid_origin_transfer_eur_per_tib"]),
        }
        volumes = {
            name: value * multipliers["volume"] for name, value in volumes.items()
        }

    node_costs = {
        name: float(node["count"]) * float(node["eur_per_month"]) * multipliers["price"]
        for name, node in prices["nodes"].items()
    }
    proxy_cost = (
        float(prices["proxy_static_ips"])
        * float(prices["proxy_eur_per_ip_month"])
        * multipliers["price"]
        * multipliers["proxy"]
    )
    backup_cost = (
        (
            float(budgets["backup_storage_global_tib"])
            * 1024
            * float(prices["backup_eur_per_gb_month"])
            + float(prices["backup_operations_eur_per_month"])
        )
        * multipliers["price"]
        * multipliers["backup"]
    )
    r2_storage_gb = (
        int(workload["retained_postings"])
        * int(workload["description_object_mean_bytes"])
        / 1_000_000_000
    )
    r2_storage_cost = (
        r2_storage_gb
        * float(prices["r2_usd_per_gb_month"])
        * exchange
        * multipliers["price"]
    )
    detail_cycles = volumes["detail"]
    r2_writes_millions = (
        detail_cycles
        * float(workload["description_change_share_of_detail"])
        / 1_000_000
    )
    r2_reads_millions = (
        detail_cycles * float(prices["r2_reads_per_detail_cycle"]) / 1_000_000
    )
    r2_operation_cost = (
        (
            r2_writes_millions * float(prices["r2_class_a_usd_per_million"])
            + r2_reads_millions * float(prices["r2_class_b_usd_per_million"])
        )
        * exchange
        * multipliers["price"]
    )
    included_series = int(topology["telemetry_backends"]) * int(
        prices["metrics_included_series_per_backend"]
    )
    ingested_series = int(budgets["telemetry_active_series_global"]) + int(
        topology["critical_telemetry_series_dual_written"]
    )
    billable_series = max(0, ingested_series - included_series)
    metrics_cost = (
        billable_series
        / 1000
        * float(prices["metrics_usd_per_thousand_series"])
        * exchange
        * multipliers["price"]
    )
    telemetry_billable_gib = sum(
        max(
            0,
            int(budgets[field]) - int(prices["telemetry_included_gib_per_signal"]),
        )
        for field in (
            "telemetry_logs_gib_per_month",
            "telemetry_traces_gib_per_month",
            "telemetry_profiles_gib_per_month",
        )
    )
    telemetry_billable_gib += int(budgets["critical_telemetry_signal_gib_dual_written"])
    signal_cost = (
        telemetry_billable_gib
        * float(prices["telemetry_usd_per_gib"])
        * exchange
        * multipliers["price"]
    )
    telemetry_cost = (metrics_cost + signal_cost) * multipliers["telemetry"]
    transfer_mean_bytes = {item.name: item.mean_response_bytes for item in classes}
    if sensitivity:
        browser_bytes = float(workload["browser_transfer_mean_bytes"])
        http_bytes = {
            "board": float(workload["monitor_http_response_mean_bytes"]),
            "detail": float(workload["detail_http_response_mean_bytes"]),
        }
        transfer_mean_bytes = {
            item.name: (1 - min(1.0, item.browser_share * multipliers["browser"]))
            * http_bytes[item.name]
            + min(1.0, item.browser_share * multipliers["browser"]) * browser_bytes
            for item in classes
        }
    transfer_bytes = sum(
        volumes[item.name]
        * transfer_mean_bytes[item.name]
        * (1 + float(workload["retry_attempts_per_success_max"]))
        for item in classes
    )
    transfer_cost = transfer_bytes / TIB * multipliers["origin_transfer"]
    miscellaneous_cost = (
        float(prices["monthly_miscellaneous_eur"]) * multipliers["price"]
    )

    pools = {
        "queue": node_costs["queue_node"],
        "worker": node_costs["worker_node"],
        "browser": node_costs["browser_node"] * multipliers["browser"],
        "postgres": node_costs["postgres_node"],
        "telemetry": node_costs["telemetry_collector_node"] + telemetry_cost,
        "cell_services": node_costs["cell_service_node"] + node_costs["load_balancer"],
        "routing": node_costs["routing_node"],
        "typesense": node_costs["typesense_node"],
        "proxy": proxy_cost,
        "backup": backup_cost,
        "r2": r2_storage_cost + r2_operation_cost,
        "origin_transfer": transfer_cost,
        "miscellaneous": miscellaneous_cost,
    }
    allocations = {name: 0.0 for name in volumes}

    def allocate(pool: str, weights: Mapping[str, float]) -> None:
        for name, value in _allocate_pool(pools[pool], volumes, weights).items():
            allocations[name] += value

    equal_weights = {item.name: 1.0 for item in classes}
    allocate("queue", equal_weights)
    allocate("cell_services", equal_weights)
    allocate("routing", equal_weights)
    allocate("telemetry", equal_weights)
    allocate("proxy", {item.name: item.proxy_share for item in classes})
    allocate("miscellaneous", equal_weights)
    allocate("worker", {item.name: item.worker_cpu_ms for item in classes})
    allocate(
        "browser",
        {
            item.name: item.browser_share
            * float(workload["browser_session_seconds_mean"])
            for item in classes
        },
    )
    allocate("postgres", {item.name: item.db_commits for item in classes})
    allocate("backup", {item.name: item.db_commits for item in classes})
    allocate("origin_transfer", transfer_mean_bytes)
    allocations["detail"] += pools["r2"]
    allocations["detail"] += pools["typesense"]

    total_cost = sum(pools.values())
    unit = {name: allocations[name] / volumes[name] * 1_000_000 for name in volumes}
    blended = total_cost / sum(volumes.values()) * 1_000_000
    return {
        "mode": "sensitivity" if sensitivity else "steady",
        "monthly_cycles": {name: int(value) for name, value in volumes.items()},
        "monthly_cost_eur": _round(total_cost, 2),
        "pool_costs_eur": {name: _round(value, 2) for name, value in pools.items()},
        "cost_per_million_eur": {
            "board": _round(unit["board"], 4),
            "detail": _round(unit["detail"], 4),
            "blended": _round(blended, 4),
        },
        "origin_transfer_tib_per_month": _round(transfer_bytes / TIB, 2),
        "r2_class_a_operations_millions": _round(r2_writes_millions, 2),
        "planning_exchange_rate_usd_to_eur": exchange,
    }


def _overload_cost_report(
    spec: Mapping[str, Any], classes: Sequence[WorkClass]
) -> dict[str, Any]:
    multiplier = float(spec["scenarios"]["overload_shard_loss"]["load_multiplier"])
    overloaded = tuple(
        WorkClass(
            name=item.name,
            cycles_per_hour=item.cycles_per_hour * multiplier,
            browser_share=item.browser_share,
            worker_cpu_ms=item.worker_cpu_ms,
            db_commits=item.db_commits,
            mean_response_bytes=item.mean_response_bytes,
            proxy_share=item.proxy_share,
        )
        for item in classes
    )
    report = _cost_report(spec, overloaded)
    report["mode"] = "overload_shard_loss"
    return report


def build_report(spec: Mapping[str, Any]) -> dict[str, Any]:
    classes = _work_classes(spec)
    recovery_counts = recovery_partition_counts(spec)
    logical_partitions = int(spec["topology"]["logical_partitions"])
    total_shards = int(spec["topology"]["data_cells"]) * int(
        spec["topology"]["queue_shards_per_cell"]
    )
    partition_factor = max(recovery_counts) / (logical_partitions / total_shards)
    steady_report = _scenario_report(spec, "steady", classes)
    overload_report = _scenario_report(spec, "overload_shard_loss", classes)
    loss_matrix: dict[str, Any] = {}
    for tier, values in overload_report["tiers"].items():
        pct_values = [
            float(value) for key, value in values.items() if key.endswith("_pct")
        ]
        provisioned_key = next(
            key for key in values if key.startswith("provisioned_shards_")
        )
        surviving_key = next(
            key for key in values if key.startswith("surviving_shards_")
        )
        if provisioned_key.endswith("_global"):
            scope = "global"
        elif provisioned_key.endswith("_index_shard"):
            scope = "affected_index_shard"
        else:
            scope = "affected_cell"
        loss_matrix[tier] = {
            "scope": scope,
            "provisioned_shards": int(values[provisioned_key]),
            "lost_shards": int(values["lost_shards"]),
            "surviving_shards": int(values[surviving_key]),
            "max_modeled_capacity_pct_after_loss": _round(max(pct_values)),
        }
    return {
        "schema_version": 1,
        "revision": spec["revision"],
        "effective_date": spec["effective_date"],
        "spec_sha256": sha256_json(spec),
        "classification": {
            "analytical": ["workload", "resource_utilization", "storage", "cost"],
            "measured_elsewhere": [
                "routing_distribution",
                "routing_recovery_conservation",
            ],
            "future_execution_gate": [
                "go_runtime",
                "redis",
                "postgres",
                "lightpanda",
                "typesense",
                "network",
                "cell_services",
                "routing",
                "load_balancer",
                "telemetry_backends",
            ],
        },
        "workload": {
            "configured_boards": int(spec["workload"]["configured_boards"]),
            "active_postings": int(spec["workload"]["active_postings"]),
            "steady_cycles_per_hour": int(
                sum(item.cycles_per_hour for item in classes)
            ),
            "overload_cycles_per_hour": int(
                sum(item.cycles_per_hour for item in classes)
                * float(spec["scenarios"]["overload_shard_loss"]["load_multiplier"])
            ),
            "origin_attempts_per_success_max": _round(
                1 + float(spec["workload"]["retry_attempts_per_success_max"])
            ),
            "payload_contract": {
                "max_inline_body_bytes": int(spec["workload"]["max_inline_body_bytes"]),
                "max_artifact_chunk_bytes": int(
                    spec["workload"]["max_artifact_chunk_bytes"]
                ),
                "max_http_transfer_bytes": int(
                    spec["workload"]["max_http_transfer_bytes"]
                ),
                "max_browser_transfer_bytes": int(
                    spec["workload"]["max_browser_transfer_bytes"]
                ),
                "resident_semantics": spec["workload"]["resident_payload_semantics"],
            },
        },
        "topology": {
            "data_cells": int(spec["topology"]["data_cells"]),
            "typesense_index_shards": int(spec["topology"]["typesense_index_shards"]),
            "typesense_index_shards_per_data_cell": int(
                spec["topology"]["typesense_index_shards"]
                // spec["topology"]["data_cells"]
            ),
            "typesense_ha_replica_groups_per_index_shard": int(
                spec["profiles"]["typesense"]["ha_replica_groups_per_index_shard"]
            ),
            "typesense_nodes_per_ha_replica_group": int(
                spec["profiles"]["typesense"]["nodes_per_ha_replica_group"]
            ),
            "typesense_full_replicas_per_index_shard": int(
                spec["profiles"]["typesense"]["ha_replica_groups_per_index_shard"]
            )
            * int(spec["profiles"]["typesense"]["nodes_per_ha_replica_group"]),
            "logical_partitions": logical_partitions,
            "queue_shards": total_shards,
            "unweighted_reference_recovery_partition_counts": recovery_counts,
            "unweighted_reference_recovery_max_load_factor": _round(partition_factor),
            "simultaneous_loss_matrix": loss_matrix,
        },
        "scenarios": {
            "steady": steady_report,
            "overload_shard_loss": overload_report,
            "regional_cell_loss": _regional_cell_loss_report(spec, classes),
        },
        "storage": _storage_report(spec),
        "cost": {
            "steady": _cost_report(spec, classes),
            "overload_shard_loss": _overload_cost_report(spec, classes),
            "sensitivity": _cost_report(spec, classes, sensitivity=True),
        },
        "thresholds": {
            "steady": spec["scenarios"]["steady"],
            "overload_shard_loss": spec["scenarios"]["overload_shard_loss"],
            "regional_cell_loss": spec["scenarios"]["regional_cell_loss"],
            "retirement_execution_gate": spec["retirement_execution_gate"],
        },
    }


def check_report(spec: Mapping[str, Any], report: Mapping[str, Any]) -> None:
    workload = spec["workload"]
    topology = spec["topology"]
    scenarios = spec["scenarios"]
    storage = report["storage"]
    _require(spec["schema_version"] == 1, "unknown capacity schema version")
    _require(spec["revision"] == "global-v1", "revision must remain global-v1")
    _require(bool(spec["effective_date"]), "effective_date is required")
    _require(bool(spec["provenance"]["prices_as_of"]), "price date is required")
    _require(len(spec["provenance"]) >= 5, "source provenance is incomplete")
    _require(int(workload["configured_boards"]) >= 10_000_000, "board floor regressed")
    _require(int(workload["active_postings"]) >= 100_000_000, "posting floor regressed")
    _require(
        int(workload["monitor_cycles_per_hour"]) >= 1_000_000, "monitor floor regressed"
    )
    _require(
        int(workload["detail_cycles_per_hour"]) >= 5_000_000, "detail floor regressed"
    )
    _require(
        float(workload["retry_attempts_per_success_max"]) <= 0.05,
        "request amplification exceeds 1.05x",
    )
    _require(
        int(topology["data_cells"]) >= 8,
        "fewer than eight data cells is global coupling",
    )
    logical_partitions = int(topology["logical_partitions"])
    _require(
        logical_partitions >= 8192
        and (logical_partitions & (logical_partitions - 1)) == 0,
        "logical partitions must be a power of two at least 8192",
    )
    total_queue_shards = int(topology["data_cells"]) * int(
        topology["queue_shards_per_cell"]
    )
    _require(
        logical_partitions % total_queue_shards == 0,
        "logical partitions must divide evenly across queue shards",
    )
    _require(
        int(topology["queue_replicas_per_primary"]) >= 2, "queue needs two replicas"
    )
    _require(
        int(topology["postgres_sync_standbys_per_writer"]) >= 1,
        "each Postgres writer needs a synchronous standby",
    )
    _require(int(topology["telemetry_backends"]) >= 2, "telemetry needs two backends")
    _require(
        int(topology["typesense_index_shards"]) >= 8,
        "Typesense must not be a global cluster",
    )
    _require(
        int(topology["typesense_index_shards"]) % int(topology["data_cells"]) == 0,
        "Typesense index shards must divide evenly across data cells",
    )
    typesense_profile = spec["profiles"]["typesense"]
    _require(
        int(typesense_profile["ha_replica_groups_per_index_shard"]) >= 1,
        "Typesense needs at least one HA replica group per index shard",
    )
    _require(
        int(typesense_profile["nodes_per_ha_replica_group"]) >= 3
        and int(typesense_profile["nodes_per_ha_replica_group"]) % 2 == 1,
        "Typesense HA replica groups need an odd node count of at least three",
    )
    _require(
        int(topology["policy_partitions"]) >= 4096, "policy state is under-partitioned"
    )
    _require(
        int(topology["posting_ownership_partitions"]) >= 8192,
        "canonical posting ownership is under-partitioned",
    )
    _require(
        int(topology["postgres_writer_shards_per_cell"]) >= 2,
        "one Postgres writer per cell is still a cell bottleneck",
    )
    _require(
        int(workload["max_inline_body_bytes"]) == 8 * 1024 * 1024,
        "inline body limit must match the 8 MiB contract ceiling",
    )
    _require(
        int(workload["max_artifact_chunk_bytes"]) == 8 * 1024 * 1024,
        "artifact chunk limit must match the 8 MiB contract ceiling",
    )
    _require(
        int(workload["max_http_transfer_bytes"]) == 16 * 1024 * 1024
        and int(workload["max_browser_transfer_bytes"]) == 64 * 1024 * 1024
        and int(workload["monitor_http_response_max_bytes"])
        == int(workload["max_http_transfer_bytes"])
        and int(workload["detail_http_response_max_bytes"])
        == int(workload["max_http_transfer_bytes"])
        and int(workload["browser_transfer_max_bytes"])
        == int(workload["max_browser_transfer_bytes"]),
        "total transfer maxima must match the cross-stream contract",
    )
    node_prices = spec["prices"]["nodes"]
    cells = int(topology["data_cells"])
    expected_node_counts = {
        "queue_node": cells
        * int(topology["queue_shards_per_cell"])
        * (1 + int(topology["queue_replicas_per_primary"])),
        "worker_node": cells * int(topology["worker_shards_per_cell"]),
        "browser_node": cells * int(topology["browser_shards_per_cell"]),
        "postgres_node": cells
        * int(topology["postgres_writer_shards_per_cell"])
        * (1 + int(topology["postgres_sync_standbys_per_writer"])),
        "telemetry_collector_node": cells
        * int(topology["telemetry_collectors_per_cell"]),
        "cell_service_node": cells * int(topology["cell_service_shards_per_cell"]),
        "routing_node": cells * int(topology["routing_shards_per_cell"]),
        "typesense_node": int(topology["typesense_index_shards"])
        * int(spec["profiles"]["typesense"]["ha_replica_groups_per_index_shard"])
        * int(spec["profiles"]["typesense"]["nodes_per_ha_replica_group"]),
        "load_balancer": cells * int(topology["load_balancers_per_cell"]),
    }
    _require(
        all(
            int(node_prices[name]["count"]) == count
            for name, count in expected_node_counts.items()
        ),
        "priced node counts do not match the declared topology",
    )
    _require(
        float(scenarios["overload_shard_loss"]["load_multiplier"]) == 2.0,
        "overload must be exactly 2x",
    )
    expected_lost_tiers = {
        "queue",
        "worker",
        "browser",
        "postgres",
        "telemetry",
        "telemetry_backend",
        "typesense",
        "cell_service",
        "routing",
        "load_balancer",
    }
    loss_by_tier = scenarios["overload_shard_loss"]["failed_shards_by_tier"]
    _require(
        set(loss_by_tier) == expected_lost_tiers
        and all(int(value) == 1 for value in loss_by_tier.values()),
        "overload must coincide with one lost shard in every runtime tier",
    )
    _require(
        report["topology"]["unweighted_reference_recovery_max_load_factor"]
        <= float(topology["failed_partition_recovery_max_load_factor"]),
        "recovery assignment exceeds its frozen imbalance factor",
    )
    for scenario_name in ("steady", "overload_shard_loss"):
        threshold = scenarios[scenario_name]
        result = report["scenarios"][scenario_name]
        _require(
            result["network_gbps"] <= float(threshold["max_network_gbps"]),
            f"{scenario_name} network budget exceeded",
        )
        for tier, values in result["tiers"].items():
            if "peak_cpu_pct" in values:
                _require(
                    values["peak_cpu_pct"] <= float(threshold["max_cpu_pct"]),
                    f"{scenario_name} {tier} CPU exceeds {threshold['max_cpu_pct']}%",
                )
            if "peak_rss_pct" in values:
                _require(
                    values["peak_rss_pct"] <= float(threshold["max_rss_pct"]),
                    f"{scenario_name} {tier} RSS exceeds {threshold['max_rss_pct']}%",
                )
        _require(
            result["tiers"]["queue"]["modeled_throughput_pct"]
            <= float(threshold["max_cpu_pct"]),
            f"{scenario_name} queue modeled throughput headroom is too small",
        )
        _require(
            result["tiers"]["browser"]["session_slot_pct"]
            <= float(threshold["max_cpu_pct"]),
            f"{scenario_name} browser session headroom is too small",
        )
        _require(
            result["tiers"]["postgres"]["modeled_commit_throughput_pct"]
            <= float(threshold["max_cpu_pct"]),
            f"{scenario_name} Postgres commit headroom is too small",
        )
        _require(
            result["tiers"]["typesense"]["modeled_throughput_pct"]
            <= float(threshold["max_cpu_pct"]),
            f"{scenario_name} Typesense throughput headroom is too small",
        )
        for tier in ("load_balancer", "telemetry_backend"):
            _require(
                result["tiers"][tier]["modeled_throughput_pct"]
                <= float(threshold["max_cpu_pct"]),
                f"{scenario_name} {tier} throughput headroom is too small",
            )
    for tier, values in report["topology"]["simultaneous_loss_matrix"].items():
        _require(values["lost_shards"] == 1, f"{tier} loss gate is not one shard")
        _require(
            values["max_modeled_capacity_pct_after_loss"]
            <= float(scenarios["overload_shard_loss"]["max_cpu_pct"]),
            f"{tier} surviving capacity exceeds 70%",
        )
    overload = report["scenarios"]["overload_shard_loss"]
    steady = report["scenarios"]["steady"]
    for scenario_name, result in (
        ("steady", steady),
        ("overload_shard_loss", overload),
    ):
        threshold = scenarios[scenario_name]
        _require(
            result["computed_policy_ready_queue_depth"]
            <= int(threshold["max_policy_ready_queue_depth"]),
            f"{scenario_name} computed ready queue exceeds its threshold",
        )
        _require(
            result["computed_total_queue_entries"]
            <= int(threshold["max_total_queue_entries"]),
            f"{scenario_name} computed total queue exceeds its threshold",
        )
        _require(
            result["computed_oldest_policy_ready_due_seconds"]
            <= float(threshold["max_oldest_policy_ready_due_seconds"]),
            f"{scenario_name} computed oldest-due exceeds its threshold",
        )
    _require(
        overload["computed_oldest_policy_ready_due_seconds"]
        <= float(
            scenarios["overload_shard_loss"]["max_oldest_policy_ready_due_seconds"]
        ),
        "2x catch-up cannot meet the frozen RTO",
    )
    _require(
        overload["computed_shard_recovery_seconds"]
        <= float(scenarios["overload_shard_loss"]["max_shard_recovery_seconds"]),
        "computed shard recovery exceeds 600 seconds",
    )
    _require(
        overload["computed_backlog_drain_seconds"]
        <= float(scenarios["overload_shard_loss"]["max_catch_up_seconds"]),
        "computed overload backlog drain exceeds catch-up threshold",
    )
    cell_loss = report["scenarios"]["regional_cell_loss"]
    _require(
        cell_loss["computed_oldest_policy_ready_due_seconds"]
        <= float(
            scenarios["regional_cell_loss"]["max_oldest_policy_ready_due_seconds"]
        ),
        "regional cell loss exceeds its disaster oldest-due threshold",
    )
    _require(
        max(cell_loss["paired_cell_modeled_utilization_pct"].values()) <= 70,
        "paired recovery cell cannot absorb the lost cell below 70%",
    )
    _require(
        storage["database_payload_tib"] <= storage["database_primary_budget_tib"],
        "database payload exceeds its primary budget",
    )
    _require(
        storage["r2_description_payload_tib"] <= storage["r2_description_budget_tib"],
        "R2 descriptions exceed their budget",
    )
    _require(
        storage["typesense_index_replicated_tib"]
        <= storage["typesense_index_replicated_budget_tib"],
        "Typesense index exceeds its budget",
    )
    _require(
        storage["control_metadata_replicated_gib"]
        <= storage["control_metadata_budget_gib"],
        "replicated catalog/policy/cursor metadata exceeds 256 GiB",
    )
    _require(
        storage["queue_replicated_peak_gib"]
        <= storage["queue_aof_and_snapshot_budget_tib"] * 1024,
        "replicated queue state exceeds its persistence budget",
    )
    steady_cost = report["cost"]["steady"]
    steady_threshold = scenarios["steady"]
    _require(
        steady_cost["monthly_cost_eur"]
        <= float(steady_threshold["max_monthly_cost_eur"]),
        "monthly steady cost ceiling exceeded",
    )
    for name in ("board", "detail", "blended"):
        _require(
            steady_cost["cost_per_million_eur"][name]
            <= float(steady_threshold[f"max_{name}_cost_per_million_eur"]),
            f"steady {name} unit cost ceiling exceeded",
        )
    overload_cost = report["cost"]["overload_shard_loss"]
    overload_threshold = scenarios["overload_shard_loss"]
    for name in ("board", "detail", "blended"):
        _require(
            overload_cost["cost_per_million_eur"][name]
            <= float(overload_threshold[f"max_{name}_cost_per_million_eur"]),
            f"overload {name} unit cost ceiling exceeded",
        )
    sensitivity = report["cost"]["sensitivity"]
    sensitivity_threshold = spec["cost_sensitivity"]
    for name in ("board", "detail", "blended"):
        _require(
            sensitivity["cost_per_million_eur"][name]
            <= float(sensitivity_threshold[f"max_{name}_cost_per_million_eur"]),
            f"sensitivity {name} unit cost ceiling exceeded",
        )


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def _physical_memory_bytes() -> int | None:
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        pages = int(os.sysconf("SC_PHYS_PAGES"))
    except (OSError, ValueError):
        return None
    return page_size * pages


def benchmark_routing(spec: Mapping[str, Any], boards: int) -> dict[str, Any]:
    """Measure weighted full-cardinality routing and prove conservation."""

    _require(boards > 0, "benchmark board count must be positive")
    topology = spec["topology"]
    workload = spec["workload"]
    logical_partitions = int(topology["logical_partitions"])
    shards_per_cell = int(topology["queue_shards_per_cell"])
    total_shards = int(topology["data_cells"]) * shards_per_cell
    failed_slot = 0
    old_cell_weights = [1.0] * int(topology["data_cells"])
    new_cell_weights = [*old_cell_weights, 1.0]
    old_growth_owners = [
        growth_owner(partition, old_cell_weights)
        for partition in range(logical_partitions)
    ]
    new_growth_owners = [
        growth_owner(partition, new_cell_weights)
        for partition in range(logical_partitions)
    ]
    growth_moved_partitions = sum(
        old != new
        for old, new in zip(old_growth_owners, new_growth_owners, strict=True)
    )
    _require(
        all(
            old == new or new == len(old_cell_weights)
            for old, new in zip(old_growth_owners, new_growth_owners, strict=True)
        ),
        "growth rendezvous moved a partition between existing cells",
    )
    board_counts = [0] * logical_partitions
    posting_weights = [0] * logical_partitions
    monitor_browser_counts = [0] * logical_partitions
    detail_browser_weights = [0] * logical_partitions
    provider_names = (
        "workday",
        "greenhouse",
        "successfactors",
        "oracle",
        "lever",
        "personio",
        "custom_api",
        "long_tail",
    )
    provider_thresholds = (64, 102, 133, 159, 184, 210, 235, 256)
    provider_board_weights = [0] * len(provider_names)
    provider_posting_weights = [0] * len(provider_names)
    provider_board_partitions = [
        [0] * logical_partitions for _ in range(len(provider_names))
    ]
    provider_posting_partitions = [
        [0] * logical_partitions for _ in range(len(provider_names))
    ]
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    for index in range(boards):
        digest = hashlib.sha256(f"board:synthetic-{index:08d}".encode()).digest()
        partition = int.from_bytes(digest[:8], "big") % logical_partitions
        board_counts[partition] += 1
        posting_roll = int.from_bytes(digest[8:12], "big") % 1000
        posting_weight = 1000 if posting_roll == 0 else 100 if posting_roll < 10 else 1
        posting_weights[partition] += posting_weight
        if digest[12] < round(256 * float(workload["monitor_browser_share"])):
            monitor_browser_counts[partition] += 1
        if digest[13] < round(256 * float(workload["detail_browser_share"])):
            detail_browser_weights[partition] += posting_weight
        provider_index = bisect.bisect_right(provider_thresholds, digest[14])
        provider_board_weights[provider_index] += 1
        provider_posting_weights[provider_index] += posting_weight
        provider_board_partitions[provider_index][partition] += 1
        provider_posting_partitions[provider_index][partition] += posting_weight

    monitor_cycles = float(workload["monitor_cycles_per_hour"])
    detail_cycles = float(workload["detail_cycles_per_hour"])
    postings_target = float(workload["active_postings"])
    total_posting_weight = sum(posting_weights)
    total_monitor_browser = sum(monitor_browser_counts)
    total_detail_browser = sum(detail_browser_weights)
    task_rates = [
        board_count / boards * monitor_cycles
        + posting_weight / total_posting_weight * detail_cycles
        for board_count, posting_weight in zip(
            board_counts, posting_weights, strict=True
        )
    ]
    scaled_postings = [
        posting_weight / total_posting_weight * postings_target
        for posting_weight in posting_weights
    ]
    browser_rates = [
        monitor_browser
        / total_monitor_browser
        * monitor_cycles
        * float(workload["monitor_browser_share"])
        + detail_browser
        / total_detail_browser
        * detail_cycles
        * float(workload["detail_browser_share"])
        for monitor_browser, detail_browser in zip(
            monitor_browser_counts, detail_browser_weights, strict=True
        )
    ]
    provider_task_rates = {
        name: (
            provider_board_weights[index] / boards * monitor_cycles
            + provider_posting_weights[index] / total_posting_weight * detail_cycles
        )
        for index, name in enumerate(provider_names)
    }
    provider_partition_task_rates = [
        [
            board_weight / boards * monitor_cycles
            + posting_weight / total_posting_weight * detail_cycles
            for board_weight, posting_weight in zip(
                provider_board_partitions[index],
                provider_posting_partitions[index],
                strict=True,
            )
        ]
        for index in range(len(provider_names))
    ]

    core_dimensions: tuple[Sequence[float], ...] = (
        board_counts,
        task_rates,
        scaled_postings,
        browser_rates,
    )
    dimensions = core_dimensions + tuple(provider_partition_task_rates)
    dimension_targets = (
        boards / total_shards,
        (monitor_cycles + detail_cycles) / total_shards,
        postings_target / total_shards,
        (
            monitor_cycles * float(workload["monitor_browser_share"])
            + detail_cycles * float(workload["detail_browser_share"])
        )
        / total_shards,
    ) + tuple(provider_task_rates[name] / total_shards for name in provider_names)
    dimension_loads = [
        [
            sum(
                values[partition]
                for partition in range(owner, logical_partitions, total_shards)
            )
            for owner in range(total_shards)
        ]
        for values in dimensions
    ]
    failed_partitions = list(range(failed_slot, logical_partitions, total_shards))
    failed_partitions.sort(
        key=lambda partition: (
            -max(
                dimensions[index][partition] / dimension_targets[index]
                for index in range(len(dimensions))
            ),
            partition,
        )
    )
    recovery_owners: dict[int, int] = {}
    candidate_slots = range(1, shards_per_cell)
    for partition in failed_partitions:

        def candidate_score(
            slot: int, owned_partition: int = partition
        ) -> tuple[float, int]:
            projected = max(
                (dimension_loads[index][slot] + dimensions[index][owned_partition])
                / dimension_targets[index]
                for index in range(len(dimensions))
            )
            tie_break = -int.from_bytes(
                hashlib.sha256(
                    f"recovery:v1:{owned_partition}:{slot}".encode()
                ).digest()[:8],
                "big",
            )
            return projected, tie_break

        owner = min(candidate_slots, key=candidate_score)
        recovery_owners[partition] = owner
        for index, values in enumerate(dimensions):
            dimension_loads[index][owner] += values[partition]

    def aggregate(values: Sequence[float], *, recover: bool) -> list[float]:
        result = [0.0] * total_shards
        for partition, value in enumerate(values):
            owner = primary_slot(partition, total_shards)
            if recover and owner == failed_slot:
                owner = recovery_owners[partition]
            result[owner] += value
        return result

    primary_boards = aggregate(board_counts, recover=False)
    recovered_boards = aggregate(board_counts, recover=True)
    recovered_tasks = aggregate(task_rates, recover=True)
    recovered_postings = aggregate(scaled_postings, recover=True)
    recovered_browser = aggregate(browser_rates, recover=True)
    moved_boards = int(primary_boards[failed_slot])
    moved_partitions = logical_partitions // total_shards
    wall_seconds = time.perf_counter() - started_wall
    cpu_seconds = time.process_time() - started_cpu
    _require(sum(board_counts) == boards, "logical partition conservation failed")
    _require(round(sum(primary_boards)) == boards, "primary shard conservation failed")
    _require(
        round(sum(recovered_boards)) == boards, "recovery shard conservation failed"
    )
    _require(recovered_boards[failed_slot] == 0, "failed shard retained ownership")
    _require(
        math.isclose(
            sum(recovered_tasks), monitor_cycles + detail_cycles, rel_tol=1e-12
        ),
        "weighted task-rate conservation failed",
    )
    _require(
        math.isclose(sum(recovered_postings), postings_target, rel_tol=1e-12),
        "posting-cardinality conservation failed",
    )
    expected_browser_rate = monitor_cycles * float(
        workload["monitor_browser_share"]
    ) + detail_cycles * float(workload["detail_browser_share"])
    _require(
        math.isclose(sum(recovered_browser), expected_browser_rate, rel_tol=1e-12),
        "browser-rate conservation failed",
    )
    mean_shard_boards = boards / total_shards
    max_recovery_factor = max(recovered_boards) / mean_shard_boards
    task_factor = max(recovered_tasks) / (
        (monitor_cycles + detail_cycles) / total_shards
    )
    posting_factor = max(recovered_postings) / (postings_target / total_shards)
    browser_factor = max(recovered_browser) / (expected_browser_rate / total_shards)
    provider_factors = {
        provider_names[index]: max(aggregate(values, recover=True))
        / (provider_task_rates[provider_names[index]] / total_shards)
        for index, values in enumerate(provider_partition_task_rates)
    }
    recovery_limit = float(topology["failed_board_recovery_max_load_factor"])
    if boards >= int(workload["configured_boards"]):
        for label, factor in (
            ("board", max_recovery_factor),
            ("task-rate", task_factor),
            ("posting", posting_factor),
            ("browser-rate", browser_factor),
            ("provider-rate", max(provider_factors.values())),
        ):
            _require(
                factor <= recovery_limit,
                f"measured {label} recovery imbalance {factor:.4f} exceeds {recovery_limit:.4f}",
            )
    result: dict[str, Any] = {
        "schema_version": 1,
        "classification": "measured_synthetic_weighted_routing_only",
        "measurement_date": str(spec["effective_date"]),
        "base_commit": str(spec["base_commit"]),
        "explicitly_not_measured": [
            "go_runtime",
            "redis",
            "postgres",
            "lightpanda",
            "origin_network",
            "typesense",
            "telemetry_backend",
        ],
        "spec_sha256": sha256_json(spec),
        "command": (
            f"python scripts/capacity_envelope.py --check --benchmark-routing {boards}"
        ),
        "units": {
            "wall_seconds": "seconds",
            "cpu_seconds": "process CPU seconds",
            "peak_rss_bytes": "bytes",
            "throughput": "weighted board routing decisions per wall-clock second",
            "task_rate": "successful cycles per hour",
        },
        "input": {
            "boards": boards,
            "active_postings": int(postings_target),
            "monitor_cycles_per_hour": int(monitor_cycles),
            "detail_cycles_per_hour": int(detail_cycles),
            "logical_partitions": logical_partitions,
            "queue_shards": total_shards,
            "failed_slot": failed_slot,
            "weighting": {
                "postings": (
                    "deterministic heavy tail: 0.1% weight 1000, "
                    "next 0.9% weight 100, remainder weight 1"
                ),
                "browser": "deterministic per-class shares from the capacity spec",
                "providers": "25/15/12/10/10/10/10/8 percent synthetic family mix",
            },
        },
        "conservation": {
            "input_boards": boards,
            "primary_boards": round(sum(primary_boards)),
            "recovered_boards": round(sum(recovered_boards)),
            "lost_boards": boards - round(sum(recovered_boards)),
            "duplicate_boards": 0,
            "recovered_active_postings": round(sum(recovered_postings)),
            "recovered_cycles_per_hour": round(sum(recovered_tasks)),
            "moved_boards": moved_boards,
            "moved_partitions": moved_partitions,
            "unaffected_partitions_moved": 0,
            "growth_input_partitions": logical_partitions,
            "growth_output_partitions": len(new_growth_owners),
            "growth_lost_partitions": 0,
            "growth_duplicate_partitions": 0,
            "growth_moved_partitions": growth_moved_partitions,
            "growth_existing_to_existing_moves": 0,
        },
        "distribution": {
            "partition_min_boards": min(board_counts),
            "partition_max_boards": max(board_counts),
            "primary_shard_min_boards": round(min(primary_boards)),
            "primary_shard_max_boards": round(max(primary_boards)),
            "recovered_shard_min_boards_excluding_failed": round(
                min(recovered_boards[1:])
            ),
            "recovered_shard_max_boards": round(max(recovered_boards)),
            "recovered_max_board_load_factor": _round(max_recovery_factor, 6),
            "recovered_max_task_rate_factor": _round(task_factor, 6),
            "recovered_max_posting_factor": _round(posting_factor, 6),
            "recovered_max_browser_rate_factor": _round(browser_factor, 6),
            "recovered_max_provider_rate_factor": _round(
                max(provider_factors.values()), 6
            ),
            "provider_task_cycles_per_hour": {
                name: round(value) for name, value in provider_task_rates.items()
            },
            "growth_new_cell_partition_share": _round(
                growth_moved_partitions / logical_partitions, 6
            ),
        },
        "measurement": {
            "wall_seconds": _round(wall_seconds, 6),
            "cpu_seconds": _round(cpu_seconds, 6),
            "peak_rss_bytes": _peak_rss_bytes(),
            "boards_per_wall_second": _round(boards / wall_seconds, 2),
        },
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "physical_memory_bytes": _physical_memory_bytes(),
        },
    }
    result["evidence_sha256"] = sha256_json(result)
    return result


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument(
        "--check", action="store_true", help="assert every frozen threshold"
    )
    parser.add_argument(
        "--json", action="store_true", help="print the analytical report as JSON"
    )
    parser.add_argument("--write-report", type=Path, help="write the analytical report")
    parser.add_argument(
        "--benchmark-routing",
        type=int,
        metavar="BOARDS",
        help="measure streaming ownership/recovery at the requested board count",
    )
    parser.add_argument(
        "--write-evidence", type=Path, help="write routing benchmark evidence"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    spec = load_spec(args.spec)
    report = build_report(spec)
    if args.check:
        check_report(spec, report)
    if args.write_report:
        _write_json(args.write_report, report)
    evidence = None
    if args.benchmark_routing is not None:
        evidence = benchmark_routing(spec, args.benchmark_routing)
        if args.write_evidence:
            _write_json(args.write_evidence, evidence)
    elif args.write_evidence:
        raise CapacityError("--write-evidence requires --benchmark-routing")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        status = "checked" if args.check else "modelled"
        cost = report["cost"]["steady"]["cost_per_million_eur"]
        print(
            f"capacity {status}: spec_sha256={report['spec_sha256']} "
            f"cost_eur_per_million={cost['blended']:.4f}"
        )
        if evidence:
            measured = evidence["measurement"]
            print(
                f"routing measured: boards={evidence['input']['boards']} "
                f"wall_seconds={measured['wall_seconds']:.6f} "
                f"peak_rss_bytes={measured['peak_rss_bytes']} "
                f"evidence_sha256={evidence['evidence_sha256']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
