from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("density_controller", HERE / "controller.py")
assert SPEC is not None and SPEC.loader is not None
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)


def failure_id(callable_):
    with unittest.TestCase().assertRaises(controller.SmokeFailure) as caught:
        callable_()
    return caught.exception.failure_id


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workload, cls.workload_sha = controller.load_manifest(HERE)
        cls.tasks = controller.expected_tasks(cls.workload)

    def fixture_report(self):
        waves = []
        for wave in self.workload["waves"]:
            task_ids = [task["id"] for task in wave["tasks"]]
            waves.append({
                "id": wave["id"],
                "completed": 8,
                "task_ids": task_ids,
                "response_sha256": [
                    hashlib.sha256(self.tasks[task_id]["response_body"].encode()).hexdigest()
                    for task_id in task_ids
                ],
                "barrier_reached": True,
            })
        return {
            "schema_version": 1,
            "workload_sha256": self.workload_sha,
            "concurrency": 4,
            "completed": 16,
            "max_global_in_flight": 4,
            "max_per_origin_in_flight": 1,
            "waves": waves,
            "unexpected": {"method": 0, "path": 0, "origin": 0, "duplicate": 0, "total": 0},
        }

    def python_report(self):
        entries = []
        for task in self.tasks.values():
            evaluation = task["expected_evaluation_sha256"] is not None
            entries.append({
                "id": task["id"], "mode": task["mode"], "outcome": "success",
                "status_matches": True, "final_url_matches": True, "html_matches": True,
                "html_bytes": task["expected_outer_html_size"],
                "html_sha256": task["expected_outer_html_sha256"],
                "evaluation_present": evaluation, "evaluation_matches": True,
                "evaluation_bytes": len(task["expected_evaluation_json"].encode("ascii")) if evaluation else 0,
                "evaluation_sha256": task["expected_evaluation_sha256"],
                "cleanup_ok": True,
            })
        return {
            "schema_version": 1, "implementation": "python-chromium",
            "workload_sha256": self.workload_sha,
            "source_commit": "a" * 40, "image_identity": "sha256:" + "b" * 64,
            "concurrency": 4, "ok": True, "accepted": 16, "completed": 16,
            "succeeded": 16, "max_in_flight": 4, "elapsed_ns": 1,
            "driver_teardown_ok": True,
            "conservation": {"accepted": 16, "completed": 16, "succeeded": 16, "failed": 0, "queued": 0, "in_flight": 0},
            "tasks": entries,
        }

    def go_report(self):
        waves = []
        for raw_wave in self.workload["waves"]:
            jobs = []
            for task in raw_wave["tasks"]:
                evaluation = task["expected_evaluation_sha256"] is not None
                job = {
                    "id": task["id"], "mode_id": task["mode"], "terminal": True,
                    "succeeded": True, "oracle_match": True, "final_url_match": True,
                    "status": task["status"], "status_match": True,
                    "html_bytes": task["expected_outer_html_size"],
                    "html_sha256": task["expected_outer_html_sha256"], "html_match": True,
                    "evaluation_present": evaluation,
                    "evaluation_bytes": len(task["expected_evaluation_json"].encode("ascii")) if evaluation else 0,
                    "evaluation_match": True,
                }
                if evaluation:
                    job["evaluation_sha256"] = task["expected_evaluation_sha256"]
                jobs.append(job)
            waves.append({
                "id": raw_wave["id"], "succeeded": True, "submitted": 8,
                "terminal": 8, "succeeded_jobs": 8, "failed_jobs": 0,
                "oracle_matches": 8, "max_in_flight": 4, "jobs": jobs,
            })
        return {
            "schema_version": 1, "implementation_id": "go-lightpanda",
            "runtime_id": "go1.24.0", "workload_sha256": self.workload_sha,
            "source_commit": "a" * 40, "image_identity": "sha256:" + "b" * 64,
            "concurrency": 4, "succeeded": True, "elapsed_ns": 1,
            "submitted": 16, "accepted": 16, "terminal": 16,
            "succeeded_jobs": 16, "failed_jobs": 0, "oracle_matches": 16,
            "conservation_ok": True, "oracle_ok": True, "max_in_flight_ok": True,
            "zero_panic_residue": True, "waves": waves,
            "pool": {"accepted": 16, "completed": 16, "panics": 0, "queued": 0,
                     "in_flight": 0, "max_queued": 8, "max_in_flight": 4,
                     "reserved_port_count": 0},
        }

    def test_json_requires_one_bounded_object(self):
        self.assertEqual(controller.parse_json_object(b'{"ok":true}\n'), {"ok": True})
        for raw in (b"", b"[]", b"{}{}", b'{"x":NaN}', b"\xff"):
            with self.subTest(raw=raw):
                self.assertEqual(failure_id(lambda raw=raw: controller.parse_json_object(raw)), "malformed_output")

    def test_fixture_prefix_is_unique_and_raw_output_is_forbidden(self):
        report = self.fixture_report()
        encoded = b"DENSITY_FIXTURE_REPORT=" + json.dumps(report).encode() + b"\n"
        self.assertEqual(controller.parse_fixture_output(encoded), report)
        for mutated in (encoded + b"raw diagnostic\n", encoded + encoded, b"{}\n"):
            with self.subTest(mutated=mutated[-20:]):
                self.assertEqual(failure_id(lambda mutated=mutated: controller.parse_fixture_output(mutated)), "transcript")

    def test_fixture_transcript_mutations_fail_closed(self):
        valid = self.fixture_report()
        controller.validate_fixture(valid, 4, self.workload_sha, self.tasks)
        mutations = [
            ("completed", 15, "conservation"),
            ("max_global_in_flight", 3, "concurrency"),
            ("max_per_origin_in_flight", 2, "conservation"),
        ]
        for key, value, want in mutations:
            report = copy.deepcopy(valid)
            report[key] = value
            with self.subTest(key=key):
                self.assertEqual(failure_id(lambda report=report: controller.validate_fixture(report, 4, self.workload_sha, self.tasks)), want)
        report = copy.deepcopy(valid)
        report["unexpected"]["duplicate"] = 1
        self.assertEqual(failure_id(lambda: controller.validate_fixture(report, 4, self.workload_sha, self.tasks)), "transcript")
        report = copy.deepcopy(valid)
        report["waves"][0]["barrier_reached"] = False
        self.assertEqual(failure_id(lambda: controller.validate_fixture(report, 4, self.workload_sha, self.tasks)), "transcript")
        report = copy.deepcopy(valid)
        report["waves"][0]["response_sha256"][0] = "0" * 64
        self.assertEqual(failure_id(lambda: controller.validate_fixture(report, 4, self.workload_sha, self.tasks)), "oracle")

    def test_python_report_conservation_oracle_and_concurrency(self):
        valid = self.python_report()
        controller.validate_measured(valid, "python", 4, "a" * 40, "sha256:" + "b" * 64,
                                     self.workload_sha, self.tasks)
        cases = [
            (("conservation", "completed"), 15, "conservation"),
            (("tasks", 0, "html_matches"), False, "oracle"),
            (("max_in_flight",), 3, "concurrency"),
            (("driver_teardown_ok",), False, "oracle"),
        ]
        for path, value, want in cases:
            report = copy.deepcopy(valid)
            target = report
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            with self.subTest(path=path):
                self.assertEqual(failure_id(lambda report=report: controller.validate_measured(
                    report, "python", 4, "a" * 40, "sha256:" + "b" * 64,
                    self.workload_sha, self.tasks)), want)

    def test_go_report_and_evaluation_mutations(self):
        valid = self.go_report()
        controller.validate_measured(valid, "go", 4, "a" * 40, "sha256:" + "b" * 64,
                                     self.workload_sha, self.tasks)
        cases = [
            (("waves", 0, "jobs", 4, "evaluation_sha256"), "0" * 64, "oracle"),
            (("waves", 0, "jobs", 0, "evaluation_present"), True, "oracle"),
            (("pool", "max_in_flight"), 3, "concurrency"),
            (("failure_id",), "https://secret.invalid", "oracle"),
        ]
        for path, value, want in cases:
            report = copy.deepcopy(valid)
            target = report
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
            with self.subTest(path=path):
                self.assertEqual(failure_id(lambda report=report: controller.validate_measured(
                    report, "go", 4, "a" * 40, "sha256:" + "b" * 64,
                    self.workload_sha, self.tasks)), want)


