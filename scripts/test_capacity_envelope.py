from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "capacity_envelope.py"
SPEC = ROOT / "capacity" / "crawler" / "global-v1.json"
REPORT = ROOT / "capacity" / "crawler" / "global-v1-report.json"
EVIDENCE = ROOT / "capacity" / "crawler" / "evidence" / "2026-08-26-local-routing.json"

module_spec = importlib.util.spec_from_file_location("capacity_envelope", SCRIPT)
assert module_spec and module_spec.loader
capacity = importlib.util.module_from_spec(module_spec)
sys.modules[module_spec.name] = capacity
module_spec.loader.exec_module(capacity)


class CapacityEnvelopeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.spec = capacity.load_spec(SPEC)
        cls.report = capacity.build_report(cls.spec)

    def test_checked_in_report_is_reproducible(self) -> None:
        self.assertEqual(json.loads(REPORT.read_text(encoding="utf-8")), self.report)
        self.assertEqual(self.report["spec_sha256"], capacity.sha256_json(self.spec))
        with self.assertRaisesRegex(capacity.CapacityError, "monthly steady cost ceiling exceeded"):
            capacity.check_report(self.spec, self.report)

    def test_epic_floor_and_simultaneous_failure_are_frozen(self) -> None:
        report = self.report
        self.assertEqual(report["workload"]["configured_boards"], 10_000_000)
        self.assertEqual(report["workload"]["active_postings"], 100_000_000)
        self.assertEqual(report["workload"]["steady_cycles_per_hour"], 6_000_000)
        self.assertEqual(report["workload"]["overload_cycles_per_hour"], 12_000_000)
        self.assertEqual(report["workload"]["origin_attempts_per_success_max"], 1.05)
        self.assertEqual(
            report["workload"]["payload_contract"]["max_inline_body_bytes"],
            8 * 1024**2,
        )
        self.assertEqual(
            report["workload"]["payload_contract"]["max_browser_transfer_bytes"],
            64 * 1024**2,
        )
        overload = report["thresholds"]["overload_shard_loss"]
        self.assertEqual(overload["load_multiplier"], 2.0)
        self.assertEqual(
            set(overload["failed_shards_by_tier"]),
            set(report["topology"]["simultaneous_loss_matrix"]),
        )
        self.assertEqual(set(overload["failed_shards_by_tier"].values()), {1})
        self.assertEqual(overload["max_policy_ready_queue_depth"], 12_000_000)
        self.assertEqual(overload["max_oldest_policy_ready_due_seconds"], 2_700)
        self.assertEqual(overload["max_shard_recovery_seconds"], 600)
        self.assertEqual(overload["max_catch_up_seconds"], 2_700)

    def test_every_tier_fits_cpu_and_rss_thresholds(self) -> None:
        for name in ("steady", "overload_shard_loss"):
            thresholds = self.report["thresholds"][name]
            scenario = self.report["scenarios"][name]
            for values in scenario["tiers"].values():
                if "peak_cpu_pct" in values:
                    self.assertLessEqual(values["peak_cpu_pct"], thresholds["max_cpu_pct"])
                if "peak_rss_pct" in values:
                    self.assertLessEqual(values["peak_rss_pct"], thresholds["max_rss_pct"])

    def test_typesense_models_full_replicas_and_leader_serialized_writes(self) -> None:
        steady = self.report["scenarios"]["steady"]["tiers"]["typesense"]
        overload = self.report["scenarios"]["overload_shard_loss"]["tiers"]["typesense"]

        self.assertEqual(self.report["topology"]["typesense_index_shards"], 24)
        self.assertEqual(self.report["topology"]["typesense_index_shards_per_data_cell"], 3)
        self.assertEqual(self.report["topology"]["typesense_ha_replica_groups_per_index_shard"], 5)
        self.assertEqual(self.report["topology"]["typesense_nodes_per_ha_replica_group"], 3)
        self.assertEqual(self.report["topology"]["typesense_full_replicas_per_index_shard"], 15)
        self.assertIn(
            "full global search QPS", self.spec["workload"]["typesense_search_fanout_semantics"]
        )
        self.assertIn(
            "every HA replica group",
            self.spec["workload"]["typesense_write_replication_semantics"],
        )
        self.assertEqual(
            self.report["topology"]["simultaneous_loss_matrix"]["typesense"]["scope"],
            "affected_index_shard",
        )

        # 640 GiB is split over 24 independent index shards. Every node in a
        # shard holds that shard's complete 26.667 GiB working set.
        self.assertAlmostEqual(steady["working_set_gib_per_replica"], 640 / 24, places=4)
        self.assertAlmostEqual(steady["peak_rss_pct"], 100 * (640 / 24) / 64, places=4)
        self.assertEqual(overload["peak_rss_pct"], steady["peak_rss_pct"])

        # Replica loss cannot create write throughput. At 2x, every HA-group
        # leader serializes all 400 writes/s for its shard; 14 nodes share reads.
        self.assertAlmostEqual(
            overload["write_leader_utilization_pct_per_ha_group"], 20.0, places=4
        )
        self.assertEqual(overload["search_qps_per_index_shard"], 4000)
        self.assertAlmostEqual(
            overload["search_replica_utilization_pct"],
            100 * 4000 / (14 * 800),
            places=4,
        )
        self.assertAlmostEqual(overload["replicated_write_cpu_cores_per_node"], 0.8)
        self.assertAlmostEqual(
            overload["search_cpu_cores_per_surviving_node"],
            4000 / 14 * 0.005,
            places=4,
        )
        self.assertAlmostEqual(
            overload["peak_cpu_pct"],
            100 * (0.8 + 4000 / 14 * 0.005) / 8,
            places=4,
        )
        self.assertLessEqual(steady["modeled_throughput_pct"], 35)
        self.assertLessEqual(overload["modeled_throughput_pct"], 70)

    def test_typesense_search_fanout_does_not_divide_qps_by_index_shards(self) -> None:
        expanded = copy.deepcopy(self.spec)
        expanded["topology"]["typesense_index_shards"] = 48
        expanded["prices"]["nodes"]["typesense_node"]["count"] = 48 * 5 * 3
        report = capacity.build_report(expanded)
        original = self.report["scenarios"]["overload_shard_loss"]["tiers"]["typesense"]
        changed = report["scenarios"]["overload_shard_loss"]["tiers"]["typesense"]

        self.assertEqual(changed["search_qps_per_index_shard"], 4000)
        self.assertEqual(
            changed["search_replica_utilization_pct"],
            original["search_replica_utilization_pct"],
        )
        self.assertEqual(
            changed["search_cpu_cores_per_surviving_node"],
            original["search_cpu_cores_per_surviving_node"],
        )
        self.assertEqual(
            changed["upserts_per_second_per_ha_group"],
            original["upserts_per_second_per_ha_group"] / 2,
        )

    def test_typesense_replica_count_only_scales_read_capacity(self) -> None:
        expanded = copy.deepcopy(self.spec)
        expanded["profiles"]["typesense"]["ha_replica_groups_per_index_shard"] = 7
        report = capacity.build_report(expanded)
        original = self.report["scenarios"]["overload_shard_loss"]["tiers"]["typesense"]
        changed = report["scenarios"]["overload_shard_loss"]["tiers"]["typesense"]

        self.assertEqual(changed["peak_rss_pct"], original["peak_rss_pct"])
        self.assertEqual(
            changed["write_leader_utilization_pct_per_ha_group"],
            original["write_leader_utilization_pct_per_ha_group"],
        )
        self.assertEqual(
            changed["replicated_write_cpu_cores_per_node"],
            original["replicated_write_cpu_cores_per_node"],
        )
        self.assertLess(
            changed["search_replica_utilization_pct"],
            original["search_replica_utilization_pct"],
        )
        self.assertLess(changed["peak_cpu_pct"], original["peak_cpu_pct"])

    def test_typesense_replica_memory_regression_fails_closed(self) -> None:
        unsafe = copy.deepcopy(self.spec)
        unsafe["profiles"]["typesense"]["memory_gib_per_node"] = 32
        with self.assertRaisesRegex(capacity.CapacityError, "typesense RSS"):
            capacity.check_report(unsafe, capacity.build_report(unsafe))

    def test_original_eight_shard_typesense_topology_fails_closed(self) -> None:
        unsafe = copy.deepcopy(self.spec)
        unsafe["topology"]["typesense_index_shards"] = 8
        unsafe["prices"]["nodes"]["typesense_node"]["count"] = 8 * 5 * 3
        report = capacity.build_report(unsafe)
        self.assertEqual(
            report["scenarios"]["steady"]["tiers"]["typesense"]["peak_rss_pct"],
            125.0,
        )
        with self.assertRaisesRegex(capacity.CapacityError, "typesense RSS"):
            capacity.check_report(unsafe, report)

    def test_single_ha_group_fanout_profile_fails_closed(self) -> None:
        unsafe = copy.deepcopy(self.spec)
        unsafe["profiles"]["typesense"]["ha_replica_groups_per_index_shard"] = 1
        unsafe["prices"]["nodes"]["typesense_node"]["count"] = 24 * 3
        report = capacity.build_report(unsafe)
        overload = report["scenarios"]["overload_shard_loss"]["tiers"]["typesense"]
        self.assertEqual(overload["search_qps_per_index_shard"], 4000)
        self.assertEqual(overload["search_replica_utilization_pct"], 250.0)
        self.assertEqual(overload["peak_cpu_pct"], 135.0)
        with self.assertRaisesRegex(capacity.CapacityError, "typesense CPU"):
            capacity.check_report(unsafe, report)

    def test_each_runtime_tier_survives_its_simultaneous_loss(self) -> None:
        matrix = self.report["topology"]["simultaneous_loss_matrix"]
        self.assertEqual(
            set(matrix),
            {
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
            },
        )
        for values in matrix.values():
            self.assertEqual(values["lost_shards"], 1)
            self.assertGreaterEqual(values["surviving_shards"], 1)
            self.assertLessEqual(values["max_modeled_capacity_pct_after_loss"], 70)

    def test_cost_report_exposes_economic_rejection(self) -> None:
        steady = self.report["cost"]["steady"]
        overload = self.report["cost"]["overload_shard_loss"]
        sensitivity = self.report["cost"]["sensitivity"]
        steady_threshold = self.spec["scenarios"]["steady"]
        overload_threshold = self.spec["scenarios"]["overload_shard_loss"]
        sensitivity_threshold = self.spec["cost_sensitivity"]
        self.assertGreater(steady["monthly_cost_eur"], steady_threshold["max_monthly_cost_eur"])
        self.assertLessEqual(steady["cost_per_million_eur"]["board"], 16)
        self.assertGreater(
            steady["cost_per_million_eur"]["detail"],
            steady_threshold["max_detail_cost_per_million_eur"],
        )
        self.assertGreater(
            overload["cost_per_million_eur"]["detail"],
            overload_threshold["max_detail_cost_per_million_eur"],
        )
        self.assertGreater(
            overload["cost_per_million_eur"]["blended"],
            overload_threshold["max_blended_cost_per_million_eur"],
        )
        self.assertGreater(
            sensitivity["cost_per_million_eur"]["detail"],
            sensitivity_threshold["max_detail_cost_per_million_eur"],
        )
        self.assertGreater(sensitivity["origin_transfer_tib_per_month"], 1_000)
        self.assertLessEqual(
            self.report["storage"]["typesense_index_replicated_tib"],
            self.report["storage"]["typesense_index_replicated_budget_tib"],
        )

    def test_threshold_regression_fails_closed(self) -> None:
        regressed = copy.deepcopy(self.spec)
        regressed["scenarios"]["overload_shard_loss"]["max_cpu_pct"] = 40
        with self.assertRaisesRegex(capacity.CapacityError, "CPU exceeds"):
            capacity.check_report(regressed, capacity.build_report(regressed))

    def test_routing_vectors_and_recovery_are_deterministic(self) -> None:
        partitions = int(self.spec["topology"]["logical_partitions"])
        vectors = {
            "board-00000000": 3142,
            "board-00000001": 1259,
            "company:board/eu": 2804,
            "会社-採用": 2488,
        }
        self.assertEqual(
            {board_id: capacity.partition_for_board(board_id, partitions) for board_id in vectors},
            vectors,
        )
        counts = capacity.recovery_partition_counts(self.spec)
        self.assertEqual(sum(counts), partitions)
        self.assertEqual(counts[:8], [0, 148, 147, 138, 145, 149, 152, 145])
        self.assertTrue(all(count == 128 for count in counts[8:]))
        old = [capacity.growth_owner(partition, [1.0] * 8) for partition in range(8192)]
        new = [capacity.growth_owner(partition, [1.0] * 9) for partition in range(8192)]
        self.assertTrue(
            all(before == after or after == 8 for before, after in zip(old, new, strict=True))
        )

    def test_streaming_harness_conserves_a_small_fixture(self) -> None:
        evidence = capacity.benchmark_routing(self.spec, 25_000)
        self.assertEqual(evidence["conservation"]["input_boards"], 25_000)
        self.assertEqual(evidence["conservation"]["lost_boards"], 0)
        self.assertEqual(evidence["conservation"]["duplicate_boards"], 0)
        self.assertEqual(evidence["conservation"]["unaffected_partitions_moved"], 0)
        self.assertEqual(evidence["conservation"]["recovered_active_postings"], 100_000_000)
        self.assertEqual(evidence["conservation"]["recovered_cycles_per_hour"], 6_000_000)
        self.assertEqual(evidence["conservation"]["growth_existing_to_existing_moves"], 0)

    def test_checked_in_full_cardinality_evidence_is_self_verifying(self) -> None:
        evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        digest = evidence.pop("evidence_sha256")
        self.assertEqual(digest, capacity.sha256_json(evidence))
        self.assertEqual(evidence["spec_sha256"], capacity.sha256_json(self.spec))
        self.assertEqual(evidence["input"]["boards"], 10_000_000)
        self.assertEqual(evidence["conservation"]["lost_boards"], 0)
        self.assertEqual(evidence["conservation"]["duplicate_boards"], 0)
        self.assertEqual(evidence["conservation"]["growth_lost_partitions"], 0)
        self.assertEqual(evidence["conservation"]["growth_duplicate_partitions"], 0)
        self.assertEqual(evidence["conservation"]["growth_existing_to_existing_moves"], 0)
        for field in (
            "recovered_max_board_load_factor",
            "recovered_max_task_rate_factor",
            "recovered_max_posting_factor",
            "recovered_max_browser_rate_factor",
            "recovered_max_provider_rate_factor",
        ):
            self.assertLessEqual(evidence["distribution"][field], 1.32)
        self.assertIn("lightpanda", evidence["explicitly_not_measured"])

    def test_cli_check_rejects_economically_infeasible_profile(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("monthly steady cost ceiling exceeded", completed.stderr)


if __name__ == "__main__":
    unittest.main()
