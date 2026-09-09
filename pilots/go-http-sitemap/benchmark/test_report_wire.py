from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

import report_wire

SOURCE = "a" * 40
MANIFEST_SHA = "b" * 64
SCHEDULE_SHA = "c" * 64
PIN_SHA = "d" * 64
PROTECTED_SHA = "e" * 64
GO_DIGEST = "sha256:" + "1" * 64
PYTHON_DIGEST = "sha256:" + "2" * 64
GO_IDENTITY = f"ghcr.io/colophon-group/jobseek-sitemap-fleet-go:sha-{SOURCE}"
PYTHON_IDENTITY = f"ghcr.io/colophon-group/jobseek-sitemap-fleet-python:sha-{SOURCE}"


class ReportWireTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = Path(__file__).resolve().parent
        cls.fleet = cls.directory / "fleet.json"
        cls.schedule = cls.directory / "schedule.json"
        cls.job_ids = sorted(job["id"] for job in json.loads(cls.fleet.read_text())["jobs"])
        pairs = json.loads(cls.schedule.read_text())["pairs"]
        cls.expected_arms: list[tuple[str, str, str]] = []
        for pair in pairs:
            second = "python" if pair["first"] == "go" else "go"
            cls.expected_arms.extend(
                (
                    (pair["id"], pair["profile"], pair["first"]),
                    (pair["id"], pair["profile"], second),
                )
            )

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.temp = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def args(self, wire: Path, status: int, cleanup: bool = True) -> argparse.Namespace:
        return argparse.Namespace(
            wire=wire,
            out=self.temp / "report.json",
            fleet=self.fleet,
            schedule=self.schedule,
            remote_status=status,
            cleanup_verified=cleanup,
            source_commit=SOURCE,
            manifest_sha256=MANIFEST_SHA,
            schedule_sha256=SCHEDULE_SHA,
            go_identity=GO_IDENTITY,
            python_identity=PYTHON_IDENTITY,
            go_digest=GO_DIGEST,
            python_digest=PYTHON_DIGEST,
            protected_postflight_sha256=PROTECTED_SHA,
            cooldown=8,
        )

    def raw_report(self, implementation: str, profile: str, *, byte_factor: float = 1.0) -> dict:
        concurrency = report_wire.PROFILES[profile]
        rounds = []
        for number, state in ((1, "cold"), (2, "pool_warm")):
            jobs = []
            for index, job_id in enumerate(self.job_ids):
                decoded = round((100 + index) * byte_factor)
                jobs.append(
                    {
                        "id": job_id,
                        "status": "succeeded",
                        "canonical_url_count": index + 1,
                        "canonical_url_sha256": f"{index + 1:064x}",
                        "canonical_url_hash_algorithm": "sha256-length-prefixed-v1",
                        "filtered_count": 0,
                        "truncated": False,
                        "request_count": 1,
                        "wire_attempt_count": 1,
                        "decoded_bytes": decoded,
                        "status_body_bytes": 0,
                        "queue_duration_ms": 1,
                        "service_duration_ms": 2,
                    }
                )
            rounds.append(
                {
                    "round": number,
                    "connection_state": state,
                    "status": "succeeded",
                    "run_duration_ms": 100,
                    "cpu_user_ms": 10,
                    "cpu_system_ms": 2,
                    "process_max_rss_bytes": 20_000_000,
                    "process_current_rss_bytes": 18_000_000,
                    "open_fds": 8,
                    "max_in_flight": concurrency,
                    "request_count": 32,
                    "wire_attempt_count": 32,
                    "decoded_bytes": sum(job["decoded_bytes"] for job in jobs),
                    "status_body_bytes": 0,
                    "jobs": jobs,
                }
            )
        identity = GO_IDENTITY if implementation == "go" else PYTHON_IDENTITY
        return {
            "schema_version": 1,
            "implementation": implementation,
            "runtime_version": "go1.25.1" if implementation == "go" else "3.13.7",
            "profile": profile,
            "concurrency": concurrency,
            "source_commit": SOURCE,
            "image_identity": identity,
            "manifest_sha256": MANIFEST_SHA,
            "status": "succeeded",
            "startup_duration_ms": 2,
            "startup_cpu_user_ms": 1,
            "startup_cpu_system_ms": 0,
            "startup_rss_bytes": 15_000_000,
            "startup_open_fds": 8,
            "run_duration_ms": 210,
            "cpu_user_ms": 20,
            "cpu_system_ms": 4,
            "process_max_rss_bytes": 20_000_000,
            "cgroup_memory_peak_bytes": 25_000_000,
            "cgroup_memory_current_bytes": 18_000_000,
            "cgroup_oom_events": 0,
            "configured": {
                "max_connections": 20,
                "max_keepalive_connections": 10,
                "per_origin_concurrency": 1,
                "http_version": "1.1",
                "accept_encoding": "identity",
                "request_timeout_ms": 20_000,
                "job_timeout_ms": 25_000,
                "max_response_bytes": 8_388_608,
                "max_aggregate_bytes": 33_554_432,
                "max_requests_per_job": 1,
            },
            "rounds": rounds,
            "final": {
                "accepted": 64,
                "completed": 64,
                "failed": 0,
                "queued": 0,
                "in_flight": 0,
                "max_in_flight": concurrency,
                "connections_open": 0,
                "connection_limit": 20,
                "per_origin_limit": 1,
                "connection_waiters": 0,
                "maximum_connections": min(concurrency + 10, 20),
                "process_current_rss_bytes": 18_000_000,
                "open_fds": 8,
            },
        }

    def arm_line(
        self,
        pair: str,
        implementation: str,
        profile: str,
        *,
        source: str = "none",
        raw_valid: bool = True,
    ) -> str:
        return "\t".join(
            (
                "ARM_LIFECYCLE",
                pair,
                implementation,
                profile,
                "exited",
                "0",
                "false",
                "true",
                "true",
                "0.5",
                "0.4",
                "2000000",
                "2000000",
                source,
                "true" if raw_valid else "false",
            )
        )

    def make_wire(
        self,
        arms: list[tuple[tuple[str, str, str], dict | str, str, bool]],
        *,
        postflight: bool = True,
    ) -> Path:
        lines = [
            "\t".join(
                (
                    "RUN_META",
                    PIN_SHA,
                    PROTECTED_SHA,
                    "8",
                    "2000000",
                    "6000000",
                    "0.5",
                    "2000000",
                    "6000000",
                    "0.4",
                )
            ),
        ]
        for (pair, profile, implementation), raw, source, remote_valid in arms:
            lines.append(
                self.arm_line(pair, implementation, profile, source=source, raw_valid=remote_valid)
            )
            lines.append(raw if isinstance(raw, str) else json.dumps(raw, separators=(",", ":")))
        if postflight:
            lines.append(f"RUN_POSTFLIGHT\ttrue\t{PIN_SHA}\t{PROTECTED_SHA}")
        path = self.temp / "wire"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def make_preflight_failure_wire(
        self, stage: str, *, status: int = 71, baseline: str = PROTECTED_SHA
    ) -> Path:
        path = self.temp / "preflight-wire"
        path.write_text(
            f"PREFLIGHT_FAILURE\t{stage}\t{status}\t{baseline}\n",
            encoding="utf-8",
        )
        return path

    def successful_arms(self, *, python_factor: float = 1.0):
        return [
            (
                arm,
                self.raw_report(
                    arm[2], arm[1], byte_factor=python_factor if arm[2] == "python" else 1.0
                ),
                "none",
                True,
            )
            for arm in self.expected_arms
        ]

    def test_successful_exact_protocol(self) -> None:
        report = report_wire.parse_report(self.args(self.make_wire(self.successful_arms()), 0))
        self.assertEqual(report["status"], "succeeded")
        self.assertTrue(report["report_valid"])
        self.assertTrue(report["timing_comparable"])
        self.assertEqual(len(report["arms"]), 60)
        self.assertEqual(len(report["byte_deltas"]), 60)

    def test_preflight_failure_retains_exact_stage_without_safety_alarm(self) -> None:
        wire = self.make_preflight_failure_wire("pull_go_image")
        report = report_wire.parse_report(self.args(wire, 71))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["error_kind"], "benchmark_infrastructure_failure")
        self.assertEqual(report["preflight_failure_stage"], "pull_go_image")
        self.assertEqual(report["protected_baseline_sha256"], PROTECTED_SHA)
        self.assertTrue(report["report_valid"])

    def test_preflight_failure_stage_must_be_allowlisted(self) -> None:
        wire = self.temp / "bad-stage-wire"
        wire.write_text(
            f"PREFLIGHT_FAILURE\tnot_a_stage\t71\t{PROTECTED_SHA}\n",
            encoding="utf-8",
        )
        report = report_wire.parse_report(self.args(wire, 71))
        self.assertEqual(report["error_kind"], "production_safety_violation")
        self.assertFalse(report["report_valid"])

    def test_preflight_safety_status_retains_sanitized_stage(self) -> None:
        wire = self.make_preflight_failure_wire("dns_pinning", status=70)
        report = report_wire.parse_report(self.args(wire, 70))
        self.assertEqual(report["status"], "aborted")
        self.assertEqual(report["error_kind"], "production_safety_violation")
        self.assertEqual(report["preflight_failure_stage"], "dns_pinning")
        self.assertTrue(report["report_valid"])

    def test_preflight_failure_status_must_match_remote_exit(self) -> None:
        wire = self.make_preflight_failure_wire("pull_go_image", status=70)
        report = report_wire.parse_report(self.args(wire, 71))
        self.assertEqual(report["error_kind"], "production_safety_violation")
        self.assertFalse(report["report_valid"])

    def test_preflight_failure_without_baseline_fails_closed_as_safety(self) -> None:
        wire = self.make_preflight_failure_wire("inventory", baseline="none")
        report = report_wire.parse_report(self.args(wire, 71))
        self.assertEqual(report["status"], "aborted")
        self.assertEqual(report["error_kind"], "production_safety_violation")
        self.assertFalse(report["report_valid"])

    def test_byte_imbalance_is_inconclusive_not_correctness_failure(self) -> None:
        report = report_wire.parse_report(
            self.args(self.make_wire(self.successful_arms(python_factor=1.10)), 0)
        )
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["error_kind"], "inconclusive_workload_imbalance")
        self.assertTrue(report["report_valid"])
        self.assertFalse(report["timing_comparable"])

    def test_count_hash_mismatch_is_correctness_failure(self) -> None:
        arms = self.successful_arms()
        arms[1][1]["rounds"][0]["jobs"][0]["canonical_url_sha256"] = "f" * 64
        report = report_wire.parse_report(self.args(self.make_wire(arms), 0))
        self.assertEqual(report["error_kind"], "correctness_parity_mismatch")
        self.assertTrue(report["report_valid"])

    def test_cohort_failure_retains_two_valid_arms(self) -> None:
        arms = self.successful_arms()[:2]
        arms[0][1]["status"] = "failed"
        arms[0][1]["error_kind"] = "empty_result"
        arms[0] = (arms[0][0], arms[0][1], "runner_report_failed", True)
        report = report_wire.parse_report(self.args(self.make_wire(arms), 72))
        self.assertEqual(report["error_kind"], "cohort_admission_failed")
        self.assertTrue(report["report_valid"])
        self.assertEqual(len(report["arms"]), 2)

    def test_later_source_failure_retains_all_arms(self) -> None:
        arms = self.successful_arms()
        arms[20][1]["status"] = "failed"
        arms[20][1]["error_kind"] = "empty_result"
        arms[20] = (arms[20][0], arms[20][1], "runner_report_failed", True)
        report = report_wire.parse_report(self.args(self.make_wire(arms), 0))
        self.assertEqual(report["error_kind"], "benchmark_or_source_failure")
        self.assertTrue(report["report_valid"])
        self.assertEqual(len(report["arms"]), 60)

    def test_partial_safety_abort_retains_prefix(self) -> None:
        report = report_wire.parse_report(
            self.args(self.make_wire(self.successful_arms()[:1], postflight=False), 70)
        )
        self.assertEqual(report["status"], "aborted")
        self.assertEqual(report["error_kind"], "production_safety_violation")
        self.assertTrue(report["report_valid"])
        self.assertEqual(len(report["arms"]), 1)

    def test_source_policy_denial_is_distinct(self) -> None:
        arms = self.successful_arms()[:1]
        arms[0][1]["status"] = "failed"
        arms[0][1]["error_kind"] = "tdm_reserved"
        arms[0] = (arms[0][0], arms[0][1], "source_policy_denial", True)
        report = report_wire.parse_report(self.args(self.make_wire(arms), 73))
        self.assertEqual(report["error_kind"], "source_policy_denial")
        self.assertTrue(report["report_valid"])

    def test_egress_policy_denial_is_distinct(self) -> None:
        arms = self.successful_arms()[:1]
        arms[0][1]["status"] = "failed"
        arms[0][1]["error_kind"] = "ssrf_refused"
        arms[0] = (arms[0][0], arms[0][1], "egress_policy_denial", True)
        report = report_wire.parse_report(self.args(self.make_wire(arms), 73))
        self.assertEqual(report["error_kind"], "egress_policy_denial")
        self.assertTrue(report["report_valid"])

    def test_unknown_and_url_typed_metric_are_rejected_without_leak(self) -> None:
        raw = self.raw_report("go", "c2")
        raw["run_duration_ms"] = "https://secret.example/job/1"
        raw["unknown"] = "https://secret.example/job/2"
        report = report_wire.parse_report(
            self.args(
                self.make_wire(
                    [(self.expected_arms[0], raw, "stdout_invalid", True)], postflight=False
                ),
                70,
            )
        )
        serialized = json.dumps(report, allow_nan=False)
        self.assertNotIn("secret.example", serialized)
        self.assertFalse(report["arms"][0]["raw_report_valid"])
        self.assertFalse(report["report_valid"])

    def test_nan_is_rejected_without_nonfinite_output(self) -> None:
        raw = self.raw_report("go", "c2")
        text = json.dumps(raw, separators=(",", ":")).replace(
            '"run_duration_ms":210', '"run_duration_ms":NaN'
        )
        report = report_wire.parse_report(
            self.args(
                self.make_wire(
                    [(self.expected_arms[0], text, "stdout_invalid", True)], postflight=False
                ),
                70,
            )
        )
        self.assertFalse(report["arms"][0]["raw_report_valid"])
        json.dumps(report, allow_nan=False)

    def test_postflight_protected_digest_mismatch_invalidates_transport(self) -> None:
        args = self.args(self.make_wire(self.successful_arms()), 0)
        args.protected_postflight_sha256 = "9" * 64
        report = report_wire.parse_report(args)
        self.assertFalse(report["postflight_valid"])
        self.assertFalse(report["report_valid"])
        self.assertEqual(report["status"], "aborted")
        self.assertEqual(report["error_kind"], "production_safety_violation")

    def test_arm_protected_false_can_never_succeed(self) -> None:
        wire = self.make_wire(self.successful_arms())
        contents = wire.read_text(encoding="utf-8")
        contents = contents.replace("\ttrue\t0.5\t0.4\t2000000", "\tfalse\t0.5\t0.4\t2000000", 1)
        wire.write_text(contents, encoding="utf-8")
        report = report_wire.parse_report(self.args(wire, 0))
        self.assertEqual(report["status"], "aborted")
        self.assertEqual(report["error_kind"], "production_safety_violation")

    def test_cleanup_failure_keeps_sanitized_prefix_with_missing_digest(self) -> None:
        args = self.args(
            self.make_wire(self.successful_arms()[:1], postflight=False), 70, cleanup=False
        )
        args.protected_postflight_sha256 = ""
        report = report_wire.parse_report(args)
        self.assertEqual(report["status"], "aborted")
        self.assertEqual(len(report["arms"]), 1)
        self.assertFalse(report["report_valid"])

    def test_connection_highwater_above_fixed_pool_is_not_successful(self) -> None:
        arms = self.successful_arms()
        arms[0][1]["final"]["maximum_connections"] = 21
        report = report_wire.parse_report(self.args(self.make_wire(arms), 0))
        self.assertEqual(report["error_kind"], "benchmark_or_source_failure")
        self.assertTrue(report["report_valid"])


if __name__ == "__main__":
    unittest.main()
