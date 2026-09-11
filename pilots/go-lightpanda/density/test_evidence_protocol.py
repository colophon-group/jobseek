from __future__ import annotations

import copy
import importlib.util
import json
import math
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "density_evidence_protocol", HERE / "evidence_protocol.py"
)
assert SPEC is not None and SPEC.loader is not None
protocol = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(protocol)


def resources(
    peak: int,
    *,
    memory_max_events: int = 0,
    oom_events: int = 0,
    observation: str = "final",
    host_page_size: int = 4096,
):
    return {
        "final_sample": observation == "final",
        "observation": observation,
        "memory_current": min(peak, 64 * 1024**2),
        "memory_peak": peak,
        "memory_limit": protocol.GIB,
        "memory_swap_limit": 0,
        "host_page_size": host_page_size,
        "memory_events": {
            "low": 0,
            "high": 0,
            "max": memory_max_events,
            "oom": oom_events,
            "oom_kill": oom_events,
            "oom_group_kill": 0,
        },
        "cpu_max": [100000, 100000],
        "cpu_stat": {"usage_usec": 1, "user_usec": 1, "system_usec": 0},
        "pids_limit": 768,
        "pids_current": 1,
        "pids_peak": 64,
        "pids_events": {"max": 0},
        "cgroup_process_count": 1,
    }


def pressure_resources(*, memory_max_events: int = 0, oom_events: int = 0):
    return resources(
        protocol.GIB + 4096,
        memory_max_events=memory_max_events,
        oom_events=oom_events,
        observation="sampled_not_final",
    )


class EvidenceProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule, cls.schedule_sha = protocol.load_schedule(
            HERE / "schedule.v1.json"
        )

    def records(
        self,
        *,
        go_elapsed: int = 100,
        python_elapsed: int = 125,
        go_peak: int = 640 * 1024**2,
        python_peak: int = 800 * 1024**2,
    ):
        result = []
        for pair in self.schedule["pairs"]:
            for arm_index, implementation in enumerate(pair["order"]):
                result.append(
                    {
                        "schema_version": 1,
                        "pair_id": pair["id"],
                        "arm_index": arm_index,
                        "implementation": implementation,
                        "concurrency": pair["concurrency"],
                        "outcome": "pass",
                        "failure_id": None,
                        "elapsed_ns": go_elapsed
                        if implementation == "go"
                        else python_elapsed,
                        "resources": resources(
                            go_peak if implementation == "go" else python_peak
                        ),
                    }
                )
        return result

    def record(self, records, pair_id, implementation):
        return next(
            record
            for record in records
            if record["pair_id"] == pair_id
            and record["implementation"] == implementation
        )

    def set_failure(self, record, failure_id, *, final_resources=None):
        record["outcome"] = "failure"
        record["failure_id"] = failure_id
        record["elapsed_ns"] = None
        record["resources"] = final_resources

    def test_schedule_is_exact_frozen_shape_order_and_hash(self):
        self.assertEqual(self.schedule_sha, protocol.EXPECTED_SCHEDULE_SHA256)
        self.assertEqual(self.schedule["expected_arm_records"], 22)
        self.assertEqual(self.schedule["resource_envelope"]["pids_limit"], 768)
        self.assertEqual(
            [
                (pair["concurrency"], pair["order"], pair["diagnostic"])
                for pair in self.schedule["pairs"]
            ],
            [
                (1, ["python", "go"], True),
                (4, ["go", "python"], False),
                (4, ["python", "go"], False),
                (4, ["go", "python"], False),
                (4, ["python", "go"], False),
                (4, ["go", "python"], False),
                (8, ["python", "go"], False),
                (8, ["go", "python"], False),
                (8, ["python", "go"], False),
                (8, ["go", "python"], False),
                (8, ["python", "go"], False),
            ],
        )

        for mutation in (
            lambda value: value.update(expected_arm_records=21),
            lambda value: value.update(schema_version=True),
            lambda value: value.update(expected_arm_records=22.0),
            lambda value: value["resource_envelope"].update(memory_swap_bytes=False),
            lambda value: value["resource_envelope"].update(pids_limit=768.0),
            lambda value: value["pairs"][2].update(order=["go", "python"]),
            lambda value: value["resource_envelope"].update(pids_limit=769),
            lambda value: value["admission"].update(go_rate_wins_min=3),
            lambda value: value.update(extra=True),
        ):
            changed = copy.deepcopy(self.schedule)
            mutation(changed)
            with self.subTest(changed=changed):
                with self.assertRaises(protocol.ProtocolError) as caught:
                    protocol.validate_schedule(changed)
                self.assertEqual(caught.exception.reason_id, "schedule_invalid")

        with tempfile.TemporaryDirectory() as directory:
            changed_path = Path(directory) / "schedule.json"
            changed_path.write_bytes((HERE / "schedule.v1.json").read_bytes() + b"\n")
            with self.assertRaises(protocol.ProtocolError) as caught:
                protocol.load_schedule(changed_path)
            self.assertEqual(caught.exception.reason_id, "schedule_hash_invalid")

    def test_exact_thresholds_are_admitted_and_json_serializable(self):
        report = protocol.summarize_evidence(self.schedule, self.records())
        self.assertEqual(report["status"], "admitted")
        self.assertEqual(report["reason_ids"], ["admission_thresholds_met"])
        self.assertEqual(
            report["metrics"]["median_rate_ratio"],
            {"numerator": 5, "denominator": 4},
        )
        self.assertEqual(
            report["metrics"]["median_memory_ratio"],
            {"numerator": 4, "denominator": 5},
        )
        encoded = json.dumps(report, allow_nan=False)
        self.assertNotIn("Infinity", encoded)
        self.assertNotIn("NaN", encoded)

    def test_exact_four_of_five_strict_wins_are_admitted(self):
        records = self.records()
        for pair_id in ("c8-p01", "c8-p02", "c8-p03", "c8-p04"):
            self.record(records, pair_id, "python")["elapsed_ns"] = 125
            self.record(records, pair_id, "go")["elapsed_ns"] = 100
        self.record(records, "c8-p05", "python")["elapsed_ns"] = 100
        self.record(records, "c8-p05", "go")["elapsed_ns"] = 100
        self.record(records, "c8-p05", "go")["resources"]["memory_peak"] = 800 * 1024**2
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "admitted")
        self.assertEqual(report["metrics"]["rate_wins"], 4)
        self.assertEqual(report["metrics"]["memory_wins"], 4)

    def test_only_three_strict_wins_fails_both_count_gates(self):
        records = self.records()
        for pair_id in ("c8-p04", "c8-p05"):
            self.record(records, pair_id, "python")["elapsed_ns"] = 100
            self.record(records, pair_id, "go")["elapsed_ns"] = 100
            self.record(records, pair_id, "go")["resources"]["memory_peak"] = (
                800 * 1024**2
            )
            self.record(records, pair_id, "python")["resources"]["memory_peak"] = (
                800 * 1024**2
            )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "not_admitted")
        self.assertEqual(
            report["reason_ids"],
            ["go_rate_wins_below_threshold", "go_memory_wins_below_threshold"],
        )

    def test_median_threshold_failures_are_exact(self):
        records = self.records()
        for pair_id in ("c8-p01", "c8-p02", "c8-p03"):
            self.record(records, pair_id, "python")["elapsed_ns"] = 124
            self.record(records, pair_id, "go")["elapsed_ns"] = 100
            self.record(records, pair_id, "go")["resources"]["memory_peak"] = (
                801 * 1024**2
            )
            self.record(records, pair_id, "python")["resources"]["memory_peak"] = (
                1000 * 1024**2
            )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "not_admitted")
        self.assertIn("go_rate_median_below_threshold", report["reason_ids"])
        self.assertIn("go_memory_median_above_threshold", report["reason_ids"])

    def test_both_c8_median_peaks_below_seventy_percent_is_inconclusive(self):
        below = (7 * protocol.GIB) // 10
        records = self.records(go_peak=below, python_peak=below)
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["reason_ids"], ["c8_ram_ceiling_unreached"])

        above = below + 1
        records = self.records(go_peak=below, python_peak=above)
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertNotEqual(report["reason_ids"], ["c8_ram_ceiling_unreached"])

    def test_c1_is_diagnostic_only(self):
        records = self.records()
        self.set_failure(self.record(records, "c1-p01", "python"), "timeout")
        self.set_failure(self.record(records, "c1-p01", "go"), "missing_artifact")
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "admitted")

    def test_c4_and_go_c8_prerequisites_distinguish_correctness_from_measurement(self):
        cases = (
            (
                "c4-p01",
                "python",
                "oracle",
                "not_admitted",
                "c4_correctness_gate_failed",
            ),
            (
                "c4-p01",
                "python",
                "timeout",
                "inconclusive",
                "c4_measurement_inconclusive",
            ),
            (
                "c8-p01",
                "go",
                "conservation",
                "not_admitted",
                "go_c8_correctness_gate_failed",
            ),
            (
                "c8-p01",
                "go",
                "fd_limit",
                "inconclusive",
                "go_c8_measurement_inconclusive",
            ),
            (
                "c4-p01",
                "python",
                "concurrency",
                "not_admitted",
                "c4_correctness_gate_failed",
            ),
            (
                "c8-p01",
                "go",
                "go_runner_invariant_failure",
                "not_admitted",
                "go_c8_correctness_gate_failed",
            ),
        )
        for pair_id, implementation, failure_id, status, reason in cases:
            records = self.records()
            self.set_failure(self.record(records, pair_id, implementation), failure_id)
            report = protocol.summarize_evidence(self.schedule, records)
            with self.subTest(failure_id=failure_id):
                self.assertEqual(report["status"], status)
                self.assertEqual(report["reason_ids"], [reason])

        records = self.records()
        self.set_failure(
            self.record(records, "c4-p01", "python"),
            "go_runner_invariant_failure",
        )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])

        for failure_id in ("cleanup", "containment", "process_residue"):
            records = self.records()
            self.set_failure(
                self.record(records, "c1-p01", "go"),
                failure_id,
            )
            report = protocol.summarize_evidence(self.schedule, records)
            with self.subTest(failure_id=failure_id):
                self.assertEqual(report["status"], "invalid")
                self.assertEqual(report["reason_ids"], ["protocol_integrity_failure"])

    def test_executor_abort_keeps_slots_without_fabricated_measurements(self):
        records = self.records()
        first_not_run = next(
            index
            for index, record in enumerate(records)
            if record["pair_id"] == "c4-p03"
        )
        self.set_failure(records[first_not_run - 1], "timeout")
        for record in records[first_not_run:]:
            self.set_failure(record, "not_run")
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_not_run"])

        records = self.records()
        self.set_failure(records[0], "not_run", final_resources=resources(1024))
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])

    def test_memory_pressure_fallback_requires_three_kernel_confirmed_failures(self):
        records = self.records(go_peak=700 * 1024**2)
        for pair_id in ("c8-p01", "c8-p02", "c8-p03"):
            self.set_failure(
                self.record(records, pair_id, "python"),
                "oom",
                final_resources=pressure_resources(oom_events=1),
            )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "admitted")
        self.assertEqual(report["reason_ids"], ["memory_pressure_fallback_met"])
        self.assertEqual(report["metrics"]["branch"], "python_memory_pressure")
        self.assertNotIn("rate_ratios", report["metrics"])
        self.assertNotIn("memory_ratios", report["metrics"])
        self.assertFalse(any("ratio" in key for key in report["metrics"]))

        records = self.records(go_peak=700 * 1024**2)
        for pair_id in ("c8-p01", "c8-p02"):
            self.set_failure(
                self.record(records, pair_id, "python"),
                "memory_pressure",
                final_resources=pressure_resources(memory_max_events=1),
            )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(
            report["reason_ids"], ["python_c8_memory_pressure_insufficient"]
        )

    def test_memory_fallback_rejects_mixed_and_unconfirmed_failures(self):
        records = self.records()
        for pair_id in ("c8-p01", "c8-p02", "c8-p03"):
            self.set_failure(
                self.record(records, pair_id, "python"),
                "oom",
                final_resources=pressure_resources(oom_events=1),
            )
        self.set_failure(self.record(records, "c8-p04", "python"), "timeout")
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["reason_ids"], ["python_c8_mixed_failures"])

        records = self.records()
        for pair_id in ("c8-p01", "c8-p02", "c8-p03"):
            self.set_failure(
                self.record(records, pair_id, "python"),
                "oom",
                final_resources=pressure_resources(),
            )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(
            report["reason_ids"], ["python_c8_memory_pressure_unconfirmed"]
        )

        records = self.records(go_peak=700 * 1024**2)
        for pair_id in ("c8-p01", "c8-p02", "c8-p03"):
            self.set_failure(
                self.record(records, pair_id, "python"),
                "oom",
                final_resources=pressure_resources(oom_events=1),
            )
        self.set_failure(
            self.record(records, "c8-p04", "python"),
            "memory_pressure",
            final_resources=pressure_resources(),
        )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "admitted")
        self.assertEqual(
            report["metrics"]["python_kernel_confirmed_pressure_failures"], 3
        )

    def test_pid_pressure_cannot_be_mislabeled_for_memory_admission(self):
        records = self.records(go_peak=700 * 1024**2)
        for pair_id in ("c8-p01", "c8-p02", "c8-p03"):
            sampled = pressure_resources(oom_events=1)
            sampled["pids_events"]["max"] = 1
            self.set_failure(
                self.record(records, pair_id, "python"),
                "oom",
                final_resources=sampled,
            )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])

        records = self.records(go_peak=700 * 1024**2)
        for pair_id in ("c8-p01", "c8-p02", "c8-p03"):
            sampled = pressure_resources(oom_events=1)
            sampled["pids_events"]["max"] = 1
            self.set_failure(
                self.record(records, pair_id, "python"),
                "pid_limit",
                final_resources=sampled,
            )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["reason_ids"], ["python_c8_mixed_failures"])

        records = self.records()
        self.set_failure(
            self.record(records, "c8-p01", "python"), "pid_limit"
        )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])

    def test_cpu_period_is_frozen_and_symmetric(self):
        records = self.records()
        self.record(records, "c8-p01", "go")["resources"]["cpu_max"] = [
            1000,
            1000,
        ]
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])

    def test_failed_oom_retains_only_bounded_page_slack(self):
        records = self.records(go_peak=700 * 1024**2)
        for pair_id in ("c8-p01", "c8-p02", "c8-p03"):
            self.set_failure(
                self.record(records, pair_id, "python"),
                "oom",
                final_resources=pressure_resources(oom_events=1),
            )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "admitted")

        invalid = self.record(records, "c8-p01", "python")["resources"]
        invalid["memory_peak"] = protocol.GIB + invalid["host_page_size"] + 1
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])

        records = self.records()
        failed = self.record(records, "c8-p01", "python")
        sampled = pressure_resources(oom_events=1)
        sampled["host_page_size"] = 6000
        self.set_failure(failed, "oom", final_resources=sampled)
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")

    def test_pass_requires_final_observation_and_single_process(self):
        for mutation in (
            lambda value: value.update(
                final_sample=False, observation="sampled_not_final"
            ),
            lambda value: value.update(cgroup_process_count=2),
        ):
            records = self.records()
            mutation(self.record(records, "c8-p01", "go")["resources"])
            report = protocol.summarize_evidence(self.schedule, records)
            with self.subTest(report=report):
                self.assertEqual(report["status"], "invalid")
                self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])

    def test_resource_current_values_cannot_exceed_peaks_or_processes(self):
        mutations = (
            lambda value: value.update(memory_current=value["memory_peak"] + 1),
            lambda value: value.update(pids_current=value["pids_peak"] + 1),
            lambda value: value.update(pids_current=0, cgroup_process_count=1),
        )
        for mutation in mutations:
            records = self.records()
            mutation(self.record(records, "c8-p01", "go")["resources"])
            report = protocol.summarize_evidence(self.schedule, records)
            with self.subTest(report=report):
                self.assertEqual(report["status"], "invalid")
                self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])

    def test_host_page_size_is_one_recorded_run_invariant(self):
        records = self.records()
        self.record(records, "c8-p01", "go")["resources"]["host_page_size"] = 8192
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])

    def test_memory_fallback_go_peak_limit_is_strict(self):
        strict_max = (4 * protocol.GIB) // 5
        records = self.records(go_peak=strict_max)
        for pair_id in ("c8-p01", "c8-p02", "c8-p03"):
            self.set_failure(
                self.record(records, pair_id, "python"),
                "memory_pressure",
                final_resources=pressure_resources(memory_max_events=1),
            )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "admitted")

        records = self.records(go_peak=strict_max + 1)
        for pair_id in ("c8-p01", "c8-p02", "c8-p03"):
            self.set_failure(
                self.record(records, pair_id, "python"),
                "memory_pressure",
                final_resources=pressure_resources(memory_max_events=1),
            )
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "not_admitted")
        self.assertEqual(report["reason_ids"], ["go_memory_pressure_peak_too_high"])

    def test_zero_noninteger_nan_and_infinite_values_are_invalid(self):
        for invalid in (0, 1.0, math.nan, math.inf):
            records = self.records()
            self.record(records, "c8-p01", "go")["elapsed_ns"] = invalid
            report = protocol.summarize_evidence(self.schedule, records)
            with self.subTest(invalid=invalid):
                self.assertEqual(report["status"], "invalid")
                self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])

        records = self.records()
        self.record(records, "c8-p01", "go")["resources"]["memory_peak"] = math.inf
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")

    def test_unhashable_enums_and_loose_record_numbers_fail_closed(self):
        mutations = (
            lambda record: record.update(outcome=[]),
            lambda record: record["resources"].update(observation=[]),
            lambda record: record.update(schema_version=True),
            lambda record: record.update(concurrency=True),
        )
        for mutation in mutations:
            records = self.records()
            mutation(records[0])
            report = protocol.summarize_evidence(self.schedule, records)
            with self.subTest(report=report):
                self.assertEqual(report["status"], "invalid")

        records = self.records()
        failed = records[0]
        failed.update(outcome="failure", failure_id=[], elapsed_ns=None, resources=None)
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")

    def test_exact_22_record_order_and_closed_shape_are_required(self):
        records = self.records()
        report = protocol.summarize_evidence(self.schedule, records[:-1])
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_count_invalid"])

        records = self.records()
        records[0], records[1] = records[1], records[0]
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_order_invalid"])

        records = self.records()
        records[0]["raw_stderr"] = "forbidden"
        report = protocol.summarize_evidence(self.schedule, records)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["reason_ids"], ["evidence_shape_invalid"])


if __name__ == "__main__":
    unittest.main()