class InspectTests(unittest.TestCase):
    def valid_inspect(self):
        return {
            "Image": "sha256:" + "b" * 64,
            "State": {"Status": "created", "Running": False, "Restarting": False, "Dead": False, "Pid": 0, "ExitCode": 0, "OOMKilled": False},
            "Config": {"Image": "sha256:" + "b" * 64, "User": "10001:10001", "Env": ["PATH=/usr/bin", "HOME=/tmp"], "Labels": {controller.LABEL_RUN: "run", controller.LABEL_ROLE: "fixture"}, "Volumes": None, "ExposedPorts": None},
            "HostConfig": {
                "NetworkMode": "network", "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges=true"], "RestartPolicy": {"Name": "no"},
                "Privileged": False, "CapAdd": None, "Devices": [], "DeviceRequests": None,
                "PidMode": "", "IpcMode": "private", "UsernsMode": "", "PortBindings": {},
                "PublishAllPorts": False, "Binds": None,
                "Tmpfs": {"/tmp": controller.FIXTURE_TMPFS},
                "NanoCpus": 1_000_000_000, "Memory": controller.FIXTURE_MEMORY,
                "MemorySwap": controller.FIXTURE_MEMORY, "PidsLimit": controller.FIXTURE_PIDS,
                "Ulimits": [{"Name": "nofile", "Soft": 256, "Hard": 256}],
            },
            "Mounts": [],
            "NetworkSettings": {"Ports": {}, "Networks": {"network": {"Aliases": list(controller.ALIASES)}}},
        }

    def test_inspect_mutations_are_rejected_before_start(self):
        valid = self.valid_inspect()
        controller.attest_container(valid, "network", "fixture", "run", "sha256:" + "b" * 64, measured=False)
        mutators = [
            lambda x: x["State"].update(Status="running", Running=True),
            lambda x: x.update(Image="sha256:" + "c" * 64),
            lambda x: x["Config"].update(User="0:0"),
            lambda x: x["HostConfig"].update(ReadonlyRootfs=False),
            lambda x: x["HostConfig"].update(CapDrop=[]),
            lambda x: x["HostConfig"].update(CapAdd=["SYS_ADMIN"]),
            lambda x: x["HostConfig"].update(SecurityOpt=[]),
            lambda x: x["HostConfig"].update(SecurityOpt=["no-new-privileges=true", "seccomp=unconfined"]),
            lambda x: x["HostConfig"].update(Privileged=True),
            lambda x: x["HostConfig"].update(Devices=[{"PathOnHost": "/dev/kvm"}]),
            lambda x: x["HostConfig"].update(DeviceRequests=[{"Count": -1}]),
            lambda x: x["HostConfig"].update(PidMode="host"),
            lambda x: x["HostConfig"].update(IpcMode="host"),
            lambda x: x["HostConfig"].update(UsernsMode="host"),
            lambda x: x["HostConfig"].update(PortBindings={"80/tcp": [{"HostPort": "8080"}]}),
            lambda x: x["HostConfig"].update(PublishAllPorts=True),
            lambda x: x["Config"].update(ExposedPorts={"80/tcp": {}}),
            lambda x: x["HostConfig"].update(Binds=["/tmp:/tmp"]),
            lambda x: x["HostConfig"].update(Tmpfs={"/tmp": controller.FIXTURE_TMPFS + ",exec"}),
            lambda x: x.update(Mounts=[{"Source": "/var/run/docker.sock"}]),
            lambda x: x["NetworkSettings"]["Networks"]["network"].update(Aliases=list(controller.ALIASES[:-1])),
        ]
        for mutate in mutators:
            inspect = copy.deepcopy(valid)
            mutate(inspect)
            with self.subTest(inspect=inspect):
                self.assertEqual(failure_id(lambda inspect=inspect: controller.attest_container(inspect, "network", "fixture", "run", "sha256:" + "b" * 64, measured=False)), "inspect")

    def test_measured_limits_and_exit_state_are_fail_closed(self):
        inspect = self.valid_inspect()
        inspect["Config"]["Labels"][controller.LABEL_ROLE] = "go"
        inspect["NetworkSettings"]["Networks"]["network"]["Aliases"] = []
        inspect["HostConfig"].update(Memory=controller.GIB, MemorySwap=controller.GIB, PidsLimit=controller.MEASURED_PIDS, Tmpfs={"/tmp": controller.MEASURED_TMPFS})
        controller.attest_container(inspect, "network", "go", "run", "sha256:" + "b" * 64, measured=True)
        for key, value in (("Memory", 1), ("MemorySwap", 2 * controller.GIB), ("PidsLimit", controller.MEASURED_PIDS + 1), ("NanoCpus", 2_000_000_000)):
            mutated = copy.deepcopy(inspect)
            mutated["HostConfig"][key] = value
            self.assertEqual(failure_id(lambda mutated=mutated: controller.attest_container(mutated, "network", "go", "run", "sha256:" + "b" * 64, measured=True)), "inspect")
        exited = {"State": {"Status": "exited", "Running": False, "Restarting": False, "Dead": False, "Pid": 0, "OOMKilled": False, "ExitCode": 0}}
        controller.inspect_exit(exited)
        oom = {"State": {**exited["State"], "OOMKilled": True, "ExitCode": 137}}
        self.assertEqual(failure_id(lambda: controller.inspect_exit(oom)), "oom")
        nonzero = {"State": {**exited["State"], "ExitCode": 2}}
        self.assertEqual(failure_id(lambda: controller.inspect_exit(nonzero)), "nonzero")
        for mutation in ({"Status": "running", "Running": True, "Pid": 9}, {"Restarting": True}, {"Dead": True}, {"Pid": 9}):
            state = copy.deepcopy(exited)
            state["State"].update(mutation)
            self.assertEqual(failure_id(lambda state=state: controller.inspect_exit(state)), "inspect")

    def test_go_nonzero_maps_only_allowlisted_sanitized_runner_failures(self):
        inspect = {"State": {"Status": "exited", "Running": False, "Restarting": False,
                             "Dead": False, "Pid": 0, "OOMKilled": False, "ExitCode": 2}}
        docker = mock.Mock()
        docker.logs.return_value = b'{"failure_id":"invariant_failure"}\n'
        self.assertEqual(
            failure_id(lambda: controller.inspect_measured_exit(docker, "container", "go", inspect)),
            "go_runner_invariant_failure",
        )
        for implementation, output in (
            ("python", b'{"failure_id":"invariant_failure"}\n'),
            ("go", b'{"failure_id":"https://secret.invalid"}\n'),
            ("go", b'{"failure_id":[]}\n'),
            ("go", b'{"failure_id":{}}\n'),
            ("go", b'not-json'),
        ):
            docker.logs.return_value = output
            with self.subTest(implementation=implementation, output=output):
                self.assertEqual(
                    failure_id(lambda implementation=implementation: controller.inspect_measured_exit(
                        docker, "container", implementation, inspect,
                    )),
                    "nonzero",
                )

    def test_network_labels_and_shape_are_fail_closed(self):
        valid = [{"Internal": True, "Labels": {
            controller.LABEL_RUN: "run", controller.LABEL_ROLE: "network",
        }}]
        controller.attest_network(valid, "run")
        for mutated in ([], [None], [{"Internal": True, "Labels": None}],
                        [{"Internal": False, "Labels": valid[0]["Labels"]}]):
            with self.subTest(mutated=mutated):
                self.assertEqual(
                    failure_id(lambda mutated=mutated: controller.attest_network(mutated, "run")),
                    "inspect",
                )


