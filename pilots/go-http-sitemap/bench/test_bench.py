from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import orchestrator
import python_runner
from common import job_manifest_sha256, load_corpus, scenario_map, validate_fixture_origin
from fixture import FixtureFleet, FixtureState, raw_http_get
from orchestrator import (
    EVIDENCE_ENTRYPOINT,
    EVIDENCE_GO_BINARY,
    EVIDENCE_PYTHON,
    EVIDENCE_PYTHON_ATTESTATION,
    EVIDENCE_SOURCE_ROOT,
    ProcessSampler,
    RunnerProcess,
    _git_cleanliness,
    _safe_child_environment,
    _validate_docker_inspect,
    _validate_loopback_only_network,
    _validate_python_ready_environment,
    arm_metrics,
    go_source_identity,
    make_jobs,
    stage_python_sources,
    summarize,
    validate_batch,
    validate_go_binary_identity,
)

BENCH_ROOT = Path(__file__).resolve().parent


class CorpusTests(unittest.TestCase):
    def test_hashed_corpus_and_required_ladder(self) -> None:
        corpus = load_corpus(BENCH_ROOT)
        self.assertEqual(
            corpus["source"]["python_source_commit"],
            "fb6117b06a3008cdac76adc258327b2f220085f8",
        )
        self.assertEqual(corpus["source"]["python_runtime"], "3.13.15")
        self.assertEqual(corpus["defaults"]["origin_count"], 32)
        self.assertEqual(corpus["defaults"]["global_active_requests"], 20)
        self.assertEqual(corpus["defaults"]["global_total_connections"], 20)
        self.assertEqual(corpus["defaults"]["per_origin_concurrency"], 2)
        self.assertEqual(corpus["defaults"]["idle_expiry_seconds"], 5)
        self.assertGreaterEqual(corpus["defaults"]["repetitions"], 5)
        self.assertGreaterEqual(corpus["defaults"]["warmup_jobs"], 128)
        self.assertGreaterEqual(corpus["defaults"]["capacity_jobs"], 512)
        self.assertGreaterEqual(corpus["defaults"]["overload_jobs"], 1024)
        self.assertEqual(
            hashlib.sha256((BENCH_ROOT / "uv.lock").read_bytes()).hexdigest(),
            corpus["source"]["python_lock_sha256"],
        )
        self.assertEqual(corpus["source"]["go_toolchain"], "go1.24.0")
        self.assertEqual(
            [entry["workers"] for entry in corpus["ladder"] if entry["kind"] == "capacity"],
            [1, 5, 20, 50],
        )
        self.assertTrue(any(entry["kind"] == "overload" for entry in corpus["ladder"]))

    def test_evidence_assets_pin_runtime_and_exec_runner(self) -> None:
        corpus = load_corpus(BENCH_ROOT)
        dockerfile = (BENCH_ROOT / "evidence" / "Dockerfile").read_text()
        wrapper = (BENCH_ROOT / "evidence" / "enter-runner-cgroup.sh").read_text()
        dockerignore = (BENCH_ROOT / "evidence" / "Dockerfile.dockerignore").read_text()
        orchestrator_source = (BENCH_ROOT / "orchestrator.py").read_text()
        self.assertIn("FROM " + corpus["source"]["python_image_identity"], dockerfile)
        self.assertIn("go1.24.0.linux-amd64.tar.gz", dockerfile)
        self.assertIn(
            "dea9ca38a0b852a74e81c26134671af7c0fbe65d81b0dc1c5bfe22cf7d4c8858", dockerfile
        )
        self.assertIn("uv sync --project /opt/jobseek-bench-env --frozen --no-dev", dockerfile)
        self.assertIn("ENTRYPOINT", dockerfile)
        self.assertEqual(dockerignore.splitlines()[0], "**")
        self.assertNotIn("!apps/crawler/.env", dockerignore)
        self.assertNotIn("!apps/crawler/data", dockerignore)
        self.assertNotIn("--runner-prefix-json", orchestrator_source)
        self.assertIn("EVIDENCE_WRAPPER", orchestrator_source)
        self.assertTrue(wrapper.rstrip().endswith('exec "$@"'))

    def test_expected_url_digests_are_frozen(self) -> None:
        corpus = load_corpus(BENCH_ROOT)
        for scenario in corpus["scenarios"]:
            urls = [
                f"https://fixture.invalid/jobs/{scenario['id']}/{index:05d}?slot={index % 17}"
                for index in range(scenario["url_count"])
            ]
            actual = hashlib.sha256("\n".join(sorted(urls)).encode()).hexdigest()
            self.assertEqual(actual, scenario["expected_url_digest_sha256"])

    def test_ordered_manifest_digest_changes_on_reorder(self) -> None:
        corpus = load_corpus(BENCH_ROOT)
        origins = [f"http://127.0.0.1:{30_000 + index}" for index in range(32)]
        jobs = make_jobs(corpus, origins, batch_id="digest-test", suite="capacity", count=8)
        self.assertEqual(job_manifest_sha256(jobs), job_manifest_sha256(list(jobs)))
        self.assertNotEqual(job_manifest_sha256(jobs), job_manifest_sha256(list(reversed(jobs))))


