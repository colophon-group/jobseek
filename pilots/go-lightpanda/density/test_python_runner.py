from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import signal
import stat
import tempfile
import unittest
from unittest import mock
from pathlib import Path
from typing import Any

import python_runner as runner


HERE = Path(__file__).resolve().parent
WORKLOAD_PATH = HERE / "workload.v1.json"
SOURCE_COMMIT = "1" * 40
IMAGE_IDENTITY = "sha256:" + "2" * 64


class _Response:
    status = 201


class FakeWorld:
    def __init__(self, workload: runner.Workload) -> None:
        self.by_target = {
            f"http://{task.origin_id}.bench.test:8080{task.relative_path}": task
            for wave in workload.waves
            for task in wave.tasks
        }
        self.expected_drivers = 0
        self.drivers: list[FakeDriver] = []
        self.driver_factory_calls = 0
        self.browsers: list[FakeBrowser] = []
        self.contexts: list[FakeContext] = []
        self.pages: list[FakePage] = []
        self.navigation_ids: list[str] = []
        self.bad_html_id: str | None = None
        self.page_close_failure_id: str | None = None
        self.teardown_failure_index: int | None = None
        self.startup_failure_index: int | None = None

    async def driver_factory(self) -> "FakeDriver":
        index = self.driver_factory_calls
        self.driver_factory_calls += 1
        if self.startup_failure_index == index:
            raise RuntimeError("private startup detail")
        driver = FakeDriver(self, index)
        self.drivers.append(driver)
        await asyncio.sleep(0)
        return driver


class FakeDriver:
    def __init__(self, world: FakeWorld, index: int) -> None:
        self.world = world
        self.index = index
        self.chromium = FakeChromium(world)
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True
        if self.world.browsers and len([browser for browser in self.world.browsers if browser.closed]) != 16:
            raise AssertionError("driver stopped before task browser cleanup")
        if self.world.teardown_failure_index == self.index:
            raise RuntimeError("private teardown detail")


class FakeChromium:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world

    async def launch(self, *, headless: bool) -> "FakeBrowser":
        if not headless or len(self.world.drivers) != self.world.expected_drivers:
            raise AssertionError("launch before every driver was ready")
        browser = FakeBrowser(self.world)
        self.world.browsers.append(browser)
        await asyncio.sleep(0)
        return browser


class FakeBrowser:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.closed = False

    async def new_context(self) -> "FakeContext":
        context = FakeContext(self.world)
        self.world.contexts.append(context)
        return context

    async def close(self) -> None:
        self.closed = True


class FakeContext:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.closed = False

    async def new_page(self) -> "FakePage":
        page = FakePage(self.world)
        self.world.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True