class FakeDocker:
    def __init__(self, mode):
        self.mode = mode
        self.calls = []

    def run(self, args, timeout=15, check=True):
        self.calls.append(list(args))
        if self.mode == "timeout" and args[0] == "wait":
            raise controller.SmokeFailure("command")
        if self.mode == "cleanup_command" and args[:2] == ["ps", "-aq"]:
            raise controller.SmokeFailure("command")
        if self.mode == "signal_output" and args[:3] == ["kill", "--signal", "USR1"]:
            return subprocess.CompletedProcess(args, 0, b"unexpected", b"")
        if args[:3] in (["kill", "--signal", "USR1"], ["kill", "--signal", "USR2"]):
            return subprocess.CompletedProcess(args, 0, args[3].encode() + b"\n", b"")
        if args[:2] == ["ps", "-aq"]:
            return subprocess.CompletedProcess(args, 0, b"c" * 64 + b"\n", b"")
        if args[:3] == ["network", "ls", "--format"]:
            return subprocess.CompletedProcess(args, 0, b"network\n", b"")
        return subprocess.CompletedProcess(args, 0, b"", b"")

    def json(self, args, timeout=15):
        self.calls.append(list(args))
        if self.mode == "malformed_labels":
            return [{"Config": {"Labels": None}}]
        return [{"Config": {"Labels": {controller.LABEL_RUN: "run", controller.LABEL_ROLE: "attacker"}}}]

    def logs(self, _container):
        return b"{}"