class SafetyTests(unittest.TestCase):
    def assert_process_gone(self, pid: int) -> None:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        self.fail(f"process {pid} survived harness cleanup")

    def test_fixture_origins_reject_public_and_credentials(self) -> None:
        with self.assertRaises(ValueError):
            validate_fixture_origin("http://8.8.8.8:80")
        with self.assertRaises(ValueError):
            validate_fixture_origin("http://user:secret@127.0.0.1:8000")
        self.assertEqual(validate_fixture_origin("http://127.0.0.1:8000"), "http://127.0.0.1:8000")

    def test_child_environment_is_an_allowlist(self) -> None:
        self.assertEqual(
            set(_safe_child_environment()),
            {"PATH", "LANG", "LC_ALL", "GOMAXPROCS", "PYTHONHASHSEED", "OMP_NUM_THREADS"},
        )
        forbidden = {"AWS_ACCESS_KEY_ID", "DATABASE_URL", "GITHUB_TOKEN", "HOME"}
        self.assertFalse(forbidden & set(_safe_child_environment()))

    def test_warmup_attempt_state_cannot_leak_into_measured_batch(self) -> None:
        corpus = load_corpus(BENCH_ROOT)
        state = FixtureState(corpus)
        scenario = scenario_map(corpus)["retry-recovered"]
        origin = "http://127.0.0.1:31000"
        state.configure_origins([origin])
        for batch_id in ("warmup-state", "measured-state"):
            job = {
                "id": "job-0000",
                "origin": origin,
                "sitemap_url": f"{origin}/fixture/{batch_id}/job-0000/sitemap/root.xml",
                "scenario": scenario["id"],
                "expected_outcome": scenario["expected_outcome"],
            }
            state.register_batch(arm_token="state-arm", batch_id=batch_id, jobs=[job])
            response = state.response_for(
                f"/fixture/{batch_id}/job-0000/sitemap/root.xml", "127.0.0.1:31000", 0
            )
            self.assertIsNotNone(response)
            assert response is not None
            self.assertEqual(response.attempt, 1)
            self.assertEqual(response.status, 500)

    def test_frozen_source_bundle_matches_hashed_corpus(self) -> None:
        corpus = load_corpus(BENCH_ROOT)
        crawler_root = BENCH_ROOT.parent.parent.parent / "apps" / "crawler"
        with tempfile.TemporaryDirectory() as directory:
            identity = stage_python_sources(
                crawler_root,
                Path(directory) / "source",
                True,
                corpus["source"],
            )
        self.assertEqual(identity["staged_commit"], corpus["source"]["python_source_commit"])
        self.assertEqual(
            {item["relative_path"]: item["sha256"] for item in identity["files"]},
            {item["relative_path"]: item["sha256"] for item in corpus["source"]["python_files"]},
        )

    def test_python_protocol_accepts_the_largest_evidence_manifest_frame(self) -> None:
        corpus = load_corpus(BENCH_ROOT)
        origins = [f"http://127.0.0.1:{31_000 + index}" for index in range(32)]
        jobs = make_jobs(
            corpus,
            origins,
            batch_id="overload-c20-r00-measured",
            suite="capacity",
            count=int(corpus["defaults"]["overload_jobs"]),
        )
        command = {
            "action": "batch",
            "batch_id": "overload-c20-r00-measured",
            "phase": "measured",
            "manifest_sha256": job_manifest_sha256(jobs),
            "jobs": jobs,
        }
        frame = json.dumps(command, separators=(",", ":")).encode() + b"\n"
        self.assertGreater(len(frame), 64 * 1024)
        self.assertLess(len(frame), python_runner.PROTOCOL_FRAME_LIMIT_BYTES)

        async def read_frame() -> bytes:
            reader = python_runner._protocol_stream_reader()
            reader.feed_data(frame)
            reader.feed_eof()
            return await reader.readline()

        self.assertEqual(asyncio.run(read_frame()), frame)

    def test_pinned_httpcore_pool_reports_configured_limits_before_measurement(self) -> None:
        async def inspect_limits() -> tuple[int, int]:
            limits = python_runner.httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
                keepalive_expiry=5.0,
            )
            async with python_runner.httpx.AsyncClient(limits=limits) as client:
                return python_runner._httpcore_pool_limits(client)

        self.assertEqual(asyncio.run(inspect_limits()), (20, 10))

    def test_runner_startup_timeout_reaps_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file = root / "pid"
            script = (
                "import os,time,pathlib; "
                f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
                "time.sleep(60)"
            )
            command = [sys.executable, "-c", script]
            with self.assertRaises(orchestrator.RunnerStartupError) as caught:
                RunnerProcess(
                    command,
                    cwd=root,
                    ready_timeout=0.2,
                )
            pid = int(pid_file.read_text())
            failure_path, _ = orchestrator._persist_arm_failure(
                out=root,
                arm_token="startup-timeout",
                command=command,
                runner=None,
                error=caught.exception,
            )
            diagnostic = json.loads(failure_path.read_text())
            self.assertEqual(diagnostic["failure_kind"], "timeout")
            self.assertEqual(diagnostic["cause_type"], "TimeoutError")
            self.assertIsNone(diagnostic["runner_observed_returncode"])
            self.assertIn("process remained alive", diagnostic["cause"])
            self.assert_process_gone(pid)

    def test_runner_startup_exit_is_distinct_from_timeout_and_retained(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_file = root / "pid"
            marker = "STARTUP-EARLY-EXIT"
            script = (
                "import os,pathlib,sys; "
                f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
                f"sys.stderr.write({(marker + chr(10))!r}); sys.stderr.flush(); "
                "raise SystemExit(7)"
            )
            command = [sys.executable, "-c", script]
            with self.assertRaises(orchestrator.RunnerStartupError) as caught:
                RunnerProcess(command, cwd=root, ready_timeout=2.0)
            pid = int(pid_file.read_text())
            failure_path, _ = orchestrator._persist_arm_failure(
                out=root,
                arm_token="startup-exit",
                command=command,
                runner=None,
                error=caught.exception,
            )
            diagnostic = json.loads(failure_path.read_text())
            self.assertEqual(diagnostic["failure_kind"], "early_exit")
            self.assertEqual(diagnostic["cause_type"], "RuntimeError")
            self.assertEqual(diagnostic["runner_observed_returncode"], 7)
            self.assertEqual(diagnostic["runner_returncode"], 7)
            self.assertIn("exit=7", diagnostic["cause"])
            self.assertIn(marker, diagnostic["stderr"])
            self.assert_process_gone(pid)

    def test_runner_thread_start_failure_reaps_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = "THREAD-START-STDERR-MARKER"
            script = (
                "import sys,time; "
                f"sys.stderr.write({(marker + chr(10))!r}); sys.stderr.flush(); "
                "time.sleep(60)"
            )
            command = [sys.executable, "-c", script]
            original_start = threading.Thread.start
            starts = 0

            def fail_second_start(thread: threading.Thread) -> None:
                nonlocal starts
                starts += 1
                if starts == 2:
                    time.sleep(0.1)
                    raise RuntimeError("synthetic thread start failure")
                original_start(thread)

            with (
                mock.patch.object(
                    threading.Thread,
                    "start",
                    autospec=True,
                    side_effect=fail_second_start,
                ),
                self.assertRaises(orchestrator.RunnerStartupError) as caught,
            ):
                RunnerProcess(command, cwd=root)
            self.assertEqual(starts, 2)
            self.assertEqual(caught.exception.failure_kind, "protocol")
            self.assertEqual(caught.exception.cause_type, "RuntimeError")
            self.assertIn("synthetic thread start failure", caught.exception.cause)
            self.assertIn(marker, caught.exception.stderr)
            self.assert_process_gone(caught.exception.pid)

    def test_runner_thread_construction_failure_reaps_child(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = "THREAD-CONSTRUCTION-STDERR-MARKER"
            script = (
                "import sys,time; "
                f"sys.stderr.write({(marker + chr(10))!r}); sys.stderr.flush(); "
                "time.sleep(60)"
            )
            command = [sys.executable, "-c", script]
            original_thread = threading.Thread
            constructions = 0

            def fail_second_construction(*args: object, **kwargs: object) -> threading.Thread:
                nonlocal constructions
                constructions += 1
                if constructions == 2:
                    time.sleep(0.1)
                    raise RuntimeError("synthetic thread construction failure")
                return original_thread(*args, **kwargs)

            with (
                mock.patch.object(
                    orchestrator.threading,
                    "Thread",
                    side_effect=fail_second_construction,
                ),
                self.assertRaises(orchestrator.RunnerStartupError) as caught,
            ):
                RunnerProcess(command, cwd=root)
            self.assertEqual(constructions, 2)
            self.assertEqual(caught.exception.failure_kind, "protocol")
            self.assertEqual(caught.exception.cause_type, "RuntimeError")
            self.assertIn("synthetic thread construction failure", caught.exception.cause)
            self.assertIn(marker, caught.exception.stderr)
            self.assert_process_gone(caught.exception.pid)

    def test_unexpected_or_non_mapping_ready_reaps_runner(self) -> None:
        for ready_json in ({"type": "unexpected"}, ["ready"]):
            with self.subTest(ready_json=ready_json), tempfile.TemporaryDirectory() as directory:
                pid_file = Path(directory) / "pid"
                script = (
                    "import json,os,time,pathlib; "
                    f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
                    f"print(json.dumps({ready_json!r}),flush=True); time.sleep(60)"
                )
                with self.assertRaises(RuntimeError):
                    RunnerProcess([sys.executable, "-c", script], cwd=Path(directory))
                self.assert_process_gone(int(pid_file.read_text()))

    def test_invalid_ready_construction_retains_record_stderr_and_reaps_runner(self) -> None:
        for index, ready_record in enumerate(({"type": "unexpected", "detail": 7}, ["ready"])):
            with (
                self.subTest(ready_record=ready_record),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                pid_file = root / "pid"
                marker = f"INVALID-READY-DIAGNOSTIC-{index}"
                script = (
                    "import json,os,pathlib,sys,time; "
                    f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid())); "
                    f"sys.stderr.write({(marker + chr(10))!r}); sys.stderr.flush(); "
                    f"print(json.dumps({ready_record!r}),flush=True); time.sleep(60)"
                )
                command = [sys.executable, "-c", script]
                with self.assertRaises(orchestrator.RunnerStartupError) as caught:
                    RunnerProcess(command, cwd=root)
                pid = int(pid_file.read_text())
                failure_path, tail = orchestrator._persist_arm_failure(
                    out=root,
                    arm_token=f"invalid-ready-{index}",
                    command=command,
                    runner=None,
                    error=caught.exception,
                )
                diagnostic = json.loads(failure_path.read_text())
                self.assertEqual(diagnostic["ready"], ready_record)
                self.assertEqual(diagnostic["command"], command)
                self.assertEqual(diagnostic["runner_pid"], pid)
                self.assertIn(marker, diagnostic["stderr"])
                self.assertIn(marker, tail)
                self.assert_process_gone(pid)

    def test_startup_failure_kills_non_exec_prefix_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "pids.json"
            child_script = "import time; time.sleep(60)"
            wrapper_script = (
                "import json,os,pathlib,subprocess,sys,time; "
                f"child=subprocess.Popen([sys.executable,'-c',{child_script!r}]); "
                f"pathlib.Path({str(pid_file)!r}).write_text(json.dumps([os.getpid(),child.pid])); "
                "time.sleep(60)"
            )
            with self.assertRaises(RuntimeError):
                RunnerProcess(
                    [sys.executable, "-c", wrapper_script],
                    cwd=Path(directory),
                    ready_timeout=0.3,
                )
            for pid in json.loads(pid_file.read_text()):
                self.assert_process_gone(pid)

    def test_sampler_stops_when_batch_pipe_write_fails(self) -> None:
        script = "import json,os; print(json.dumps({'type':'ready','pid':os.getpid()}),flush=True)"
        with tempfile.TemporaryDirectory() as directory:
            runner = RunnerProcess([sys.executable, "-c", script], cwd=Path(directory))
            runner.process.wait(timeout=2.0)
            with self.assertRaises((BrokenPipeError, OSError, TimeoutError)):
                runner.batch({"action": "batch"}, timeout=0.2)
            self.assertFalse(
                any(
                    thread.name.startswith("benchmark-sampler-") and thread.is_alive()
                    for thread in threading.enumerate()
                )
            )
            runner.terminate()

    def test_batch_exit_retains_diagnostic_and_exposes_bounded_stderr_tail(self) -> None:
        marker = "REAL-RUNNER-EXIT-DIAGNOSTIC"
        script = (
            "import json,os,sys; "
            "print(json.dumps({'type':'ready','pid':os.getpid()}),flush=True); "
            "sys.stdin.readline(); "
            f"sys.stderr.write('x'*6000+'{marker}\\n'); sys.stderr.flush(); "
            "raise SystemExit(2)"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = [sys.executable, "-c", script]
            runner = RunnerProcess(command, cwd=root)
            pid = runner.process.pid
            with self.assertRaisesRegex(RuntimeError, "exited before emitting") as caught:
                runner.batch({"action": "batch"}, timeout=2.0)
            self.assertNotIsInstance(caught.exception, TimeoutError)
            self.assertIn("exit=2", str(caught.exception))
            self.assertIn(marker, str(caught.exception))
            runner.terminate()
            failure_path, tail = orchestrator._persist_arm_failure(
                out=root,
                arm_token="diagnostic-arm",
                command=command,
                runner=runner,
                error=caught.exception,
            )
            self.assertEqual(failure_path, root / "raw/failures/diagnostic-arm.json")
            diagnostic = json.loads(failure_path.read_text())
            self.assertEqual(diagnostic["arm_token"], "diagnostic-arm")
            self.assertEqual(diagnostic["command"], command)
            self.assertEqual(diagnostic["ready"], {"type": "ready", "pid": pid})
            self.assertEqual(diagnostic["runner_returncode"], 2)
            self.assertIn(marker, diagnostic["stderr"])
            self.assertEqual(diagnostic["stderr_tail_exposed"], tail)
            self.assertLessEqual(len(tail), orchestrator.STDERR_TAIL_CHARACTERS + 11)
            self.assert_process_gone(pid)

    def test_sampler_thread_failure_is_propagated_and_stopped(self) -> None:
        table = {os.getpid(): (os.getppid(), 1)}
        sampler = ProcessSampler(os.getpid())
        with mock.patch.object(
            orchestrator,
            "_process_table",
            side_effect=[table, RuntimeError("sample failed")],
        ):
            sampler.start()
            deadline = time.monotonic() + 1.0
            while sampler.error is None and time.monotonic() < deadline:
                time.sleep(0.01)
            with self.assertRaisesRegex(RuntimeError, "sampler thread failed"):
                sampler.stop()
        self.assertFalse(sampler.thread.is_alive())

    def test_arbitrary_go_binary_is_rejected(self) -> None:
        corpus = load_corpus(BENCH_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "arbitrary.go"
            binary = root / "arbitrary"
            source.write_text("package main\nfunc main() {}\n")
            subprocess.check_call(["go", "build", "-trimpath", "-o", binary, source])
            with self.assertRaisesRegex(RuntimeError, "Go binary identity mismatch"):
                go_source_identity(binary, corpus["source"], evidence=False)

    def test_stale_go_binary_build_info_is_rejected(self) -> None:
        source = load_corpus(BENCH_ROOT)["source"]
        identity = {
            "repository_head": "1" * 40,
            "repository_clean": True,
            "build_info": {
                "go_version": source["go_toolchain"],
                "path": source["go_runner_path"],
                "module": source["go_module"],
                "settings": {
                    "-trimpath": "true",
                    "vcs.revision": "2" * 40,
                    "vcs.modified": "false",
                    "GOOS": "linux",
                    "GOARCH": "amd64",
                },
            },
        }
        with self.assertRaisesRegex(RuntimeError, "VCS revision"):
            validate_go_binary_identity(identity, source, evidence=True)

    def test_python_environment_attestation_is_exact(self) -> None:
        source = load_corpus(BENCH_ROOT)["source"]
        ready = {
            "runtime": source["python_runtime"],
            "lock_sha256": source["python_lock_sha256"],
            "runtime_package_versions": source["python_runtime_packages"],
            "absent_runtime_packages": source["python_absent_runtime_packages"],
            "installed_distributions": [
                {"name": name, "version": version}
                for name, version in source["python_runtime_packages"].items()
            ],
            "executable_sha256": "0" * 64,
            "executable_path": str(EVIDENCE_PYTHON),
            "os_release": source["python_os_release"],
        }
        build_attestation = {
            "sys_executable": str(EVIDENCE_PYTHON),
            "executable_sha256": ready["executable_sha256"],
            "runtime": ready["runtime"],
            "lock_sha256": ready["lock_sha256"],
            "runtime_package_versions": ready["runtime_package_versions"],
            "absent_runtime_packages": ready["absent_runtime_packages"],
            "installed_distributions": ready["installed_distributions"],
            "os_release": ready["os_release"],
        }
        self.assertRegex(
            _validate_python_ready_environment(
                ready, source, evidence=True, build_attestation=build_attestation
            ),
            r"^[0-9a-f]{64}$",
        )
        ready["runtime_package_versions"] = {
            **source["python_runtime_packages"],
            "httpx": "0.0.0",
        }
        with self.assertRaisesRegex(RuntimeError, "package versions"):
            _validate_python_ready_environment(
                ready, source, evidence=True, build_attestation=build_attestation
            )

    def test_cleanliness_includes_ignored_and_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.check_call(["git", "init", "-q"], cwd=root)
            (root / ".gitignore").write_text("ignored\n")
            subprocess.check_call(["git", "add", ".gitignore"], cwd=root)
            subprocess.check_call(
                [
                    "git",
                    "-c",
                    "user.name=Benchmark Test",
                    "-c",
                    "user.email=benchmark@example.invalid",
                    "commit",
                    "-qm",
                    "fixture",
                ],
                cwd=root,
            )
            (root / "ignored").write_text("overlay")
            (root / "untracked").write_text("overlay")
            cleanliness = _git_cleanliness(root)
            self.assertTrue(cleanliness["status"])
            self.assertEqual(cleanliness["ignored"], ["ignored"])

    def test_evidence_envelope_rejects_inexact_or_cotenant_cgroup(self) -> None:
        valid = {
            "path": "/jobseek-7948",
            "cpu.max": "100000 100000",
            "memory.max": "1073741824",
            "memory.swap.max": "0",
            "pids.max": "128",
            "memory.events": "oom_kill 0",
            "cgroup.procs": [42],
        }

        def run(snapshot: dict[str, object]) -> None:
            def fake_snapshot(pid: int) -> dict[str, object]:
                return snapshot if pid == 42 else {"path": "/orchestrator"}

            with (
                mock.patch.object(orchestrator.platform, "system", return_value="Linux"),
                mock.patch.object(orchestrator.platform, "machine", return_value="x86_64"),
                mock.patch.object(orchestrator, "_cgroup_snapshot", side_effect=fake_snapshot),
            ):
                orchestrator._validate_evidence_envelope(
                    runner_pid=42,
                    process_pid=42,
                    ready={"file_descriptor_soft_limit": 256},
                    fd_limit=256,
                )

        run(valid)
        for change in (
            {"cpu.max": "50000 100000"},
            {"memory.max": "536870912", "memory.swap.max": "0"},
            {"pids.max": "127"},
            {"cgroup.procs": [42, 43]},
        ):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                run({**valid, **change})
        for key in (
            "cpu.max",
            "memory.max",
            "memory.swap.max",
            "pids.max",
            "memory.events",
            "cgroup.procs",
        ):
            with self.subTest(missing=key), self.assertRaisesRegex(RuntimeError, "missing"):
                missing = dict(valid)
                del missing[key]
                run(missing)

    def test_missing_memory_event_counter_is_not_treated_as_zero(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "counter is unavailable"):
            orchestrator._memory_event({"memory.events": "oom 0\nlow 0"}, "oom_kill")
        with self.assertRaisesRegex(RuntimeError, "snapshot is unavailable"):
            orchestrator._memory_event(None, "oom_kill")

    def test_network_attestation_rejects_interfaces_and_routes(self) -> None:
        base = {
            "supported": True,
            "interfaces": ["lo"],
            "ipv4_routes": [],
            "ipv6_routes": [],
        }
        _validate_loopback_only_network(base)
        with self.assertRaisesRegex(RuntimeError, "interface"):
            _validate_loopback_only_network({**base, "interfaces": ["eth0", "lo"]})
        with self.assertRaisesRegex(RuntimeError, "IPv4 route"):
            _validate_loopback_only_network(
                {
                    **base,
                    "ipv4_routes": [
                        {
                            "interface": "lo",
                            "destination": "0.0.0.0",
                            "mask": "0.0.0.0",
                        }
                    ],
                }
            )
        reject_sentinel = {
            "interface": "lo",
            "destination": "::",
            "prefix_length": 0,
            "metric": 0xFFFFFFFF,
            "flags": 0x00200200,
        }
        _validate_loopback_only_network({**base, "ipv6_routes": [reject_sentinel]})
        with self.assertRaisesRegex(RuntimeError, "IPv6 route"):
            _validate_loopback_only_network(
                {**base, "ipv6_routes": [{**reject_sentinel, "flags": 0x00200000}]}
            )
        with self.assertRaisesRegex(RuntimeError, "IPv6 route"):
            _validate_loopback_only_network(
                {**base, "ipv6_routes": [{**reject_sentinel, "interface": "eth0"}]}
            )

    def test_docker_inspection_binds_image_and_network_none(self) -> None:
        source = load_corpus(BENCH_ROOT)["source"]
        derived = "sha256:" + "1" * 64
        evidence_commit = "6" * 40
        image = {
            "Id": derived,
            "Config": {
                "Entrypoint": EVIDENCE_ENTRYPOINT,
                "Labels": {
                    "org.opencontainers.image.base.name": source["python_image_identity"],
                    "org.jobseek.evidence.python-attestation": str(EVIDENCE_PYTHON_ATTESTATION),
                    "org.jobseek.evidence.go-binary": str(EVIDENCE_GO_BINARY),
                    "org.jobseek.evidence.source-root": str(EVIDENCE_SOURCE_ROOT),
                    "org.jobseek.evidence.commit": evidence_commit,
                },
            },
            "RootFS": {"Layers": ["sha256:" + "2" * 64, "sha256:" + "4" * 64]},
        }
        frozen_digest = source["python_image_identity"].rsplit("@", 1)[1]
        base = {
            "Id": "sha256:" + "5" * 64,
            "RepoDigests": [f"python@{frozen_digest}"],
            "RootFS": {"Layers": ["sha256:" + "2" * 64]},
        }
        container_id = "3" * 64
        args = ["--evidence", "--out", "/evidence/attempt"]
        container = {
            "Id": container_id,
            "Image": derived,
            "Config": {
                "Entrypoint": EVIDENCE_ENTRYPOINT,
                "Cmd": args,
                "Hostname": container_id[:12],
            },
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "Privileged": True,
                "CgroupnsMode": "host",
            },
            "Mounts": [
                {"Destination": "/source-proof", "RW": False},
                {"Destination": "/evidence", "RW": True},
                {"Destination": "/sys/fs/cgroup", "RW": True},
            ],
        }
        live_mounts = {
            "/": {"mount_options": ["ro"]},
            "/source-proof": {"mount_options": ["ro"]},
            "/evidence": {"mount_options": ["rw"]},
            "/sys/fs/cgroup": {"mount_options": ["rw"]},
        }
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.json"
            image_path = Path(directory) / "image.json"
            container_path = Path(directory) / "container.json"
            base_path.write_text(json.dumps([base]))
            image_path.write_text(json.dumps([image]))
            container_path.write_text(json.dumps([container]))
            attestation = _validate_docker_inspect(
                base_path=base_path,
                image_path=image_path,
                container_path=container_path,
                derived_image_id=derived,
                frozen_base=source["python_image_identity"],
                live_hostname=container_id[:12],
                live_cgroup=f"0::/docker/{container_id}",
                live_pid=1,
                executing_arguments=args,
                live_mounts=live_mounts,
                evidence_commit=evidence_commit,
            )
            self.assertEqual(attestation["container_network_mode"], "none")
            container["HostConfig"]["NetworkMode"] = "bridge"
            container_path.write_text(json.dumps([container]))
            with self.assertRaisesRegex(RuntimeError, "NetworkMode"):
                _validate_docker_inspect(
                    base_path=base_path,
                    image_path=image_path,
                    container_path=container_path,
                    derived_image_id=derived,
                    frozen_base=source["python_image_identity"],
                    live_hostname=container_id[:12],
                    live_cgroup=f"0::/docker/{container_id}",
                    live_pid=1,
                    executing_arguments=args,
                    live_mounts=live_mounts,
                    evidence_commit=evidence_commit,
                )
            container["HostConfig"]["NetworkMode"] = "none"
            container_path.write_text(json.dumps([container]))
            with self.assertRaisesRegex(RuntimeError, "live cgroup"):
                _validate_docker_inspect(
                    base_path=base_path,
                    image_path=image_path,
                    container_path=container_path,
                    derived_image_id=derived,
                    frozen_base=source["python_image_identity"],
                    live_hostname=container_id[:12],
                    live_cgroup="0::/docker/a-different-container",
                    live_pid=1,
                    executing_arguments=args,
                    live_mounts=live_mounts,
                    evidence_commit=evidence_commit,
                )
            image["RootFS"]["Layers"] = ["sha256:" + "9" * 64]
            image_path.write_text(json.dumps([image]))
            with self.assertRaisesRegex(RuntimeError, "base layer prefix"):
                _validate_docker_inspect(
                    base_path=base_path,
                    image_path=image_path,
                    container_path=container_path,
                    derived_image_id=derived,
                    frozen_base=source["python_image_identity"],
                    live_hostname=container_id[:12],
                    live_cgroup=f"0::/docker/{container_id}",
                    live_pid=1,
                    executing_arguments=args,
                    live_mounts=live_mounts,
                    evidence_commit=evidence_commit,
                )


class MetricTests(unittest.TestCase):
    @staticmethod
    def _arm(level: str, implementation: str, rate: float) -> dict[str, object]:
        return {
            "level": level,
            "suite": "capacity",
            "implementation": implementation,
            "metrics": {
                "successful_jobs_per_second": rate,
                "cpu_seconds_per_successful_job": 0.01,
                "steady_rss_kib": 1 if implementation == "go" else 2,
                "peak_process_tree_rss_kib": 2,
                "end_to_end": {"p99_ms": 1 if implementation == "go" else 2},
                "error_jobs": 0,
            },
        }

    def test_smoke_never_passes_decision_gate_and_only_c5_is_eligible(self) -> None:
        arms: list[dict[str, object]] = []
        pairs: list[dict[str, object]] = []
        for level in ("c1", "c5"):
            for repetition in range(5):
                go = self._arm(level, "go", 2.0)
                python = self._arm(level, "python", 1.0)
                arms.extend((go, python))
                pairs.append(
                    {
                        "level": level,
                        "suite": "capacity",
                        "parity": True,
                        "go": go,
                        "python": python,
                        "repetition": repetition,
                    }
                )
        smoke = summarize(arms, pairs, mode="smoke")
        self.assertFalse(smoke["production_anchor_c5_decision_gate"])
        self.assertFalse(any(row["decision_gate_pass"] for row in smoke["levels"].values()))
        evidence = summarize(arms, pairs, mode="evidence")
        self.assertTrue(evidence["levels"]["c5"]["decision_gate_pass"])
        self.assertFalse(evidence["levels"]["c1"]["decision_gate_pass"])

    def test_one_paired_resource_regression_fails_even_when_aggregate_passes(self) -> None:
        arms: list[dict[str, object]] = []
        pairs: list[dict[str, object]] = []
        for repetition in range(5):
            go = self._arm("c5", "go", 2.0)
            python = self._arm("c5", "python", 1.0)
            if repetition == 0:
                go["metrics"]["steady_rss_kib"] = 3
                go["metrics"]["end_to_end"]["p99_ms"] = 3
            arms.extend((go, python))
            pairs.append(
                {
                    "level": "c5",
                    "suite": "capacity",
                    "parity": True,
                    "go": go,
                    "python": python,
                    "repetition": repetition,
                }
            )
        result = summarize(arms, pairs, mode="evidence")
        self.assertLessEqual(
            result["levels"]["c5"]["implementations"]["go"]["steady_rss_kib_median"],
            result["levels"]["c5"]["implementations"]["python"]["steady_rss_kib_median"],
        )
        self.assertFalse(result["levels"]["c5"]["paired_resources_pass"])
        self.assertFalse(result["production_anchor_c5_decision_gate"])

    def test_index_children_are_not_counted_as_retries(self) -> None:
        arm = {
            "result": {
                "wall_ns": 1_000_000_000,
                "user_cpu_ns": 1,
                "sys_cpu_ns": 1,
                "peak_rss_kib": 1,
                "panics": 0,
                "unfinished": 0,
                "max_queued": 1,
                "max_in_flight": 1,
            },
            "enriched_jobs": [
                {
                    "outcome": "success",
                    "queue_ns": 1,
                    "service_ns": 1,
                    "end_to_end_ns": 2,
                    "requests": 5,
                    "fixture_response_body_bytes": 1,
                    "root_attempts": 1,
                    "root_retry_intervals": 0,
                }
            ],
            "samples": {"steady_rss_kib": 1, "peak_sampled_rss_kib": 1, "max_open_fds": 1},
            "connections": {},
        }
        metrics = arm_metrics(arm)
        self.assertEqual(metrics["root_attempts"], 1)
        self.assertEqual(metrics["root_retry_intervals"], 0)
        self.assertNotIn("retries", metrics)


class BatchValidationTests(unittest.TestCase):
    def _case(
        self, *, count: int, same_origin: bool, workers: int, capacity: int
    ) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]], dict[str, int]]:
        corpus = load_corpus(BENCH_ROOT)
        scenario = scenario_map(corpus)["urlset-512"]
        jobs: list[dict[str, object]] = []
        outputs: list[dict[str, object]] = []
        transcript: list[dict[str, object]] = []
        for index in range(count):
            origin_index = 0 if same_origin else index
            origin = f"http://127.0.0.1:{31_000 + origin_index}"
            job_id = f"job-{index:04d}"
            jobs.append(
                {
                    "id": job_id,
                    "origin": origin,
                    "sitemap_url": f"{origin}/fixture/test/{job_id}/sitemap/root.xml",
                    "scenario": scenario["id"],
                    "expected_outcome": "success",
                }
            )
            accepted = index * 2 + 1
            started = accepted + 1
            finished = 20 + index
            outputs.append(
                {
                    "id": job_id,
                    "origin": origin,
                    "scenario": scenario["id"],
                    "outcome": "success",
                    "url_count": scenario["url_count"],
                    "url_digest_sha256": scenario["expected_url_digest_sha256"],
                    "requests": 1,
                    "wire_attempts": 1,
                    "filtered_count": 0,
                    "truncated": False,
                    "decoded_bytes": 7,
                    "status_body_bytes": 0,
                    "accepted_ns": accepted,
                    "started_ns": started,
                    "finished_ns": finished,
                    "queue_ns": started - accepted,
                    "service_ns": finished - started,
                    "end_to_end_ns": finished - accepted,
                }
            )
            transcript.append(
                {
                    "job_id": job_id,
                    "started_ns": started,
                    "finished_ns": finished,
                    "status": 200,
                    "method": "GET",
                    "protocol": "HTTP/1.1",
                    "peer_is_loopback": True,
                    "response_body_bytes": 7,
                    "response_wire_bytes": 20,
                    "path": f"/fixture/test/{job_id}/sitemap/root.xml",
                }
            )
        queue_intervals = [(row["accepted_ns"], row["started_ns"]) for row in outputs]
        service_intervals = [(row["started_ns"], row["finished_ns"]) for row in outputs]
        unfinished_intervals = [(row["accepted_ns"], row["finished_ns"]) for row in outputs]
        result: dict[str, object] = {
            "type": "batch",
            "implementation": "go-worker-pilot",
            "batch_id": "test",
            "phase": "measured",
            "manifest_sha256": job_manifest_sha256(jobs),
            "jobs": outputs,
            "wall_ns": 100,
            "user_cpu_ns": 1,
            "sys_cpu_ns": 1,
            "accepted": count,
            "completed": count,
            "unfinished": 0,
            "panics": 0,
            "process_children": 0,
            "max_queued": orchestrator.interval_max(queue_intervals),
            "max_in_flight": orchestrator.interval_max(service_intervals),
            "max_unfinished": orchestrator.interval_max(unfinished_intervals),
            "transport_connections": {
                "open": 1,
                "maximum_open": 1,
                "in_use_permits": 1,
                "maximum_in_use_permits": 1,
                "permit_limit": 20,
                "waiters": 0,
            },
        }
        snapshot = {
            "open": 1,
            "active": 0,
            "idle": 1,
            "max_open_per_origin": 1,
            "max_idle_per_origin": 1,
            "max_active_global": 1,
            "max_active_per_origin": 1,
            "max_server_handler_open_global": 1,
            "max_server_handler_open_per_origin": 1,
            "max_server_handler_idle_global": 1,
            "max_server_handler_idle_per_origin": 1,
        }
        return result, jobs, transcript, snapshot

    def _validate(
        self,
        result: dict[str, object],
        jobs: list[dict[str, object]],
        transcript: list[dict[str, object]],
        snapshot: dict[str, int],
        *,
        workers: int,
        capacity: int,
    ) -> None:
        corpus = load_corpus(BENCH_ROOT)
        validate_batch(
            result=result,
            jobs=jobs,
            transcript=transcript,
            connection_snapshot=snapshot,
            samples={
                "max_processes": 1,
                "sample_count": 2,
                "periodic_sample_count": 0,
                "coverage_ns": 100,
                "sampler_thread_stopped": True,
            },
            scenarios=scenario_map(corpus),
            defaults=corpus["defaults"],
            workers=workers,
            capacity=capacity,
            implementation="go",
            batch_id="test",
            phase="measured",
            evidence=False,
        )

    def test_historical_max_unfinished_is_a_distinct_capacity_gate(self) -> None:
        result, jobs, transcript, snapshot = self._case(
            count=3, same_origin=False, workers=3, capacity=2
        )
        self.assertLessEqual(result["max_queued"], 2)
        self.assertLessEqual(result["max_in_flight"], 3)
        with self.assertRaisesRegex(RuntimeError, "accepted-but-unfinished"):
            self._validate(result, jobs, transcript, snapshot, workers=3, capacity=2)

    def test_server_handler_close_propagation_peak_is_diagnostic(self) -> None:
        result, jobs, transcript, snapshot = self._case(
            count=1, same_origin=True, workers=2, capacity=2
        )
        snapshot["max_open_per_origin"] = 1
        snapshot["max_server_handler_open_global"] = 21
        snapshot["max_server_handler_open_per_origin"] = 3
        self._validate(result, jobs, transcript, snapshot, workers=2, capacity=2)

    def test_client_local_or_settled_connection_overcap_is_rejected(self) -> None:
        for source in ("local", "settled-global", "settled-origin"):
            with self.subTest(source=source):
                result, jobs, transcript, snapshot = self._case(
                    count=1, same_origin=True, workers=2, capacity=2
                )
                if source == "local":
                    result["transport_connections"]["maximum_in_use_permits"] = 21
                elif source == "settled-global":
                    snapshot["open"] = 21
                else:
                    snapshot["max_open_per_origin"] = 3
                with self.assertRaisesRegex(RuntimeError, "connection cap exceeded"):
                    self._validate(result, jobs, transcript, snapshot, workers=2, capacity=2)

    def test_boolean_client_local_transport_counters_are_rejected(self) -> None:
        result, jobs, transcript, snapshot = self._case(
            count=1, same_origin=True, workers=2, capacity=2
        )
        result["transport_connections"].update(
            {
                "open": True,
                "maximum_open": True,
                "in_use_permits": True,
                "maximum_in_use_permits": True,
                "waiters": False,
            }
        )
        with self.assertRaisesRegex(RuntimeError, "counters are invalid"):
            self._validate(result, jobs, transcript, snapshot, workers=2, capacity=2)

    def test_post_response_pre_fin_idle_peak_is_reported_but_settled_idle_is_gated(self) -> None:
        result, jobs, transcript, snapshot = self._case(
            count=1, same_origin=True, workers=1, capacity=1
        )
        snapshot["max_server_handler_idle_global"] = 99
        snapshot["max_server_handler_idle_per_origin"] = 99
        self._validate(result, jobs, transcript, snapshot, workers=1, capacity=1)
        self.assertEqual(snapshot["max_server_handler_idle_global"], 99)

    def test_per_origin_service_concurrency_is_independently_gated(self) -> None:
        result, jobs, transcript, snapshot = self._case(
            count=3, same_origin=True, workers=3, capacity=3
        )
        with self.assertRaisesRegex(RuntimeError, "per-origin job/service"):
            self._validate(result, jobs, transcript, snapshot, workers=3, capacity=3)

    def test_batch_identity_metadata_is_gated(self) -> None:
        result, jobs, transcript, snapshot = self._case(
            count=1, same_origin=True, workers=1, capacity=1
        )
        result["phase"] = "warmup"
        with self.assertRaisesRegex(RuntimeError, "batch metadata"):
            self._validate(result, jobs, transcript, snapshot, workers=1, capacity=1)

    def test_latency_fields_must_exactly_match_emitted_timestamps(self) -> None:
        for field in ("queue_ns", "service_ns", "end_to_end_ns"):
            with self.subTest(field=field):
                result, jobs, transcript, snapshot = self._case(
                    count=1, same_origin=True, workers=1, capacity=1
                )
                result["jobs"][0][field] += 1
                with self.assertRaisesRegex(RuntimeError, "duration metadata"):
                    self._validate(result, jobs, transcript, snapshot, workers=1, capacity=1)


