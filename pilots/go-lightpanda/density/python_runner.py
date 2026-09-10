#!/usr/bin/env python3
"""Closed-output Playwright comparison arm for the fixed-RAM density pilot.

The controller, not this process, is the hard deadline and process-tree safety
boundary.  This runner intentionally has no thread or coroutine timeout that
purports to make a wedged browser safe.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import signal
import stat
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable


SCHEMA_VERSION = 1
IMPLEMENTATION = "python-chromium"
EXPECTED_WAVES = ("w0", "w1")
ALLOWED_CONCURRENCY = (1, 4, 8)
EXPECTED_ORIGINS = tuple(f"origin-{number}" for number in range(8))
MAX_INPUT_BYTES = 128 << 10
EXPECTED_WORKLOAD_SHA256 = "3db365d2a484b932049313d53469d07ffb2c5d9fdfe05821fd87cf67b1557740"
INITIAL_MARKER = Path("/tmp/controller-initial")
FINAL_MARKER = Path("/tmp/controller-final")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_WORKLOAD_KEYS = {"schema_version", "origin_count", "waves"}
_WAVE_KEYS = {"id", "tasks"}
_TASK_KEYS = {
    "id",
    "origin_id",
    "mode",
    "relative_path",
    "status",
    "response_body",
    "expected_outer_html_size",
    "expected_outer_html_sha256",
    "expected_evaluation_json",
    "expected_evaluation_sha256",
}


class ContractError(ValueError):
    """A trusted, local pilot input did not match the closed contract."""


class _ProtocolCancelled(BaseException):
    """The controller interrupted the evidence process."""


class _ClosedArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ContractError("arguments")


def _create_marker(path: Path) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        metadata = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size != 0
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
    ):
        raise ContractError("protocol marker")


def _validate_marker(path: Path) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size != 0
        or metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
    ):
        raise ContractError("protocol marker")


class _SignalMarkerProtocol:
    """Race-free controller rendezvous using distinct main-thread signals."""

    def __init__(self) -> None:
        self._previous_cancel_handlers = {
            number: signal.getsignal(number)
            for number in (signal.SIGINT, signal.SIGTERM)
        }
        for number in self._previous_cancel_handlers:
            signal.signal(number, self._cancel)

    @staticmethod
    def _cancel(_number: int, _frame: Any) -> None:
        raise _ProtocolCancelled

    def _wait(self, path: Path, release_signal: signal.Signals) -> None:
        released = threading.Event()
        previous = signal.getsignal(release_signal)

        def release(_number: int, _frame: Any) -> None:
            released.set()

        # Arm the handler before publishing the marker so the controller can
        # never race marker observation against signal readiness.
        signal.signal(release_signal, release)
        try:
            _create_marker(path)
            # Python dispatches signal handlers on the main interpreter thread.
            # signal.pause() yields to that dispatcher; Event.wait() may remain
            # inside a restarted lock wait and never run the Python handler.
            while not released.is_set():
                signal.pause()
            _validate_marker(path)
            path.unlink()
        finally:
            signal.signal(release_signal, previous)

    def wait_initial(self) -> None:
        self._wait(INITIAL_MARKER, signal.SIGUSR1)

    def wait_final(self) -> None:
        self._wait(FINAL_MARKER, signal.SIGUSR2)

    def close(self) -> None:
        for number, previous in self._previous_cancel_handlers.items():
            signal.signal(number, previous)


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    origin_id: str
    mode: str
    relative_path: str
    status: int
    response_body: bytes
    expected_outer_html_size: int
    expected_outer_html_sha256: str
    expected_evaluation_json: bytes | None
    expected_evaluation_sha256: str | None


@dataclass(frozen=True, slots=True)
class Wave:
    id: str
    tasks: tuple[Task, ...]


@dataclass(frozen=True, slots=True)
class Workload:
    sha256: str
    waves: tuple[Wave, ...]


def _object(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError("object shape")
    return value


def _load_json_bytes(path: Path) -> tuple[bytes, Any]:
    contents = path.read_bytes()
    if not contents or len(contents) > MAX_INPUT_BYTES:
        raise ContractError("input size")
    try:
        value = json.loads(contents, parse_constant=lambda _value: (_ for _ in ()).throw(ContractError("constant")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("json") from exc
    return contents, value


def _ascii_bytes(value: Any, *, allow_empty: bool = False) -> bytes:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ContractError("ascii string")
    try:
        return value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ContractError("ascii string") from exc


def load_workload(path: Path) -> Workload:
    contents, raw = _load_json_bytes(path)
    contents_sha256 = hashlib.sha256(contents).hexdigest()
    if contents_sha256 != EXPECTED_WORKLOAD_SHA256:
        raise ContractError("workload identity")
    root = _object(raw, _WORKLOAD_KEYS)
    if root["schema_version"] != SCHEMA_VERSION or root["origin_count"] != 8:
        raise ContractError("workload version")
    raw_waves = root["waves"]
    if not isinstance(raw_waves, list) or len(raw_waves) != 2:
        raise ContractError("wave count")

    waves: list[Wave] = []
    seen_ids: set[str] = set()
    seen_routes: set[tuple[str, str]] = set()
    for wave_index, raw_wave in enumerate(raw_waves):
        wave_object = _object(raw_wave, _WAVE_KEYS)
        wave_id = wave_object["id"]
        if wave_id != EXPECTED_WAVES[wave_index]:
            raise ContractError("wave id")
        raw_tasks = wave_object["tasks"]
        if not isinstance(raw_tasks, list) or len(raw_tasks) != 8:
            raise ContractError("task count")
        tasks: list[Task] = []
        wave_origins: set[str] = set()
        for raw_task in raw_tasks:
            task_object = _object(raw_task, _TASK_KEYS)
            task_id = task_object["id"]
            origin_id = task_object["origin_id"]
            mode = task_object["mode"]
            relative_path = task_object["relative_path"]
            if not isinstance(task_id, str) or _ID_RE.fullmatch(task_id) is None or task_id in seen_ids:
                raise ContractError("task id")
            if origin_id not in EXPECTED_ORIGINS or origin_id in wave_origins:
                raise ContractError("task origin")
            if mode not in ("b0", "b1"):
                raise ContractError("task mode")
            if (
                not isinstance(relative_path, str)
                or not relative_path.startswith("/density/")
                or len(relative_path) > 128
                or "?" in relative_path
                or "#" in relative_path
                or any(ord(character) < 0x20 or ord(character) > 0x7E for character in relative_path)
                or (origin_id, relative_path) in seen_routes
            ):
                raise ContractError("task route")
            if task_object["status"] != 201:
                raise ContractError("task status")
            response_body = _ascii_bytes(task_object["response_body"])
            outer_size = task_object["expected_outer_html_size"]
            outer_sha = task_object["expected_outer_html_sha256"]
            if (
                not isinstance(outer_size, int)
                or isinstance(outer_size, bool)
                or not 0 < outer_size <= 1 << 20
                or not isinstance(outer_sha, str)
                or _SHA_RE.fullmatch(outer_sha) is None
            ):
                raise ContractError("outer html expectation")

            evaluation_raw = task_object["expected_evaluation_json"]
            evaluation_sha = task_object["expected_evaluation_sha256"]
            evaluation_bytes: bytes | None
            if mode == "b0":
                if evaluation_raw is not None or evaluation_sha is not None:
                    raise ContractError("b0 evaluation")
                evaluation_bytes = None
            else:
                evaluation_bytes = _ascii_bytes(evaluation_raw, allow_empty=True)
                try:
                    decoded_evaluation = json.loads(evaluation_bytes)
                except json.JSONDecodeError as exc:
                    raise ContractError("b1 evaluation json") from exc
                compact = json.dumps(
                    decoded_evaluation, ensure_ascii=True, separators=(",", ":")
                ).encode("ascii")
                if (
                    not isinstance(decoded_evaluation, str)
                    or compact != evaluation_bytes
                    or not isinstance(evaluation_sha, str)
                    or _SHA_RE.fullmatch(evaluation_sha) is None
                    or hashlib.sha256(evaluation_bytes).hexdigest() != evaluation_sha
                ):
                    raise ContractError("b1 evaluation expectation")

            seen_ids.add(task_id)
            wave_origins.add(origin_id)
            seen_routes.add((origin_id, relative_path))
            tasks.append(
                Task(
                    id=task_id,
                    origin_id=origin_id,
                    mode=mode,
                    relative_path=relative_path,
                    status=201,
                    response_body=response_body,
                    expected_outer_html_size=outer_size,
                    expected_outer_html_sha256=outer_sha,
                    expected_evaluation_json=evaluation_bytes,
                    expected_evaluation_sha256=evaluation_sha,
                )
            )
        if wave_origins != set(EXPECTED_ORIGINS):
            raise ContractError("wave origins")
        waves.append(Wave(id=wave_id, tasks=tuple(tasks)))

    if len(seen_ids) != 16:
        raise ContractError("task conservation")
    return Workload(sha256=contents_sha256, waves=tuple(waves))


def evaluation_json_bytes(value: Any) -> bytes:
    """Encode B1 exactly as compact ASCII JSON, matching the runtime contract."""

    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _empty_task_report(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "mode": task.mode,
        "outcome": "internal_failure",
        "status_matches": False,
        "final_url_matches": False,
        "html_matches": False,
        "html_bytes": 0,
        "html_sha256": None,
        "evaluation_present": False,
        "evaluation_matches": task.mode == "b0",
        "evaluation_bytes": 0,
        "evaluation_sha256": None,
        "cleanup_ok": True,
    }


async def _run_one(driver: Any, task: Task) -> dict[str, Any]:
    report = _empty_task_report(task)
    target = f"http://{task.origin_id}.bench.test:8080{task.relative_path}"
    browser = context = page = None
    stage = "browser_failure"
    try:
        browser = await driver.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        page.set_default_timeout(0)
        page.set_default_navigation_timeout(0)

        stage = "navigation_failure"
        response = await page.goto(target, wait_until="load")
        report["status_matches"] = response is not None and response.status == task.status
        report["final_url_matches"] = page.url == target

        stage = "extraction_failure"
        outer_html = await page.evaluate("document.documentElement.outerHTML")
        if not isinstance(outer_html, str):
            raise TypeError
        outer_bytes = outer_html.encode("utf-8")
        report["html_bytes"] = len(outer_bytes)
        report["html_sha256"] = hashlib.sha256(outer_bytes).hexdigest()
        report["html_matches"] = (
            report["html_bytes"] == task.expected_outer_html_size
            and report["html_sha256"] == task.expected_outer_html_sha256
        )

        if task.mode == "b1":
            stage = "evaluation_failure"
            evaluation = await page.evaluate("document.title")
            evaluation_bytes = evaluation_json_bytes(evaluation)
            report["evaluation_present"] = True
            report["evaluation_bytes"] = len(evaluation_bytes)
            report["evaluation_sha256"] = hashlib.sha256(evaluation_bytes).hexdigest()
            report["evaluation_matches"] = (
                evaluation_bytes == task.expected_evaluation_json
                and report["evaluation_sha256"] == task.expected_evaluation_sha256
            )

        report["outcome"] = (
            "success"
            if report["status_matches"]
            and report["final_url_matches"]
            and report["html_matches"]
            and report["evaluation_matches"]
            else "mismatch"
        )
    except Exception:
        report["outcome"] = stage
    finally:
        cleanup_ok = True
        for resource in (page, context, browser):
            if resource is None:
                continue
            try:
                await resource.close()
            except Exception:
                cleanup_ok = False
        report["cleanup_ok"] = cleanup_ok
        if not cleanup_ok:
            report["outcome"] = "cleanup_failure"
    return report


async def _default_driver_factory() -> Any:
    from playwright.async_api import async_playwright

    return await async_playwright().start()


async def run_benchmark(
    workload: Workload,
    concurrency: int,
    source_commit: str,
    image_identity: str,
    *,
    driver_factory: Callable[[], Awaitable[Any]] = _default_driver_factory,
) -> dict[str, Any]:
    if concurrency not in ALLOWED_CONCURRENCY:
        raise ContractError("concurrency")
    if _COMMIT_RE.fullmatch(source_commit) is None or _IMAGE_RE.fullmatch(image_identity) is None:
        raise ContractError("identity")

    # A single wave is the entire admission capacity; the next wave is not
    # admitted until queue.join() proves the first has completed and cleaned.
    queue: asyncio.Queue[Task | None] = asyncio.Queue(maxsize=8)
    start = asyncio.Event()
    ready = [asyncio.get_running_loop().create_future() for _ in range(concurrency)]
    reports: list[dict[str, Any]] = []
    meter = {"accepted": 0, "in_flight": 0, "max_in_flight": 0}
    teardown_states = [True] * concurrency

    async def worker(worker_index: int) -> None:
        driver = None
        startup_ok = False
        try:
            try:
                driver = await driver_factory()
                startup_ok = True
            except Exception:
                startup_ok = False
            ready[worker_index].set_result(startup_ok)
            await start.wait()
            if not startup_ok:
                return
            while True:
                item = await queue.get()
                if item is None:
                    queue.task_done()
                    return
                task = item
                meter["in_flight"] += 1
                meter["max_in_flight"] = max(meter["max_in_flight"], meter["in_flight"])
                try:
                    reports.append(await _run_one(driver, task))
                finally:
                    meter["in_flight"] -= 1
                    queue.task_done()
        finally:
            if driver is not None:
                try:
                    await driver.stop()
                except Exception:
                    teardown_states[worker_index] = False

    workers = [asyncio.create_task(worker(index)) for index in range(concurrency)]
    startup_states = await asyncio.gather(*ready)

    measured_started_ns = time.perf_counter_ns()
    start.set()
    if all(startup_states):
        for wave_id in EXPECTED_WAVES:
            wave = next(item for item in workload.waves if item.id == wave_id)
            for task in wave.tasks:
                meter["accepted"] += 1
                queue.put_nowait(task)
            await queue.join()
    measured_finished_ns = time.perf_counter_ns()

    for _ in range(sum(startup_states)):
        queue.put_nowait(None)
    if any(startup_states):
        await queue.join()
    await asyncio.gather(*workers)
    driver_teardown_ok = all(teardown_states)

    task_order = {
        task.id: index
        for index, task in enumerate(task for wave in workload.waves for task in wave.tasks)
    }
    reports.sort(key=lambda item: task_order[item["id"]])
    succeeded = sum(report["outcome"] == "success" for report in reports)
    completed = len(reports)
    accepted = meter["accepted"]
    failed = completed - succeeded
    queued = accepted - completed
    ok = (
        all(startup_states)
        and accepted == 16
        and completed == 16
        and succeeded == 16
        and meter["max_in_flight"] == concurrency
        and queued == 0
        and meter["in_flight"] == 0
        and driver_teardown_ok
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "implementation": IMPLEMENTATION,
        "workload_sha256": workload.sha256,
        "source_commit": source_commit,
        "image_identity": image_identity,
        "concurrency": concurrency,
        "ok": ok,
        "accepted": accepted,
        "completed": completed,
        "succeeded": succeeded,
        "max_in_flight": meter["max_in_flight"],
        "elapsed_ns": max(0, measured_finished_ns - measured_started_ns),
        "driver_teardown_ok": driver_teardown_ok,
        "conservation": {
            "accepted": accepted,
            "completed": completed,
            "succeeded": succeeded,
            "failed": failed,
            "queued": queued,
            "in_flight": meter["in_flight"],
        },
        "tasks": reports,
    }


def _failed_report(
    workload_sha256: str | None,
    source_commit: str,
    image_identity: str,
    concurrency: int,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "implementation": IMPLEMENTATION,
        "workload_sha256": workload_sha256,
        "source_commit": source_commit if _COMMIT_RE.fullmatch(source_commit) else None,
        "image_identity": image_identity if _IMAGE_RE.fullmatch(image_identity) else None,
        "concurrency": concurrency if concurrency in ALLOWED_CONCURRENCY else 0,
        "ok": False,
        "accepted": 0,
        "completed": 0,
        "succeeded": 0,
        "max_in_flight": 0,
        "elapsed_ns": 0,
        "driver_teardown_ok": False,
        "conservation": {
            "accepted": 0,
            "completed": 0,
            "succeeded": 0,
            "failed": 0,
            "queued": 0,
            "in_flight": 0,
        },
        "tasks": [],
    }


def _raw_sha(path: Path) -> str | None:
    try:
        contents = path.read_bytes()
        return hashlib.sha256(contents).hexdigest() if contents else None
    except OSError:
        return None


def main(
    argv: list[str] | None = None,
    *,
    protocol_factory: Callable[[], Any] = _SignalMarkerProtocol,
    output: Any = None,
) -> int:
    parser = _ClosedArgumentParser(add_help=False)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--image-identity", required=True)
    args = argparse.Namespace(
        workload=Path(""), concurrency=0, source_commit="", image_identity=""
    )
    arguments_ok = False
    try:
        args = parser.parse_args(argv)
        arguments_ok = True
    except Exception:
        pass

    protocol = protocol_factory()
    try:
        # The initial release precedes all manifest, Playwright driver, pool,
        # and browser initialization, giving the controller an idle baseline.
        protocol.wait_initial()

        if arguments_ok:
            try:
                workload = load_workload(args.workload)
                report = asyncio.run(
                    run_benchmark(
                        workload,
                        args.concurrency,
                        args.source_commit,
                        args.image_identity,
                    )
                )
            except Exception:
                report = _failed_report(
                    _raw_sha(args.workload),
                    args.source_commit,
                    args.image_identity,
                    args.concurrency,
                )
        else:
            report = _failed_report(
                _raw_sha(args.workload),
                args.source_commit,
                args.image_identity,
                args.concurrency,
            )

        # run_benchmark does not return until every page, context, browser,
        # driver, and worker has been torn down.  Keep the closed report in
        # memory while the controller takes its final live-cgroup sample.
        protocol.wait_final()
        destination = sys.stdout if output is None else output
        destination.write(json.dumps(report, ensure_ascii=True, separators=(",", ":")) + "\n")
        return 0 if report["ok"] else 1
    except _ProtocolCancelled:
        return 1
    except Exception:
        return 1
    finally:
        protocol.close()


if __name__ == "__main__":
    raise SystemExit(main())