class LifecycleTests(unittest.TestCase):
    def smoke(self, docker):
        workload, workload_sha = controller.load_manifest(HERE)
        return controller.SmokeController(docker, "a" * 40, 4, 1, workload, workload_sha, run_id="run")

    def test_wait_timeout_always_kills(self):
        docker = FakeDocker("timeout")
        smoke = self.smoke(docker)
        self.assertEqual(failure_id(lambda: smoke._wait("c" * 64, 0.01)), "timeout")
        self.assertIn(["kill", "c" * 64], docker.calls)

    def test_phase_signals_are_exact_and_validate_docker_output(self):
        docker = FakeDocker("ok")
        smoke = self.smoke(docker)
        smoke._signal("c" * 64, "USR1")
        smoke._signal("c" * 64, "USR2")
        self.assertEqual(
            docker.calls,
            [
                ["kill", "--signal", "USR1", "c" * 64],
                ["kill", "--signal", "USR2", "c" * 64],
            ],
        )
        self.assertEqual(
            failure_id(lambda: self.smoke(FakeDocker("signal_output"))._signal("c" * 64, "USR1")),
            "command",
        )
        self.assertEqual(
            failure_id(lambda: smoke._signal("c" * 64, "TERM")), "command"
        )

    def test_cleanup_refuses_label_mutation(self):
        docker = FakeDocker("cleanup")
        smoke = self.smoke(docker)
        self.assertEqual(failure_id(lambda: smoke._cleanup("network")), "cleanup")
        self.assertFalse(any(call[:2] == ["rm", "-f"] for call in docker.calls))
        self.assertTrue(any(call[:3] == ["ps", "-aq", "--no-trunc"] for call in docker.calls))
        self.assertTrue(any(call[:3] == ["network", "ls", "--format"] for call in docker.calls))

    def test_cleanup_normalizes_null_labels(self):
        docker = FakeDocker("malformed_labels")
        self.assertEqual(failure_id(lambda: self.smoke(docker)._cleanup("network")), "cleanup")

    def test_cleanup_normalizes_docker_command_failure(self):
        docker = FakeDocker("cleanup_command")
        self.assertEqual(
            failure_id(lambda: self.smoke(docker)._cleanup("network")),
            "cleanup",
        )

    def test_image_identity_rejects_malformed_docker_shapes(self):
        class ImageDocker:
            def __init__(self, value):
                self.value = value

            def json(self, _args):
                return self.value

        for value in ({}, [], [None], [{"Id": "latest"}], [{"Id": "sha256:" + "b" * 64}, {}]):
            with self.subTest(value=value):
                self.assertEqual(failure_id(lambda value=value: self.smoke(ImageDocker(value)).image_identity("tag")), "image")
        self.assertEqual(
            self.smoke(ImageDocker([{"Id": "sha256:" + "b" * 64}])).image_identity("tag"),
            "sha256:" + "b" * 64,
        )

    def test_exit_before_final_stops_sampler_once_and_retains_oom(self):
        smoke = self.smoke(FakeDocker("ok"))
        identity = mock.Mock()
        identity.only_main_process.return_value = True
        resources = {
            "memory_events": {
                "low": 0, "high": 0, "max": 3, "oom": 1,
                "oom_kill": 1, "oom_group_kill": 0,
            },
            "pids_events": {"max": 0},
        }
        sampler = mock.Mock()
        sampler.stop.return_value = resources
        exited = {
            "State": {
                "Status": "exited", "Running": False,
                "Restarting": False, "Dead": False, "Pid": 0,
                "ExitCode": 137, "OOMKilled": True,
            }
        }
        with (
            mock.patch.object(smoke, "image_identity", side_effect=[
                "sha256:" + "a" * 64, "sha256:" + "b" * 64,
            ]),
            mock.patch.object(smoke, "_create", side_effect=["f" * 64, "m" * 64]),
            mock.patch.object(smoke, "_start"),
            mock.patch.object(smoke, "_cleanup"),
            mock.patch.object(smoke, "_inspect_one", side_effect=[
                {}, {}, {"State": {"Pid": 123}}, exited,
            ]),
            mock.patch.object(controller, "attest_network"),
            mock.patch.object(controller, "attest_container"),
            mock.patch.object(controller.ProcessIdentity, "capture", return_value=identity),
            mock.patch.object(controller, "wait_process_phase", side_effect=["matched", "exited"]),
            mock.patch.object(controller.CgroupSampler, "from_identity", return_value=sampler),
        ):
            with self.assertRaises(controller.SmokeFailure) as caught:
                smoke.run_arm(
                    "go", "go", "fixture", retain_failure_resources=True
                )
        self.assertEqual(caught.exception.failure_id, "oom")
        self.assertIs(caught.exception.resources, resources)
        self.assertFalse(caught.exception.resources_final)
        sampler.stop.assert_called_once_with(reject_oom=False)

    def test_exit_before_initial_retains_kernel_pressure(self):
        smoke = self.smoke(FakeDocker("ok"))
        identity = mock.Mock()
        resources = {
            "memory_events": {
                "low": 0, "high": 0, "max": 2, "oom": 1,
                "oom_kill": 1, "oom_group_kill": 0,
            },
            "pids_events": {"max": 0},
        }
        sampler = mock.Mock()
        sampler.stop.return_value = resources
        with (
            mock.patch.object(smoke, "image_identity", side_effect=[
                "sha256:" + "a" * 64, "sha256:" + "b" * 64,
            ]),
            mock.patch.object(smoke, "_create", side_effect=["f" * 64, "m" * 64]),
            mock.patch.object(smoke, "_start"),
            mock.patch.object(smoke, "_cleanup"),
            mock.patch.object(smoke, "_inspect_one", side_effect=[
                {}, {}, {"State": {"Pid": 123}},
            ]),
            mock.patch.object(controller, "attest_network"),
            mock.patch.object(controller, "attest_container"),
            mock.patch.object(controller.ProcessIdentity, "capture", return_value=identity),
            mock.patch.object(controller, "wait_process_phase", return_value="exited"),
            mock.patch.object(controller.CgroupSampler, "from_identity", return_value=sampler),
        ):
            with self.assertRaises(controller.SmokeFailure) as caught:
                smoke.run_arm(
                    "go", "go", "fixture", retain_failure_resources=True
                )
        self.assertEqual(caught.exception.failure_id, "oom")
        self.assertIs(caught.exception.resources, resources)
        self.assertFalse(caught.exception.resources_final)
        sampler.stop.assert_called_once_with(reject_oom=False)

    def test_fixture_failures_are_not_reclassified_from_measured_pressure(self):
        measured_exit = {
            "State": {
                "Status": "exited", "Running": False,
                "Restarting": False, "Dead": False, "Pid": 0,
                "ExitCode": 0, "OOMKilled": False,
            }
        }
        fixture_states = {
            "timeout": measured_exit,
            "nonzero": {
                "State": {**measured_exit["State"], "ExitCode": 2},
            },
            "oom": {
                "State": {
                    **measured_exit["State"], "ExitCode": 137,
                    "OOMKilled": True,
                },
            },
        }
        for fixture_failure in ("timeout", "nonzero", "oom"):
            with self.subTest(fixture_failure=fixture_failure):
                smoke = self.smoke(FakeDocker("ok"))
                identity = mock.Mock()
                identity.only_main_process.return_value = True
                resources = {
                    "memory_events": {
                        "low": 0, "high": 0, "max": 3,
                        "oom": int(fixture_failure != "oom"),
                        "oom_kill": int(fixture_failure != "oom"),
                        "oom_group_kill": 0,
                    },
                    "pids_events": {"max": 0},
                }
                sampler = mock.Mock()
                sampler.stop.return_value = resources
                waits = [None]
                if fixture_failure == "timeout":
                    waits.append(controller.SmokeFailure("timeout"))
                else:
                    waits.append(None)
                with (
                    mock.patch.object(smoke, "image_identity", side_effect=[
                        "sha256:" + "a" * 64, "sha256:" + "b" * 64,
                    ]),
                    mock.patch.object(smoke, "_create", side_effect=["f" * 64, "m" * 64]),
                    mock.patch.object(smoke, "_start"),
                    mock.patch.object(smoke, "_signal"),
                    mock.patch.object(smoke, "_wait", side_effect=waits),
                    mock.patch.object(smoke, "_cleanup"),
                    mock.patch.object(smoke, "_inspect_one", side_effect=[
                        {}, {}, {"State": {"Pid": 123}}, measured_exit,
                        fixture_states[fixture_failure],
                    ]),
                    mock.patch.object(controller, "attest_network"),
                    mock.patch.object(controller, "attest_container"),
                    mock.patch.object(controller.ProcessIdentity, "capture", return_value=identity),
                    mock.patch.object(controller, "wait_process_phase", side_effect=["matched", "matched"]),
                    mock.patch.object(controller.CgroupSampler, "from_identity", return_value=sampler),
                ):
                    with self.assertRaises(controller.SmokeFailure) as caught:
                        smoke.run_arm(
                            "go", "go", "fixture",
                            retain_failure_resources=True,
                        )
                self.assertEqual(caught.exception.failure_id, "infrastructure")
                self.assertIsNone(caught.exception.resources)
                self.assertFalse(caught.exception.resources_final)
                sampler.stop.assert_called_once_with(
                    reject_oom=False, allow_fallback=False,
                )

    def test_cleanup_failure_overrides_fixture_failure_and_aborts(self):
        smoke = self.smoke(FakeDocker("ok"))
        identity = mock.Mock()
        identity.only_main_process.return_value = True
        resources = {
            "memory_events": {
                "low": 0, "high": 0, "max": 3, "oom": 1,
                "oom_kill": 1, "oom_group_kill": 0,
            },
            "pids_events": {"max": 0},
        }
        sampler = mock.Mock()
        sampler.stop.return_value = resources
        measured_exit = {
            "State": {
                "Status": "exited", "Running": False,
                "Restarting": False, "Dead": False, "Pid": 0,
                "ExitCode": 0, "OOMKilled": False,
            }
        }
        with (
            mock.patch.object(smoke, "image_identity", side_effect=[
                "sha256:" + "a" * 64, "sha256:" + "b" * 64,
            ]),
            mock.patch.object(smoke, "_create", side_effect=["f" * 64, "m" * 64]),
            mock.patch.object(smoke, "_start"),
            mock.patch.object(smoke, "_signal"),
            mock.patch.object(
                smoke, "_wait",
                side_effect=[None, controller.SmokeFailure("timeout")],
            ),
            mock.patch.object(
                smoke, "_cleanup", side_effect=controller.SmokeFailure("cleanup")
            ),
            mock.patch.object(smoke, "_inspect_one", side_effect=[
                {}, {}, {"State": {"Pid": 123}}, measured_exit,
            ]),
            mock.patch.object(controller, "attest_network"),
            mock.patch.object(controller, "attest_container"),
            mock.patch.object(
                controller.ProcessIdentity, "capture", return_value=identity
            ),
            mock.patch.object(
                controller, "wait_process_phase", side_effect=["matched", "matched"]
            ),
            mock.patch.object(
                controller.CgroupSampler, "from_identity", return_value=sampler
            ),
        ):
            with self.assertRaises(controller.SmokeFailure) as caught:
                smoke.run_arm(
                    "go", "go", "fixture", retain_failure_resources=True
                )
        self.assertEqual(caught.exception.failure_id, "cleanup")
        self.assertIsNone(caught.exception.resources)
        self.assertFalse(caught.exception.resources_final)

    def test_fixture_validation_failures_are_infrastructure(self):
        exited = {
            "State": {
                "Status": "exited", "Running": False,
                "Restarting": False, "Dead": False, "Pid": 0,
                "ExitCode": 0, "OOMKilled": False,
            }
        }
        for fixture_failure in ("transcript", "oracle"):
            with self.subTest(fixture_failure=fixture_failure):
                smoke = self.smoke(FakeDocker("ok"))
                identity = mock.Mock()
                identity.only_main_process.return_value = True
                sampler = mock.Mock()
                sampler.stop.return_value = {
                    "memory_events": {
                        "low": 0, "high": 0, "max": 3, "oom": 1,
                        "oom_kill": 1, "oom_group_kill": 0,
                    },
                    "pids_events": {"max": 0},
                }
                with (
                    mock.patch.object(smoke, "image_identity", side_effect=[
                        "sha256:" + "a" * 64, "sha256:" + "b" * 64,
                    ]),
                    mock.patch.object(
                        smoke, "_create", side_effect=["f" * 64, "m" * 64]
                    ),
                    mock.patch.object(smoke, "_start"),
                    mock.patch.object(smoke, "_signal"),
                    mock.patch.object(smoke, "_wait"),
                    mock.patch.object(smoke, "_cleanup"),
                    mock.patch.object(smoke, "_inspect_one", side_effect=[
                        {}, {}, {"State": {"Pid": 123}}, exited, exited,
                    ]),
                    mock.patch.object(controller, "attest_network"),
                    mock.patch.object(controller, "attest_container"),
                    mock.patch.object(
                        controller.ProcessIdentity, "capture", return_value=identity
                    ),
                    mock.patch.object(
                        controller, "wait_process_phase",
                        side_effect=["matched", "matched"],
                    ),
                    mock.patch.object(
                        controller.CgroupSampler,
                        "from_identity",
                        return_value=sampler,
                    ),
                    mock.patch.object(controller, "parse_json_object", return_value={}),
                    mock.patch.object(controller, "validate_measured", return_value={}),
                    mock.patch.object(
                        controller, "parse_fixture_output", return_value={}
                    ),
                    mock.patch.object(
                        controller,
                        "validate_fixture",
                        side_effect=controller.SmokeFailure(fixture_failure),
                    ),
                ):
                    with self.assertRaises(controller.SmokeFailure) as caught:
                        smoke.run_arm(
                            "go", "go", "fixture",
                            retain_failure_resources=True,
                        )
                self.assertEqual(caught.exception.failure_id, "infrastructure")
                self.assertIsNone(caught.exception.resources)
                self.assertFalse(caught.exception.resources_final)

    def test_cleanup_overrides_unexpected_runtime_failure(self):
        smoke = self.smoke(FakeDocker("ok"))
        identity = mock.Mock()
        identity.only_main_process.return_value = True
        sampler = mock.Mock()
        sampler.stop.return_value = {
            "memory_events": {
                "low": 0, "high": 0, "max": 0, "oom": 0,
                "oom_kill": 0, "oom_group_kill": 0,
            },
            "pids_events": {"max": 0},
        }
        with (
            mock.patch.object(smoke, "image_identity", side_effect=[
                "sha256:" + "a" * 64, "sha256:" + "b" * 64,
            ]),
            mock.patch.object(smoke, "_create", side_effect=["f" * 64, "m" * 64]),
            mock.patch.object(smoke, "_start"),
            mock.patch.object(smoke, "_signal"),
            mock.patch.object(
                smoke, "_cleanup", side_effect=controller.SmokeFailure("cleanup")
            ),
            mock.patch.object(smoke, "_inspect_one", side_effect=[
                {}, {}, {"State": {"Pid": 123}},
            ]),
            mock.patch.object(controller, "attest_network"),
            mock.patch.object(controller, "attest_container"),
            mock.patch.object(
                controller.ProcessIdentity, "capture", return_value=identity
            ),
            mock.patch.object(
                controller,
                "wait_process_phase",
                side_effect=["matched", RuntimeError("unexpected")],
            ),
            mock.patch.object(
                controller.CgroupSampler, "from_identity", return_value=sampler
            ),
        ):
            with self.assertRaises(controller.SmokeFailure) as caught:
                smoke.run_arm(
                    "go", "go", "fixture", retain_failure_resources=True
                )
        self.assertEqual(caught.exception.failure_id, "cleanup")
        self.assertIsNone(caught.exception.resources)
        self.assertFalse(caught.exception.resources_final)


