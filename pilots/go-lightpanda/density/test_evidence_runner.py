from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from . import controller, evidence_protocol, evidence_runner
except ImportError:
    import controller  # type: ignore[no-redef]
    import evidence_protocol  # type: ignore[no-redef]
    import evidence_runner  # type: ignore[no-redef]


HERE = Path(__file__).resolve().parent
GIB = 1024**3


def raw_resources(
    peak: int,
    *,
    memory_max: int = 0,
    oom: int = 0,
    pids_max: int = 0,
    processes: int = 1,
) -> dict:
    return {
        "host_page_size": 4096,
        "memory_current": min(peak, 64 * 1024**2),
        "memory_peak": peak,
        "memory_limit": GIB,
        "memory_swap_limit": 0,
        "memory_events": {
            "low": 0,
            "high": 0,
            "max": memory_max,
            "oom": oom,
            "oom_kill": oom,
            "oom_group_kill": 0,
        },
        "cpu_max": [100000, 100000],
        "cpu_stat": {"usage_usec": 3, "user_usec": 2, "system_usec": 1},
        "pids_limit": 768,
        "pids_events": {"max": pids_max},
        "pids_current": max(1, processes),
        "pids_peak": 64,
        "cgroup_process_count": processes,
    }


class FakeIdentityGuard:
    identities = {
        "go": "sha256:" + "a" * 64,
        "python": "sha256:" + "b" * 64,
        "fixture": "sha256:" + "c" * 64,
    }

    def __init__(self):
        self.changed = False
        self.verifications = 0

    def resolve(self):
        return dict(self.identities)

    def verify(self, identities):
        self.verifications += 1
        if self.changed or identities != self.identities:
            raise evidence_runner.GuardFailure("containment")


class FakeHealthGuard:
    def __init__(self):
        self.captured = False
        self.unhealthy = False

    def capture(self):
        self.captured = True

    def verify(self):
        if not self.captured or self.unhealthy:
            raise evidence_runner.GuardFailure("containment")


class EvidenceRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schedule, cls.schedule_sha = evidence_protocol.load_schedule(
            HERE / "schedule.v1.json"
        )
        cls.workload, cls.workload_sha = controller.load_manifest(HERE)

    def runner(self, directory, arm_runner, *, identity=None, health=None):
        checkpoints = []
        instance = evidence_runner.EvidenceRunner(
            source_commit="d" * 40,
            schedule=self.schedule,
            schedule_sha256=self.schedule_sha,
            workload=self.workload,
            workload_sha256=self.workload_sha,
            timeout=1,
            output=Path(directory) / "evidence.json",
            identity_guard=identity or FakeIdentityGuard(),
            health_guard=health or FakeHealthGuard(),
            arm_runner=arm_runner,
            checkpoint_writer=lambda report: checkpoints.append(
                json.loads(json.dumps(report))
            ),
        )
        return instance, checkpoints

    def successful_arm(self, pair, _arm_index, implementation, _identities):
        return {
            "measured": {
                "elapsed_ns": 100 if implementation == "go" else 125
            },
            "resources": raw_resources(
                640 * 1024**2
                if implementation == "go"
                else 800 * 1024**2
            ),
        }

    def test_runs_all_frozen_slots_in_order_and_checkpoints_every_arm(self):
        calls = []

        def arm(pair, index, implementation, identities):
            calls.append((pair["id"], index, implementation, dict(identities)))
            return self.successful_arm(pair, index, implementation, identities)

        with tempfile.TemporaryDirectory() as directory:
            runner, checkpoints = self.runner(directory, arm)
            report = runner.run()
        expected = [
            (pair["id"], index, implementation)
            for pair in self.schedule["pairs"]
            for index, implementation in enumerate(pair["order"])
        ]
        self.assertEqual([call[:3] for call in calls], expected)
        self.assertEqual(len(checkpoints), 23)
        self.assertEqual(checkpoints[0]["state"], "initialized")
        self.assertEqual(checkpoints[0]["completed_arms"], 0)
        self.assertEqual(report["state"], "complete")
        self.assertEqual(report["completed_arms"], 22)
        self.assertEqual(report["summary"]["status"], "admitted")
        self.assertTrue(all(item["outcome"] == "pass" for item in report["records"]))
        encoded = json.dumps(report)
        for forbidden in (
            "container",
            "/sys/fs/cgroup",
            "origin-",
            "https://",
            "/proc/",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_bounded_arm_failure_is_retained_and_later_arms_continue(self):
        calls = 0

        def arm(pair, index, implementation, identities):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise controller.SmokeFailure("timeout")
            return self.successful_arm(pair, index, implementation, identities)

        with tempfile.TemporaryDirectory() as directory:
            runner, checkpoints = self.runner(directory, arm)
            report = runner.run()
        self.assertEqual(calls, 22)
        self.assertEqual(len(checkpoints), 23)
        self.assertEqual(report["state"], "complete")
        self.assertEqual(report["records"][0]["failure_id"], "timeout")
        self.assertEqual(report["records"][1]["outcome"], "pass")

    def test_cleanup_failure_aborts_and_preserves_remaining_slots(self):
        calls = 0

        def arm(*_args):
            nonlocal calls
            calls += 1
            raise controller.SmokeFailure(
                "cleanup", resources=raw_resources(32 * 1024**2)
            )

        with tempfile.TemporaryDirectory() as directory:
            runner, checkpoints = self.runner(directory, arm)
            report = runner.run()
        self.assertEqual(calls, 1)
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(report["state"], "aborted")
        self.assertEqual(report["executor_failure_id"], "cleanup")
        self.assertEqual(report["records"][0]["failure_id"], "cleanup")
        self.assertTrue(
            all(
                record["failure_id"] == "not_run"
                for record in report["records"][1:]
            )
        )
        self.assertEqual(
            report["summary"]["reason_ids"], ["protocol_integrity_failure"]
        )

    def test_c1_final_process_residue_aborts_all_later_arms(self):
        calls = 0

        def arm(*_args):
            nonlocal calls
            calls += 1
            raise controller.SmokeFailure(
                "process_residue",
                resources=raw_resources(32 * 1024**2, processes=2),
                resources_final=True,
            )

        with tempfile.TemporaryDirectory() as directory:
            runner, checkpoints = self.runner(directory, arm)
            report = runner.run()
        self.assertEqual(calls, 1)
        self.assertEqual(len(checkpoints), 2)
        self.assertEqual(report["state"], "aborted")
        self.assertEqual(report["executor_failure_id"], "process_residue")
        self.assertEqual(report["records"][0]["failure_id"], "process_residue")
        self.assertTrue(
            all(
                record["failure_id"] == "not_run"
                for record in report["records"][1:]
            )
        )
        self.assertEqual(
            report["summary"]["reason_ids"], ["protocol_integrity_failure"]
        )

    def test_preserves_controller_resource_pressure_attribution(self):
        failures = [
            controller.SmokeFailure(
                "pid_limit",
                resources=raw_resources(
                    GIB + 4096, memory_max=9, oom=2, pids_max=1, processes=0
                ),
            ),
            controller.SmokeFailure(
                "oom",
                resources=raw_resources(
                    GIB + 4096, memory_max=9, oom=2, processes=0
                ),
            ),
            controller.SmokeFailure(
                "memory_pressure",
                resources=raw_resources(
                    GIB, memory_max=1, processes=0
                ),
            ),
        ]
        calls = 0

        def arm(pair, index, implementation, identities):
            nonlocal calls
            if calls < len(failures):
                error = failures[calls]
                calls += 1
                raise error
            calls += 1
            return self.successful_arm(pair, index, implementation, identities)

        with tempfile.TemporaryDirectory() as directory:
            runner, _checkpoints = self.runner(directory, arm)
            report = runner.run()
        self.assertEqual(
            [record["failure_id"] for record in report["records"][:3]],
            ["pid_limit", "oom", "memory_pressure"],
        )
        self.assertEqual(
            report["records"][0]["resources"]["observation"],
            "sampled_not_final",
        )
        self.assertEqual(report["summary"]["status"], "inconclusive")

    def test_does_not_infer_failure_subject_from_measured_counters(self):
        calls = 0

        def arm(pair, index, implementation, identities):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise controller.SmokeFailure(
                    "nonzero",
                    resources=raw_resources(
                        GIB + 4096,
                        memory_max=9,
                        oom=2,
                        pids_max=1,
                        processes=0,
                    ),
                )
            return self.successful_arm(pair, index, implementation, identities)

        with tempfile.TemporaryDirectory() as directory:
            runner, _checkpoints = self.runner(directory, arm)
            report = runner.run()
        self.assertEqual(report["records"][0]["failure_id"], "nonzero")
        self.assertEqual(report["summary"]["status"], "invalid")
        self.assertEqual(
            report["summary"]["reason_ids"], ["evidence_shape_invalid"]
        )

    def test_correctness_failure_is_not_reclassified_as_memory_pressure(self):
        failures = 0

        def arm(pair, index, implementation, identities):
            nonlocal failures
            if pair["concurrency"] == 8 and implementation == "python":
                failures += 1
                raise controller.SmokeFailure(
                    "oracle",
                    resources=raw_resources(
                        GIB, memory_max=1, processes=0
                    ),
                )
            return self.successful_arm(pair, index, implementation, identities)

        with tempfile.TemporaryDirectory() as directory:
            runner, _checkpoints = self.runner(directory, arm)
            report = runner.run()
        self.assertEqual(failures, 5)
        python_c8 = [
            record
            for record in report["records"]
            if record["concurrency"] == 8
            and record["implementation"] == "python"
        ]
        self.assertTrue(
            all(record["failure_id"] == "oracle" for record in python_c8)
        )
        self.assertEqual(report["summary"]["status"], "inconclusive")
        self.assertEqual(
            report["summary"]["reason_ids"], ["python_c8_mixed_failures"]
        )

    def test_repeated_normalized_fixture_ooms_cannot_admit(self):
        fixture_failures = 0

        def arm(pair, index, implementation, identities):
            nonlocal fixture_failures
            if pair["concurrency"] == 8 and implementation == "python":
                fixture_failures += 1
                # SmokeController normalizes any fixture lifecycle failure,
                # including OOMKilled, and omits the measured cgroup sample.
                raise controller.SmokeFailure("infrastructure")
            return self.successful_arm(pair, index, implementation, identities)

        with tempfile.TemporaryDirectory() as directory:
            runner, _checkpoints = self.runner(directory, arm)
            report = runner.run()
        self.assertEqual(fixture_failures, 5)
        self.assertEqual(report["summary"]["status"], "inconclusive")
        self.assertEqual(
            report["summary"]["reason_ids"], ["python_c8_mixed_failures"]
        )
        python_c8 = [
            record
            for record in report["records"]
            if record["concurrency"] == 8
            and record["implementation"] == "python"
        ]
        self.assertTrue(
            all(
                record["failure_id"] == "infrastructure"
                and record["resources"] is None
                for record in python_c8
            )
        )

    def test_invalid_resource_overshoot_is_dropped_without_clamping(self):
        calls = 0

        def arm(pair, index, implementation, identities):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise controller.SmokeFailure(
                    "oom",
                    resources=raw_resources(
                        GIB + 8192, memory_max=1, oom=1, processes=0
                    ),
                )
            return self.successful_arm(pair, index, implementation, identities)

        with tempfile.TemporaryDirectory() as directory:
            runner, _checkpoints = self.runner(directory, arm)
            report = runner.run()
        self.assertIsNone(report["records"][0]["resources"])
        self.assertEqual(report["records"][0]["failure_id"], "oom")

    def test_failure_after_final_marker_keeps_final_observation(self):
        calls = 0

        def arm(pair, index, implementation, identities):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise controller.SmokeFailure(
                    "oracle",
                    resources=raw_resources(32 * 1024**2),
                    resources_final=True,
                )
            return self.successful_arm(pair, index, implementation, identities)

        with tempfile.TemporaryDirectory() as directory:
            runner, _checkpoints = self.runner(directory, arm)
            report = runner.run()
        self.assertEqual(report["records"][0]["failure_id"], "oracle")
        self.assertEqual(
            report["records"][0]["resources"]["observation"], "final"
        )
        self.assertTrue(report["records"][0]["resources"]["final_sample"])

    def test_constructor_requires_exact_frozen_schedule_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(evidence_runner.GuardFailure) as caught:
                evidence_runner.EvidenceRunner(
                    source_commit="d" * 40,
                    schedule=self.schedule,
                    schedule_sha256="0" * 64,
                    workload=self.workload,
                    workload_sha256=self.workload_sha,
                    timeout=1,
                    output=Path(directory) / "evidence.json",
                    identity_guard=FakeIdentityGuard(),
                    health_guard=FakeHealthGuard(),
                    arm_runner=self.successful_arm,
                )
        self.assertEqual(caught.exception.failure_id, "infrastructure")

    def test_post_arm_image_mutation_converts_current_slot_and_aborts(self):
        identity = FakeIdentityGuard()

        def arm(pair, index, implementation, identities):
            identity.changed = True
            return self.successful_arm(pair, index, implementation, identities)

        with tempfile.TemporaryDirectory() as directory:
            runner, _checkpoints = self.runner(
                directory, arm, identity=identity
            )
            report = runner.run()
        self.assertEqual(report["state"], "aborted")
        self.assertEqual(report["executor_failure_id"], "containment")
        self.assertEqual(report["records"][0]["failure_id"], "containment")
        self.assertIsNotNone(report["records"][0]["resources"])

    def test_unexpected_exception_is_normalized_without_leaking_text(self):
        def arm(*_args):
            raise RuntimeError("https://secret.invalid/private")

        with tempfile.TemporaryDirectory() as directory:
            runner, _checkpoints = self.runner(directory, arm)
            report = runner.run()
        self.assertEqual(report["state"], "complete")
        self.assertTrue(
            all(
                record["failure_id"] == "infrastructure"
                for record in report["records"]
            )
        )
        self.assertNotIn("secret", json.dumps(report))

    def test_actual_adapter_uses_fresh_tokens_and_frozen_pids_limit(self):
        captured = []

        class FakeSmoke:
            def __init__(self, *_args, run_id, **_kwargs):
                self.run_id = run_id

            def run_arm(self, implementation, image, fixture, **kwargs):
                captured.append(
                    (self.run_id, implementation, image, fixture, kwargs)
                )
                return self_outer.successful_arm({}, 0, implementation, {})

        self_outer = self
        tokens = iter(("one", "two"))
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            evidence_runner.controller, "SmokeController", FakeSmoke
        ):
            runner, _checkpoints = self.runner(directory, self.successful_arm)
            runner.token_factory = lambda: next(tokens)
            runner._run_arm(
                self.schedule["pairs"][0], 0, "python", FakeIdentityGuard.identities
            )
            runner._run_arm(
                self.schedule["pairs"][0], 1, "go", FakeIdentityGuard.identities
            )
        self.assertEqual([item[0] for item in captured], ["one", "two"])
        for _token, implementation, image, fixture, kwargs in captured:
            self.assertEqual(image, FakeIdentityGuard.identities[implementation])
            self.assertEqual(fixture, FakeIdentityGuard.identities["fixture"])
            self.assertEqual(kwargs["measured_pids"], 768)
            self.assertTrue(kwargs["retain_failure_resources"])

    def test_default_checkpoint_is_atomic_private_and_final(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = FakeIdentityGuard()
            health = FakeHealthGuard()
            output = Path(directory) / "evidence.json"
            runner = evidence_runner.EvidenceRunner(
                source_commit="d" * 40,
                schedule=self.schedule,
                schedule_sha256=self.schedule_sha,
                workload=self.workload,
                workload_sha256=self.workload_sha,
                timeout=1,
                output=output,
                identity_guard=identity,
                health_guard=health,
                arm_runner=self.successful_arm,
            )
            report = runner.run()
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(output.read_text()), report)