class FakePage:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.task: runner.Task | None = None
        self.url = ""
        self.closed = False

    def set_default_timeout(self, timeout: int) -> None:
        if timeout != 0:
            raise AssertionError

    def set_default_navigation_timeout(self, timeout: int) -> None:
        if timeout != 0:
            raise AssertionError

    async def goto(self, target: str, *, wait_until: str) -> _Response:
        if wait_until != "load":
            raise AssertionError
        self.url = target
        self.task = self.world.by_target[target]
        self.world.navigation_ids.append(self.task.id)
        await asyncio.sleep(0)
        return _Response()

    async def evaluate(self, expression: str) -> Any:
        if self.task is None:
            raise AssertionError
        if expression == "document.documentElement.outerHTML":
            value = self.task.response_body.removeprefix(b"<!doctype html>").decode("ascii")
            return value + ("x" if self.world.bad_html_id == self.task.id else "")
        if expression == "document.title" and self.task.expected_evaluation_json is not None:
            return json.loads(self.task.expected_evaluation_json)
        raise AssertionError("unexpected expression")

    async def close(self) -> None:
        self.closed = True
        if self.task is not None and self.world.page_close_failure_id == self.task.id:
            raise RuntimeError("private page close detail")


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workload = runner.load_workload(WORKLOAD_PATH)

    def _mutated(self, source: Path, mutate: Any) -> Path:
        raw = json.loads(source.read_text(encoding="utf-8"))
        mutate(raw)
        temporary = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
        with temporary:
            json.dump(raw, temporary)
        return Path(temporary.name)

    def test_checked_in_contract_and_raw_hashes(self) -> None:
        self.assertEqual(sum(len(wave.tasks) for wave in self.workload.waves), 16)
        self.assertEqual(self.workload.sha256, hashlib.sha256(WORKLOAD_PATH.read_bytes()).hexdigest())

    def test_workload_rejects_shape_ascii_and_evaluation_mutations(self) -> None:
        mutations = [
            lambda raw: raw.update(extra=True),
            lambda raw: raw["waves"][0]["tasks"][0].update(response_body="non-ascii-é"),
            lambda raw: raw["waves"][0]["tasks"][4].update(expected_evaluation_json='"spaced" '),
            lambda raw: raw["waves"][0]["tasks"][4].update(expected_evaluation_sha256="0" * 64),
            lambda raw: raw["waves"][1]["tasks"][0].update(origin_id="origin-7"),
        ]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                with self.assertRaises(runner.ContractError):
                    runner.load_workload(self._mutated(WORKLOAD_PATH, mutate))

    def test_b1_encoding_is_compact_ascii_json(self) -> None:
        self.assertEqual(runner.evaluation_json_bytes("café"), b'"caf\\u00e9"')
        self.assertEqual(runner.evaluation_json_bytes('a"b'), b'"a\\"b"')

    def test_cli_protocol_surrounds_manifest_read_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workload = Path(directory) / "workload.json"
            output = io.StringIO()
            events: list[str] = []

            class Protocol:
                def wait_initial(inner_self) -> None:
                    events.append("initial")
                    self.assertEqual(output.getvalue(), "")
                    workload.write_text("not the manifest", encoding="ascii")

                def wait_final(inner_self) -> None:
                    events.append("final")
                    self.assertEqual(output.getvalue(), "")

                def close(inner_self) -> None:
                    events.append("close")

            exit_code = runner.main(
                [
                    "--workload",
                    str(workload),
                    "--concurrency",
                    "4",
                    "--source-commit",
                    SOURCE_COMMIT,
                    "--image-identity",
                    IMAGE_IDENTITY,
                ],
                protocol_factory=Protocol,
                output=output,
            )
            self.assertEqual(exit_code, 1)
            self.assertEqual(events, ["initial", "final", "close"])
            self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_cli_removed_start_gate_uses_both_protocol_phases(self) -> None:
        output = io.StringIO()
        events: list[str] = []

        class Protocol:
            def wait_initial(inner_self) -> None:
                events.append("initial")
                self.assertEqual(output.getvalue(), "")

            def wait_final(inner_self) -> None:
                events.append("final")

            def close(inner_self) -> None:
                events.append("close")

        exit_code = runner.main(
            ["--start-gate", "/tmp/controller-start"],
            protocol_factory=Protocol,
            output=output,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(events, ["initial", "final", "close"])
        self.assertFalse(json.loads(output.getvalue())["ok"])

    def test_cli_protocol_cancellation_emits_no_evidence(self) -> None:
        output = io.StringIO()
        events: list[str] = []

        class Protocol:
            def wait_initial(inner_self) -> None:
                events.append("initial")

            def wait_final(inner_self) -> None:
                events.append("final")
                raise runner._ProtocolCancelled

            def close(inner_self) -> None:
                events.append("close")

        exit_code = runner.main(
            ["--start-gate", "/tmp/controller-start"],
            protocol_factory=Protocol,
            output=output,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(events, ["initial", "final", "close"])
        self.assertEqual(output.getvalue(), "")

    def test_protocol_marker_is_exact_exclusive_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "controller-final"
            runner._create_marker(marker)
            metadata = marker.lstat()
            self.assertTrue(stat.S_ISREG(metadata.st_mode))
            self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
            self.assertEqual(metadata.st_size, 0)
            self.assertEqual(metadata.st_nlink, 1)
            self.assertEqual(metadata.st_uid, os.geteuid())
            self.assertEqual(metadata.st_gid, os.getegid())
            with self.assertRaises(FileExistsError):
                runner._create_marker(marker)
            runner._validate_marker(marker)
            marker.unlink()
            self.assertFalse(marker.exists())

    def test_release_between_handler_arm_and_wait_is_not_lost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "controller-initial"
            events: list[str] = []
            handlers: dict[signal.Signals, Any] = {}
            create_marker = runner._create_marker

            def install_handler(number: signal.Signals, handler: Any) -> None:
                events.append("arm" if callable(handler) else "restore")
                handlers[number] = handler

            def create_and_release(path: Path) -> None:
                events.append("create")
                create_marker(path)
                handlers[signal.SIGUSR1](signal.SIGUSR1, None)
                events.append("release")

            protocol = object.__new__(runner._SignalMarkerProtocol)
            with (
                mock.patch.object(runner.signal, "getsignal", return_value=signal.SIG_DFL),
                mock.patch.object(runner.signal, "signal", side_effect=install_handler),
                mock.patch.object(runner.signal, "pause", side_effect=AssertionError("release was lost")),
                mock.patch.object(runner, "_create_marker", side_effect=create_and_release),
            ):
                protocol._wait(marker, signal.SIGUSR1)

            self.assertEqual(events, ["arm", "create", "release", "restore"])
            self.assertFalse(marker.exists())


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.workload = runner.load_workload(WORKLOAD_PATH)

    async def _run(self, world: FakeWorld, concurrency: int = 4) -> dict[str, Any]:
        world.expected_drivers = concurrency
        return await runner.run_benchmark(
            self.workload,
            concurrency,
            SOURCE_COMMIT,
            IMAGE_IDENTITY,
            driver_factory=world.driver_factory,
        )

    async def test_exact_workers_fresh_lifecycles_sequential_waves_and_closed_report(self) -> None:
        world = FakeWorld(self.workload)
        report = await self._run(world)

        self.assertTrue(report["ok"])
        self.assertTrue(report["driver_teardown_ok"])
        self.assertEqual((report["accepted"], report["completed"], report["succeeded"]), (16, 16, 16))
        self.assertEqual(report["max_in_flight"], 4)
        self.assertEqual(len(world.drivers), 4)
        self.assertEqual((len(world.browsers), len(world.contexts), len(world.pages)), (16, 16, 16))
        self.assertTrue(all(item.closed for item in world.browsers + world.contexts + world.pages))
        self.assertTrue(all(driver.stopped for driver in world.drivers))
        self.assertTrue(all(task_id.startswith("w0-") for task_id in world.navigation_ids[:8]))
        self.assertTrue(all(task_id.startswith("w1-") for task_id in world.navigation_ids[8:]))

        wire = json.dumps(report, separators=(",", ":"))
        for forbidden in ("http://", "<html", "document.title", "private"):
            self.assertNotIn(forbidden, wire)
        self.assertEqual(
            set(report),
            {
                "schema_version",
                "implementation",
                "workload_sha256",
                "source_commit",
                "image_identity",
                "concurrency",
                "ok",
                "accepted",
                "completed",
                "succeeded",
                "max_in_flight",
                "elapsed_ns",
                "driver_teardown_ok",
                "conservation",
                "tasks",
            },
        )

    async def test_comparison_and_cleanup_mutations_fail_closed_but_conserve(self) -> None:
        bad_id = self.workload.waves[0].tasks[4].id
        for mutation in ("html", "cleanup"):
            world = FakeWorld(self.workload)
            if mutation == "html":
                world.bad_html_id = bad_id
            else:
                world.page_close_failure_id = bad_id
            report = await self._run(world)
            task = next(item for item in report["tasks"] if item["id"] == bad_id)
            self.assertFalse(report["ok"])
            self.assertEqual(report["conservation"]["completed"], 16)
            self.assertEqual(task["outcome"], "mismatch" if mutation == "html" else "cleanup_failure")
            self.assertTrue(all(browser.closed for browser in world.browsers))

    async def test_driver_teardown_failure_is_sanitized_and_authoritative(self) -> None:
        world = FakeWorld(self.workload)
        world.teardown_failure_index = 0
        report = await self._run(world)
        self.assertFalse(report["ok"])
        self.assertFalse(report["driver_teardown_ok"])
        self.assertEqual(report["succeeded"], 16)
        self.assertNotIn("private teardown detail", json.dumps(report))

    async def test_partial_driver_startup_fails_without_admission_or_hang(self) -> None:
        world = FakeWorld(self.workload)
        world.startup_failure_index = 1
        report = await asyncio.wait_for(self._run(world), timeout=1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["accepted"], 0)
        self.assertEqual(report["tasks"], [])
        self.assertEqual(world.driver_factory_calls, 4)
        self.assertTrue(all(driver.stopped for driver in world.drivers))
        self.assertNotIn("private startup detail", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