class DockerBoundaryTests(unittest.TestCase):
    def executable(self, directory, body):
        path = Path(directory) / "fake-docker"
        path.write_text("#!/usr/bin/env python3\nimport sys\n" + body, encoding="utf-8")
        path.chmod(0o700)
        return str(path)

    def test_docker_json_errors_are_normalized(self):
        class BadJSONDocker(controller.Docker):
            def run(self, _args, timeout=15, check=True):
                return subprocess.CompletedProcess([], 0, b"not-json", b"")

        self.assertEqual(failure_id(lambda: BadJSONDocker().json(["inspect", "x"])), "command")

    def test_logs_require_empty_stderr_and_combined_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = self.executable(directory, "sys.stdout.write('{}'); sys.stderr.write('secret')\n")
            self.assertEqual(failure_id(lambda: controller.Docker(binary).logs("x")), "malformed_output")
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(controller, "MAX_REPORT_BYTES", 64):
            binary = self.executable(directory, "sys.stdout.write('x' * 40); sys.stderr.write('y' * 40)\n")
            self.assertEqual(failure_id(lambda: controller.Docker(binary).logs("x")), "malformed_output")

    def test_atomic_report_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            controller.write_report(path, {"schema_version": 1, "ok": False, "failure_id": "inspect"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text()), {"schema_version": 1, "ok": False, "failure_id": "inspect"})