class GuardTests(unittest.TestCase):
    def test_image_guard_resolves_once_and_detects_tag_mutation(self):
        identities = [
            "sha256:" + "a" * 64,
            "sha256:" + "b" * 64,
            "sha256:" + "c" * 64,
        ]
        docker = mock.Mock()
        inspected = [
            {
                "Id": value,
                "Config": {
                    "Labels": {
                        evidence_runner.PROVENANCE_LABEL: "d" * 40,
                    }
                },
            }
            for value in identities * 2
        ]
        docker.json.side_effect = [[item] for item in inspected]
        guard = evidence_runner.DockerImageGuard(
            docker,
            {"go": "g", "python": "p", "fixture": "f"},
            "d" * 40,
        )
        resolved = guard.resolve()
        guard.verify(resolved)
        docker.json.side_effect = [
            [{**inspected[0], "Id": "sha256:" + "d" * 64}],
            [inspected[1]],
            [inspected[2]],
        ]
        with self.assertRaises(evidence_runner.GuardFailure) as caught:
            guard.verify(resolved)
        self.assertEqual(caught.exception.failure_id, "containment")

    def test_image_guard_rejects_missing_or_stale_source_provenance(self):
        docker = mock.Mock()
        docker.json.return_value = [{
            "Id": "sha256:" + "a" * 64,
            "Config": {"Labels": {
                evidence_runner.PROVENANCE_LABEL: "c" * 40,
            }},
        }]
        guard = evidence_runner.DockerImageGuard(
            docker,
            {"go": "g", "python": "p", "fixture": "f"},
            "d" * 40,
        )
        with self.assertRaises(evidence_runner.GuardFailure) as caught:
            guard.resolve()
        self.assertEqual(caught.exception.failure_id, "image")

    def test_protected_guard_requires_stable_healthy_container(self):
        name = "deploy-murmur-1"
        base = {
            "Id": "a" * 64,
            "Image": "sha256:" + "b" * 64,
            "RestartCount": 0,
            "State": {
                "Status": "running",
                "Running": True,
                "Restarting": False,
                "Dead": False,
                "StartedAt": "2026-01-01T00:00:00Z",
                "Health": {"Status": "healthy"},
            },
        }
        docker = mock.Mock()
        docker.json.return_value = [base]
        guard = evidence_runner.ProtectedContainerGuard(docker, (name,))
        guard.capture()
        guard.verify()
        changed = json.loads(json.dumps(base))
        changed["RestartCount"] = 1
        docker.json.return_value = [changed]
        with self.assertRaises(evidence_runner.GuardFailure):
            guard.verify()
        self.assertNotIn(name, str(guard._baseline))

    def test_invalid_protected_name_is_closed(self):
        with self.assertRaises(evidence_runner.GuardFailure) as caught:
            evidence_runner.ProtectedContainerGuard(mock.Mock(), ("--all",))
        self.assertEqual(caught.exception.failure_id, "containment")


if __name__ == "__main__":
    unittest.main()