class FixtureTests(unittest.TestCase):
    def test_connection_open_is_attributed_before_first_request(self) -> None:
        state = FixtureState(load_corpus(BENCH_ROOT))
        state.configure_origins(["http://127.0.0.1:31000"])
        state.activate_arm("open-arm")
        connections = [
            state.open_connection(0, ("127.0.0.1", 40_000 + index)) for index in range(3)
        ]
        snapshot = state.connection_snapshot("open-arm")
        self.assertEqual(snapshot["max_server_handler_open_global"], 3)
        self.assertEqual(snapshot["max_server_handler_open_per_origin"], 3)
        self.assertEqual(snapshot["max_server_handler_idle_global"], 0)
        for connection in connections:
            state.close_connection(connection, "test")
        state.deactivate_arm("open-arm")

    def test_32_distinct_loopback_http11_origins(self) -> None:
        corpus = load_corpus(BENCH_ROOT)
        with FixtureFleet(corpus) as fleet:
            self.assertEqual(len(fleet.origins), 32)
            self.assertEqual(len(set(fleet.origins)), 32)
            batch_id = "fixture-test"
            jobs = make_jobs(corpus, fleet.origins, batch_id=batch_id, suite="capacity", count=1)
            fleet.state.register_batch(arm_token="fixture-arm", batch_id=batch_id, jobs=jobs)
            response = raw_http_get(
                jobs[0]["origin"], f"/fixture/{batch_id}/job-0000/sitemap/root.xml"
            )
            self.assertTrue(response.startswith(b"HTTP/1.1 200 OK\r\n"))
            self.assertIn(b"<urlset", response)
            rows = fleet.state.requests_for("fixture-arm", batch_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["protocol"], "HTTP/1.1")
            self.assertTrue(rows[0]["peer_is_loopback"])

    def test_registered_job_is_rejected_on_wrong_origin(self) -> None:
        corpus = load_corpus(BENCH_ROOT)
        with FixtureFleet(corpus) as fleet:
            batch_id = "wrong-origin"
            jobs = make_jobs(corpus, fleet.origins, batch_id=batch_id, suite="capacity", count=1)
            fleet.state.register_batch(arm_token="wrong-origin-arm", batch_id=batch_id, jobs=jobs)
            response = raw_http_get(
                fleet.origins[1], f"/fixture/{batch_id}/job-0000/sitemap/root.xml"
            )
            self.assertEqual(response, b"")
            self.assertEqual(fleet.state.requests_for("wrong-origin-arm", batch_id), [])


if __name__ == "__main__":
    unittest.main()
