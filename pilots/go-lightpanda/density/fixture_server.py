#!/usr/bin/env python3
"""Silent, exact-byte HTTP fixture for the browser density comparison."""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from python_runner import ALLOWED_CONCURRENCY, EXPECTED_ORIGINS, Task, Workload, load_workload


READY_PATH = Path("/tmp/ready")
LISTEN_ADDRESS = ("0.0.0.0", 8080)


@dataclass(frozen=True, slots=True)
class RoutedTask:
    wave_index: int
    task_index: int
    task: Task


class FixtureState:
    def __init__(self, workload: Workload, concurrency: int) -> None:
        if concurrency not in ALLOWED_CONCURRENCY:
            raise ValueError("concurrency")
        self.workload = workload
        self.concurrency = concurrency
        self.routes = {
            (task.origin_id, task.relative_path): RoutedTask(wave_index, task_index, task)
            for wave_index, wave in enumerate(workload.waves)
            for task_index, task in enumerate(wave.tasks)
        }
        self.condition = threading.Condition()
        self.done = threading.Event()
        self.current_wave = 0
        self.generation = 0
        self.generation_origins: set[str] = set()
        self.active_ids: set[str] = set()
        self.completed_ids: set[str] = set()
        self.completed_by_wave: list[dict[str, str]] = [{}, {}]
        self.barrier_releases = [0, 0]
        self.global_in_flight = 0
        self.origin_in_flight = {origin: 0 for origin in EXPECTED_ORIGINS}
        self.max_global_in_flight = 0
        self.max_per_origin_in_flight = 0
        self.unexpected = {"method": 0, "path": 0, "origin": 0, "duplicate": 0, "total": 0}

    def unexpected_request(self, kind: str) -> None:
        with self.condition:
            self.unexpected[kind] += 1
            self.unexpected["total"] += 1

    def route(self, origin_id: str | None, path: str) -> RoutedTask | None:
        if origin_id not in EXPECTED_ORIGINS:
            self.unexpected_request("origin")
            return None
        routed = self.routes.get((origin_id, path))
        if routed is None:
            self.unexpected_request("path")
            return None
        with self.condition:
            if (
                routed.wave_index != self.current_wave
                or routed.task.id in self.active_ids
                or routed.task.id in self.completed_ids
            ):
                self.unexpected["duplicate"] += 1
                self.unexpected["total"] += 1
                return None
            self.active_ids.add(routed.task.id)
            self.global_in_flight += 1
            self.origin_in_flight[origin_id] += 1
            self.max_global_in_flight = max(self.max_global_in_flight, self.global_in_flight)
            self.max_per_origin_in_flight = max(
                self.max_per_origin_in_flight, self.origin_in_flight[origin_id]
            )
            return routed

    def wait_at_barrier(self, routed: RoutedTask) -> None:
        with self.condition:
            generation = self.generation
            self.generation_origins.add(routed.task.origin_id)
            if len(self.generation_origins) == self.concurrency:
                self.barrier_releases[routed.wave_index] += 1
                self.generation += 1
                self.generation_origins.clear()
                self.condition.notify_all()
                return
            self.condition.wait_for(lambda: self.generation != generation)

    def finish(self, routed: RoutedTask) -> None:
        task = routed.task
        with self.condition:
            self.active_ids.remove(task.id)
            self.completed_ids.add(task.id)
            self.global_in_flight -= 1
            self.origin_in_flight[task.origin_id] -= 1
            self.completed_by_wave[routed.wave_index][task.id] = hashlib.sha256(
                task.response_body
            ).hexdigest()
            if len(self.completed_by_wave[routed.wave_index]) == 8:
                self.current_wave += 1
            if len(self.completed_ids) == 16:
                self.done.set()

    def abandon(self, routed: RoutedTask) -> None:
        task = routed.task
        with self.condition:
            self.active_ids.discard(task.id)
            self.global_in_flight -= 1
            self.origin_in_flight[task.origin_id] -= 1
            self.unexpected["total"] += 1

    def delivery_failed(self) -> None:
        with self.condition:
            self.unexpected["total"] += 1

    def report(self) -> dict[str, Any]:
        expected_releases = 8 // self.concurrency
        with self.condition:
            waves = []
            for wave_index, wave in enumerate(self.workload.waves):
                completed = self.completed_by_wave[wave_index]
                waves.append(
                    {
                        "id": wave.id,
                        "completed": len(completed),
                        "task_ids": [task.id for task in wave.tasks if task.id in completed],
                        "response_sha256": [
                            completed[task.id] for task in wave.tasks if task.id in completed
                        ],
                        "barrier_reached": self.barrier_releases[wave_index]
                        == expected_releases,
                    }
                )
            return {
                "schema_version": 1,
                "workload_sha256": self.workload.sha256,
                "concurrency": self.concurrency,
                "completed": len(self.completed_ids),
                "max_global_in_flight": self.max_global_in_flight,
                "max_per_origin_in_flight": self.max_per_origin_in_flight,
                "waves": waves,
                "unexpected": dict(self.unexpected),
            }


class FixtureServer(http.server.ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, state: FixtureState) -> None:
        self.state = state
        super().__init__(LISTEN_ADDRESS, FixtureHandler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        del request, client_address
        self.state.unexpected_request("path")


class FixtureHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: FixtureServer

    def log_message(self, format: str, *args: Any) -> None:
        del format, args

    def _reject_method(self) -> None:
        self.server.state.unexpected_request("method")
        self.send_response(405)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    do_POST = _reject_method
    do_PUT = _reject_method
    do_PATCH = _reject_method
    do_DELETE = _reject_method
    do_HEAD = _reject_method
    do_OPTIONS = _reject_method

    def do_GET(self) -> None:
        host = self.headers.get("Host", "")
        host_name = host[: -len(":8080")] if host.endswith(":8080") else None
        suffix = ".bench.test"
        origin_id = (
            host_name[: -len(suffix)]
            if host_name is not None and host_name.endswith(suffix)
            else None
        )
        routed = self.server.state.route(origin_id, self.path)
        if routed is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return

        committed = False
        try:
            self.server.state.wait_at_barrier(routed)
            body = routed.task.response_body
            # Commit the completed request before its response is observable so
            # a fast client cannot race the next wave against current_wave.
            self.server.state.finish(routed)
            committed = True
            self.send_response(routed.task.status)
            self.send_header("Content-Type", "text/html; charset=us-ascii")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except Exception:
            if committed:
                self.server.state.delivery_failed()
            raise
        finally:
            if not committed:
                self.server.state.abandon(routed)


def serve(workload: Workload, concurrency: int) -> dict[str, Any]:
    state = FixtureState(workload, concurrency)
    server = FixtureServer(state)
    thread = threading.Thread(target=server.serve_forever, name="fixture-http")
    thread.start()
    READY_PATH.touch(exist_ok=True)
    state.done.wait()
    server.shutdown()
    server.server_close()
    thread.join()
    return state.report()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workload", required=True, type=Path)
    parser.add_argument("--concurrency", required=True, type=int)
    args = parser.parse_args(argv)
    workload = load_workload(args.workload)
    report = serve(workload, args.concurrency)
    print(
        "DENSITY_FIXTURE_REPORT="
        + json.dumps(report, ensure_ascii=True, separators=(",", ":")),
        flush=True,
    )
    return 0 if report["completed"] == 16 and report["unexpected"]["total"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
