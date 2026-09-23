"""Frozen TLS JSON-LD fixture; its report is admission evidence."""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import threading
import time
from pathlib import Path
from typing import Any, cast

WORKLOAD_SHA256 = "4be1503fef65b7ac74f1085f168cd7d2e9db19060fdd8271abb7b9ca42ef5390"
ORIGINS = tuple(f"origin-{index}" for index in range(4))
RoutedTask = tuple[int, dict[str, Any], bytes]


def load_workload(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    workload = json.loads(raw)
    if (
        hashlib.sha256(raw).hexdigest() != WORKLOAD_SHA256
        or workload.get("schema_version") != 3
        or workload.get("input_generation") != "lane-v3-generation-1"
        or workload.get("fixture_ipv4") != "11.252.0.2"
        or workload.get("origin_count") != 4
        or len(workload.get("waves", [])) != 4
    ):
        raise ValueError("workload identity changed")
    seen: set[str] = set()
    for wave_index, wave in enumerate(workload["waves"]):
        tasks = wave.get("tasks", [])
        if (
            wave.get("id") != f"w{wave_index}"
            or len(tasks) != 4
            or {task.get("origin_id") for task in tasks} != set(ORIGINS)
        ):
            raise ValueError("workload wave changed")
        for task in tasks:
            expected = {"normal": (0, 0), "slow": (350, 0), "large": (0, 262144)}
            if task.get("id") in seen or expected.get(task.get("profile")) != (
                task.get("delay_ms"),
                task.get("padding_bytes"),
            ):
                raise ValueError("workload task changed")
            seen.add(task["id"])
    if len(seen) != 16:
        raise ValueError("workload count changed")
    return workload


def response_body(task: dict[str, Any]) -> bytes:
    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": task["title"],
        "description": f"<p>{task['description']}</p>",
        "employmentType": task["employment_type"],
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": task["location"]},
        "identifier": {"@type": "PropertyValue", "value": task["id"]},
        "datePosted": "2026-09-01",
    }
    encoded = json.dumps(posting, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return (
        '<!doctype html><script type="application/ld+json">'
        + encoded
        + "</script><main>"
        + task["id"]
        + "</main><div hidden>"
        + "x" * task["padding_bytes"]
        + "</div>"
    ).encode("ascii")


class FixtureState:
    def __init__(self, workload: dict[str, Any], concurrency: int) -> None:
        if concurrency not in {1, 4}:
            raise ValueError("concurrency")
        self.concurrency = concurrency
        self.condition = threading.Condition()
        self.done = threading.Event()
        self.routes: dict[tuple[str, str], RoutedTask] = {
            (task["origin_id"], task["path"]): (wave, task, response_body(task))
            for wave, group in enumerate(workload["waves"])
            for task in group["tasks"]
            if task["origin_id"] in ORIGINS[:concurrency]
        }
        self.expected_count = 4 * concurrency
        self.generation = self.in_flight = self.max_global = 0
        self.members: set[str] = set()
        self.active: set[str] = set()
        self.completed: list[str] = []
        self.digests: list[str] = []
        self.per_origin = {origin: 0 for origin in ORIGINS}
        self.max_per_origin = self.unexpected = 0
        self.predecessors = {
            task["id"]: workload["waves"][wave - 1]["tasks"][index]["id"]
            for wave in range(1, 4)
            for index, task in enumerate(workload["waves"][wave]["tasks"])
            if task["origin_id"] in ORIGINS[:concurrency]
        }

    def begin(self, origin: str, path: str) -> RoutedTask | None:
        with self.condition:
            routed = self.routes.get((origin, path))
            if not routed or routed[1]["id"] in (self.active | set(self.completed)):
                self.unexpected += 1
                return None
            task = routed[1]
            predecessor = self.predecessors.get(task["id"])
            if predecessor and not self.condition.wait_for(
                lambda: predecessor in self.completed, timeout=10
            ):
                self.unexpected += 1
                return None
            if task["id"] in (self.active | set(self.completed)):
                self.unexpected += 1
                return None
            self.active.add(task["id"])
            self.in_flight += 1
            self.per_origin[origin] += 1
            self.max_global = max(self.max_global, self.in_flight)
            self.max_per_origin = max(self.max_per_origin, self.per_origin[origin])
            return routed

    def barrier(self, routed: RoutedTask) -> None:
        with self.condition:
            generation = self.generation
            self.members.add(routed[1]["origin_id"])
            if len(self.members) == self.concurrency:
                self.members.clear()
                self.generation += 1
                self.condition.notify_all()
            elif not self.condition.wait_for(lambda: generation != self.generation, timeout=10):
                raise TimeoutError("fixture concurrency barrier")

    def finish(self, routed: RoutedTask) -> None:
        with self.condition:
            task, body = routed[1:]
            self.active.remove(task["id"])
            self.completed.append(task["id"])
            self.digests.append(hashlib.sha256(body).hexdigest())
            self.in_flight -= 1
            self.per_origin[task["origin_id"]] -= 1
            self.condition.notify_all()
            if len(self.completed) == self.expected_count:
                self.done.set()

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": 3,
            "requests": len(self.completed),
            "task_ids": self.completed,
            "response_sha256": self.digests,
            "max_global_in_flight": self.max_global,
            "max_per_origin_in_flight": self.max_per_origin,
            "unexpected": self.unexpected,
        }


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = False
    allow_reuse_address = True

    def __init__(self, state: FixtureState) -> None:
        self.state = state
        super().__init__(("0.0.0.0", 8080), Handler)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *args: Any) -> None:
        del args

    def do_GET(self) -> None:
        suffix = ".lane.bench.test"
        host = self.headers.get("Host", "").split(":", 1)[0]
        state = cast(Server, self.server).state
        routed = state.begin(host[: -len(suffix)] if host.endswith(suffix) else "", self.path)
        if routed is None:
            self.send_error(404)
            return
        try:
            state.barrier(routed)
            time.sleep(routed[1]["delay_ms"] / 1000)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=us-ascii")
            self.send_header("Content-Length", str(len(routed[2])))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(routed[2])
            self.wfile.flush()
            state.finish(routed)
        except Exception:
            with state.condition:
                state.unexpected += 1
            raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--concurrency", type=int, choices=(1, 4), required=True)
    args = parser.parse_args()
    state = FixtureState(load_workload(args.workload), args.concurrency)
    server = Server(state)
    thread = threading.Thread(target=server.serve_forever, name="b0-fixture")
    thread.start()
    Path("/tmp/ready").touch(mode=0o600)
    if not state.done.wait(timeout=300):
        state.unexpected += 1
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)
    report = state.report()
    print("ADMISSION_FIXTURE=" + json.dumps(report, sort_keys=True, separators=(",", ":")))
    return int(report["requests"] != state.expected_count or report["unexpected"] != 0)


if __name__ == "__main__":
    raise SystemExit(main())