class SamplerTests(unittest.TestCase):
    def write_cgroup(self, path, *, peak):
        values = {
            "memory.current": "1\n", "memory.peak": f"{peak}\n",
            "memory.max": f"{controller.GIB}\n", "memory.swap.max": "0\n",
            "memory.events": "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\noom_group_kill 0\n",
            "cpu.max": "100000 100000\n",
            "cpu.stat": (
                "usage_usec 3\nuser_usec 2\nsystem_usec 1\n"
                "core_sched.force_idle_usec 0\n"
            ),
            "pids.max": f"{controller.MEASURED_PIDS}\n", "pids.events": "max 0\n",
            "pids.current": "1\n", "pids.peak": "2\n", "cgroup.procs": "123\n",
        }
        for name, value in values.items():
            (path / name).write_text(value, encoding="ascii")

    def test_resource_sampling_continues_and_falls_back_after_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cgroup"
            path.mkdir()
            self.write_cgroup(path, peak=2)
            sampler = controller.CgroupSampler(path, interval=0.002)
            sampler.start()
            deadline = time.monotonic() + 1
            while sampler.latest is None and time.monotonic() < deadline:
                time.sleep(0.002)
            self.write_cgroup(path, peak=9)
            while (sampler.latest is None or sampler.latest["memory_peak"] != 9) and time.monotonic() < deadline:
                time.sleep(0.002)
            vanished = path.with_name("vanished")
            path.rename(vanished)
            result = sampler.stop()
            self.assertEqual(result["memory_peak"], 9)
            self.assertEqual(result["memory_limit"], controller.GIB)
            self.assertEqual(result["memory_swap_limit"], 0)
            self.assertEqual(result["pids_limit"], controller.MEASURED_PIDS)
            self.assertEqual(result["pids_current"], 1)
            self.assertEqual(result["pids_events"], {"max": 0})
            self.assertEqual(result["cgroup_process_count"], 1)
            self.assertEqual(result["cpu_max"], [100000, 100000])
            controller.validate_resources(result)
            for key, value, want in (
                ("memory_swap_limit", 1, "inspect"),
                ("cpu_max", [200000, 100000], "inspect"),
                ("cgroup_process_count", 2, "process_residue"),
                ("pids_current", controller.MEASURED_PIDS + 1, "inspect"),
                ("pids_peak", controller.MEASURED_PIDS + 1, "inspect"),
                ("pids_events", {"max": 1}, "pid_limit"),
            ):
                mutated = copy.deepcopy(result)
                mutated[key] = value
                self.assertEqual(
                    failure_id(lambda mutated=mutated: controller.validate_resources(mutated)),
                    want,
                )

            one_page_oom = copy.deepcopy(result)
            one_page_oom["memory_peak"] = (
                controller.GIB + one_page_oom["host_page_size"]
            )
            one_page_oom["memory_events"]["max"] = 1
            one_page_oom["memory_events"]["oom"] = 1
            one_page_oom["memory_events"]["oom_kill"] = 1
            self.assertEqual(
                failure_id(lambda: controller.validate_resources(one_page_oom)),
                "oom",
            )
            one_page_pressure = copy.deepcopy(result)
            one_page_pressure["memory_peak"] = (
                controller.GIB + one_page_pressure["host_page_size"]
            )
            one_page_pressure["memory_events"]["max"] = 1
            self.assertEqual(
                failure_id(
                    lambda: controller.validate_resources(one_page_pressure)
                ),
                "memory_pressure",
            )
            one_page_without_pressure = copy.deepcopy(result)
            one_page_without_pressure["memory_peak"] = (
                controller.GIB
                + one_page_without_pressure["host_page_size"]
            )
            self.assertEqual(
                failure_id(
                    lambda: controller.validate_resources(
                        one_page_without_pressure
                    )
                ),
                "inspect",
            )
            for key, value in (
                (
                    "memory_peak",
                    controller.GIB + one_page_oom["host_page_size"] + 1,
                ),
                ("cgroup_process_count", 2),
            ):
                invalid = copy.deepcopy(one_page_oom)
                invalid[key] = value
                with self.subTest(key=key):
                    self.assertEqual(
                        failure_id(
                            lambda invalid=invalid: controller.validate_resources(
                                invalid
                            )
                        ),
                        (
                            "process_residue"
                            if key == "cgroup_process_count"
                            else "inspect"
                        ),
                    )

    def test_final_sample_never_falls_back_to_earlier_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cgroup"
            path.mkdir()
            self.write_cgroup(path, peak=7)
            sampler = controller.CgroupSampler(path, interval=0.002)
            sampler.start()
            deadline = time.monotonic() + 1
            while sampler.latest is None and time.monotonic() < deadline:
                time.sleep(0.002)
            path.rename(path.with_name("vanished"))
            with self.assertRaises(controller.SmokeFailure) as caught:
                sampler.stop(reject_oom=False, allow_fallback=False)
            self.assertEqual(caught.exception.failure_id, "missing_cgroup")

    def test_missing_event_counters_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.write_cgroup(path, peak=2)
            (path / "pids.events").write_text("other 0\n", encoding="ascii")
            with self.assertRaises(ValueError):
                controller.CgroupSampler(path)._read_resources()
            self.write_cgroup(path, peak=2)
            (path / "memory.events").write_text(
                "low 0\nhigh 0\nmax 0\noom 0\noom_kill 0\n", encoding="ascii"
            )
            with self.assertRaises(ValueError):
                controller.CgroupSampler(path)._read_resources()

    def test_process_stat_is_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            proc_root = Path(directory)
            process_dir = proc_root / "123"
            process_dir.mkdir()
            fields = ["T", *("0" for _ in range(18)), "42"]
            (process_dir / "stat").write_text(
                "123 (density runner) " + " ".join(fields) + "\n", encoding="ascii"
            )
            self.assertEqual(
                controller._read_process_stat(123, proc_root=proc_root), ("T", 42)
            )
            (process_dir / "stat").write_text("malformed\n", encoding="ascii")
            self.assertEqual(
                failure_id(
                    lambda: controller._read_process_stat(123, proc_root=proc_root)
                ),
                "process_identity",
            )

    def test_only_main_process_rejects_residue_and_malformed_members(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            identity = controller.ProcessIdentity(123, 42, path)
            (path / "cgroup.procs").write_text("123\n", encoding="ascii")
            self.assertTrue(identity.only_main_process())
            (path / "cgroup.procs").write_text("123\n124\n", encoding="ascii")
            self.assertFalse(identity.only_main_process())
            (path / "cgroup.procs").write_text("secret\n", encoding="ascii")
            self.assertEqual(
                failure_id(identity.only_main_process), "process_residue"
            )

    def test_phase_markers_are_exact_and_prove_progress(self):
        identity = controller.ProcessIdentity(123, 42, Path("/cgroup"))
        valid = mock.Mock(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=10001,
            st_gid=10001,
            st_nlink=1,
            st_size=0,
        )
        with mock.patch.object(Path, "lstat", return_value=valid):
            self.assertTrue(identity.marker_ready(controller.INITIAL_MARKER_PATH))
            self.assertTrue(identity.marker_ready(controller.FINAL_MARKER_PATH))
        invalid = mock.Mock(
            st_mode=stat.S_IFREG | 0o600,
            st_uid=0,
            st_gid=10001,
            st_nlink=1,
            st_size=0,
        )
        with mock.patch.object(Path, "lstat", return_value=invalid):
            self.assertEqual(
                failure_id(
                    lambda: identity.marker_ready(controller.FINAL_MARKER_PATH)
                ),
                "process_state",
            )

        class FinalIdentity:
            def state(self):
                return "S"

            def marker_ready(self, marker_path):
                return marker_path == controller.FINAL_MARKER_PATH

        self.assertEqual(
            controller.wait_process_phase(
                FinalIdentity(), phase="final", deadline=time.monotonic() + 1
            ),
            "matched",
        )

    def test_flat_cgroup_parser_rejects_malformed_or_duplicate_keys(self):
        for raw in (
            "core_sched..force_idle_usec 0\n",
            ".core_sched 0\n",
            "usage_usec -1\n",
            "usage_usec 1\nusage_usec 2\n",
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                controller._parse_flat(raw, set())

    def test_cgroup_diagnostics_are_closed_canonical_ids(self):
        self.assertEqual(set(controller.CGROUP_FILE_FAILURES), set(controller.CGROUP_FILES))
        self.assertEqual(len(set(controller.CGROUP_FILE_FAILURES.values())), len(controller.CGROUP_FILES))
        for name, failure in controller.CGROUP_FILE_FAILURES.items():
            self.assertEqual(failure, "missing_cgroup_" + name.replace(".", "_"))
            self.assertIn(failure, controller.FAILURE_IDS)
            self.assertRegex(failure, r"^[a-z_]+$")


if __name__ == "__main__":
    unittest.main()
