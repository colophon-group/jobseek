from __future__ import annotations

import html
import ipaddress
import json
import socket
import socketserver
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit

from common import SAFE_ID, scenario_map, validate_fixture_origin


def _urlset(scenario_id: str, start: int, count: int) -> bytes:
    entries = "".join(
        "<url><loc>"
        + html.escape(
            f"https://fixture.invalid/jobs/{scenario_id}/{index:05d}"
            f"?utm_source=benchmark&slot={index % 17}",
            quote=False,
        )
        + "</loc></url>"
        for index in range(start, start + count)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + entries + "</urlset>"
    ).encode("utf-8")


def _sitemap_index(host: str, prefix: str, children: int) -> bytes:
    entries = "".join(
        f"<sitemap><loc>http://{host}{prefix}/jobs-child-{index}.xml</loc></sitemap>"
        for index in range(children)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + entries
        + "</sitemapindex>"
    ).encode("utf-8")


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    scenario: str
    attempt: int
    delay_ms: int
    batch_id: str
    job_id: str
    arm_token: str


class FixtureState:
    def __init__(self, corpus: dict[str, Any], transcript: BinaryIO | None = None) -> None:
        self.scenarios = scenario_map(corpus)
        self.transcript = transcript
        self.lock = threading.Lock()
        self.batches: dict[str, dict[str, Any]] = {}
        self.origin_indexes: dict[str, int] = {}
        self.attempts: dict[tuple[str, str, str], int] = {}
        self.records: list[dict[str, Any]] = []
        self.connections: dict[int, dict[str, Any]] = {}
        self.next_connection_id = 1
        self.active_global = 0
        self.active_by_origin: dict[int, int] = {}
        self.max_active_global_by_arm: dict[str, int] = {}
        self.max_active_origin_by_arm: dict[str, int] = {}
        self.max_open_global_by_arm: dict[str, int] = {}
        self.max_open_origin_by_arm: dict[str, int] = {}
        self.max_idle_global_by_arm: dict[str, int] = {}
        self.max_idle_origin_by_arm: dict[str, int] = {}
        self.active_arm: str | None = None
        self.bodies: dict[tuple[str, int], bytes] = {}
        for scenario in corpus["scenarios"]:
            children = int(scenario["index_children"])
            if children:
                per_child = int(scenario["url_count"]) // children
                for child in range(children):
                    self.bodies[(scenario["id"], child)] = _urlset(
                        scenario["id"], child * per_child, per_child
                    )
            elif scenario["url_count"]:
                self.bodies[(scenario["id"], -1)] = _urlset(
                    scenario["id"], 0, int(scenario["url_count"])
                )

    def configure_origins(self, origins: list[str]) -> None:
        canonical = [validate_fixture_origin(origin) for origin in origins]
        if len(canonical) != len(set(canonical)):
            raise ValueError("fixture origins must be unique")
        with self.lock:
            if self.origin_indexes:
                raise RuntimeError("fixture origins were already configured")
            self.origin_indexes = {origin: index for index, origin in enumerate(canonical)}

    def _write(self, record: dict[str, Any]) -> None:
        self.records.append(record)
        if self.transcript is not None:
            self.transcript.write((json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
            self.transcript.flush()

    def register_batch(
        self,
        *,
        arm_token: str,
        batch_id: str,
        jobs: list[dict[str, Any]],
    ) -> None:
        if SAFE_ID.fullmatch(batch_id) is None or SAFE_ID.fullmatch(arm_token) is None:
            raise ValueError("unsafe fixture batch or arm token")
        job_map: dict[str, dict[str, Any]] = {}
        for job in jobs:
            job_id = str(job["id"])
            if SAFE_ID.fullmatch(job_id) is None or job_id in job_map:
                raise ValueError("unsafe or duplicate fixture job ID")
            if job["scenario"] not in self.scenarios:
                raise ValueError("unknown fixture scenario")
            origin = validate_fixture_origin(str(job["origin"]))
            if origin not in self.origin_indexes:
                raise ValueError("job origin is not a member of this fixture fleet")
            registered = dict(job)
            registered["_fixture_origin_index"] = self.origin_indexes[origin]
            registered["_fixture_host"] = urlsplit(origin).netloc
            job_map[job_id] = registered
        with self.lock:
            if self.active_arm is None:
                # Convenience for direct fixture tests. The benchmark
                # orchestrator activates each arm before its runner starts.
                self.active_arm = arm_token
            elif self.active_arm != arm_token:
                raise RuntimeError("batch does not belong to the active fixture arm")
            self.batches[batch_id] = {"arm_token": arm_token, "jobs": job_map}
            for key in [key for key in self.attempts if key[0] == arm_token and key[1] == batch_id]:
                del self.attempts[key]

    def activate_arm(self, arm_token: str) -> None:
        if SAFE_ID.fullmatch(arm_token) is None:
            raise ValueError("unsafe fixture arm token")
        with self.lock:
            if self.active_arm not in (None, arm_token):
                raise RuntimeError("another fixture arm is still active")
            self.active_arm = arm_token

    def deactivate_arm(self, arm_token: str) -> None:
        with self.lock:
            if self.active_arm != arm_token:
                raise RuntimeError("fixture arm activation changed unexpectedly")
            self.active_arm = None

    def _record_open_idle_maxima(self, arm_token: str) -> None:
        open_by_origin: dict[int, int] = {}
        idle_by_origin: dict[int, int] = {}
        for connection in self.connections.values():
            origin = int(connection["origin_index"])
            open_by_origin[origin] = open_by_origin.get(origin, 0) + 1
            if not connection["active"] and connection["request_count"] > 0:
                idle_by_origin[origin] = idle_by_origin.get(origin, 0) + 1
        self.max_open_global_by_arm[arm_token] = max(
            self.max_open_global_by_arm.get(arm_token, 0), len(self.connections)
        )
        self.max_open_origin_by_arm[arm_token] = max(
            self.max_open_origin_by_arm.get(arm_token, 0), max(open_by_origin.values(), default=0)
        )
        idle = sum(idle_by_origin.values())
        self.max_idle_global_by_arm[arm_token] = max(
            self.max_idle_global_by_arm.get(arm_token, 0), idle
        )
        self.max_idle_origin_by_arm[arm_token] = max(
            self.max_idle_origin_by_arm.get(arm_token, 0), max(idle_by_origin.values(), default=0)
        )

    def open_connection(self, origin_index: int, peer: tuple[str, int]) -> int:
        with self.lock:
            if self.active_arm is None:
                raise RuntimeError("connection opened without an active fixture arm")
            connection_id = self.next_connection_id
            self.next_connection_id += 1
            self.connections[connection_id] = {
                "origin_index": origin_index,
                "peer": peer[0],
                "active": False,
                "last_arm": self.active_arm,
                "request_count": 0,
            }
            self._record_open_idle_maxima(self.active_arm)
            self._write(
                {
                    "type": "connection_open",
                    "at_ns": time.monotonic_ns(),
                    "connection_id": connection_id,
                    "origin_index": origin_index,
                    "arm_token": self.active_arm,
                    "peer_is_loopback": ipaddress.ip_address(peer[0]).is_loopback,
                }
            )
            return connection_id

    def close_connection(self, connection_id: int, reason: str) -> None:
        with self.lock:
            connection = self.connections.pop(connection_id, None)
            if connection is None:
                return
            self._write(
                {
                    "type": "connection_close",
                    "at_ns": time.monotonic_ns(),
                    "connection_id": connection_id,
                    "origin_index": connection["origin_index"],
                    "last_arm": connection["last_arm"],
                    "reason": reason,
                }
            )

    def response_for(self, path: str, host: str, origin_index: int) -> Response | None:
        parts = path.split("/")
        if len(parts) != 6 or parts[1] != "fixture":
            return None
        _, _, batch_id, job_id, resource_group, resource = parts
        if resource_group != "sitemap" or SAFE_ID.fullmatch(batch_id) is None:
            return None
        with self.lock:
            batch = self.batches.get(batch_id)
            if batch is None:
                return None
            job = batch["jobs"].get(job_id)
            if job is None:
                return None
            if job["_fixture_origin_index"] != origin_index or job["_fixture_host"] != host:
                return None
            scenario = self.scenarios[job["scenario"]]
            arm_token = str(batch["arm_token"])
            key = (arm_token, batch_id, job_id)
            if resource == "root.xml":
                attempt = self.attempts.get(key, 0) + 1
                self.attempts[key] = attempt
                statuses = scenario["root_statuses"]
                status = int(statuses[min(attempt - 1, len(statuses) - 1)])
                if status != 200:
                    body = b""
                elif scenario["index_children"]:
                    prefix = f"/fixture/{batch_id}/{job_id}/sitemap"
                    body = _sitemap_index(host, prefix, int(scenario["index_children"]))
                else:
                    body = self.bodies.get((scenario["id"], -1), b"")
            elif resource.startswith("jobs-child-") and resource.endswith(".xml"):
                try:
                    child_index = int(resource.removeprefix("jobs-child-").removesuffix(".xml"))
                except ValueError:
                    return None
                if not 0 <= child_index < int(scenario["index_children"]):
                    return None
                attempt = 1
                status = 200
                body = self.bodies[(scenario["id"], child_index)]
            else:
                return None
        return Response(
            status=status,
            body=body,
            scenario=str(scenario["id"]),
            attempt=attempt,
            delay_ms=int(scenario["response_delay_ms"]),
            batch_id=batch_id,
            job_id=job_id,
            arm_token=arm_token,
        )

    def begin_request(self, connection_id: int, response: Response, origin_index: int) -> int:
        with self.lock:
            if self.active_arm != response.arm_token:
                raise RuntimeError("request does not belong to the active fixture arm")
            connection = self.connections[connection_id]
            connection["active"] = True
            connection["last_arm"] = response.arm_token
            connection["request_count"] += 1
            self._record_open_idle_maxima(response.arm_token)
            self.active_global += 1
            self.active_by_origin[origin_index] = self.active_by_origin.get(origin_index, 0) + 1
            self.max_active_global_by_arm[response.arm_token] = max(
                self.max_active_global_by_arm.get(response.arm_token, 0), self.active_global
            )
            self.max_active_origin_by_arm[response.arm_token] = max(
                self.max_active_origin_by_arm.get(response.arm_token, 0),
                self.active_by_origin[origin_index],
            )
            return int(connection["request_count"])

    def finish_request(
        self,
        *,
        connection_id: int,
        origin_index: int,
        response: Response,
        method: str,
        protocol: str,
        path: str,
        request_wire_bytes: int,
        response_wire_bytes: int,
        request_on_connection: int,
        started_ns: int,
        peer_is_loopback: bool,
    ) -> None:
        finished_ns = time.monotonic_ns()
        with self.lock:
            connection = self.connections.get(connection_id)
            if connection is not None:
                connection["active"] = False
            self.active_global -= 1
            self.active_by_origin[origin_index] -= 1
            self._record_open_idle_maxima(response.arm_token)
            self._write(
                {
                    "type": "request",
                    "arm_token": response.arm_token,
                    "batch_id": response.batch_id,
                    "job_id": response.job_id,
                    "scenario": response.scenario,
                    "origin_index": origin_index,
                    "connection_id": connection_id,
                    "request_on_connection": request_on_connection,
                    "started_ns": started_ns,
                    "finished_ns": finished_ns,
                    "duration_ns": finished_ns - started_ns,
                    "method": method,
                    "protocol": protocol,
                    "path": path,
                    "attempt": response.attempt,
                    "status": response.status,
                    "request_wire_bytes": request_wire_bytes,
                    "response_body_bytes": len(response.body),
                    "response_wire_bytes": response_wire_bytes,
                    "peer_is_loopback": peer_is_loopback,
                }
            )

    def requests_for(self, arm_token: str, batch_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [
                dict(record)
                for record in self.records
                if record.get("type") == "request"
                and record.get("arm_token") == arm_token
                and record.get("batch_id") == batch_id
            ]

    def connection_snapshot(self, arm_token: str) -> dict[str, int]:
        with self.lock:
            relevant = [
                value for value in self.connections.values() if value["last_arm"] == arm_token
            ]
            active = sum(bool(value["active"]) for value in relevant)
            open_by_origin: dict[int, int] = {}
            idle_by_origin: dict[int, int] = {}
            for value in relevant:
                origin = int(value["origin_index"])
                open_by_origin[origin] = open_by_origin.get(origin, 0) + 1
                if not value["active"] and value["request_count"] > 0:
                    idle_by_origin[origin] = idle_by_origin.get(origin, 0) + 1
            idle = sum(idle_by_origin.values())
            return {
                "open": len(relevant),
                "active": active,
                "idle": idle,
                "max_open_per_origin": max(open_by_origin.values(), default=0),
                "max_idle_per_origin": max(idle_by_origin.values(), default=0),
                "max_active_global": self.max_active_global_by_arm.get(arm_token, 0),
                "max_active_per_origin": self.max_active_origin_by_arm.get(arm_token, 0),
                "max_open_global": self.max_open_global_by_arm.get(arm_token, 0),
                "max_open_per_origin_observed": self.max_open_origin_by_arm.get(arm_token, 0),
                "max_idle_global_observed": self.max_idle_global_by_arm.get(arm_token, 0),
                "max_idle_per_origin_observed": self.max_idle_origin_by_arm.get(arm_token, 0),
            }


class _TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[socketserver.BaseRequestHandler],
        *,
        state: FixtureState,
        origin_index: int,
    ) -> None:
        self.state = state
        self.origin_index = origin_index
        super().__init__(address, handler)


class _Handler(socketserver.BaseRequestHandler):
    server: _TCPServer

    def handle(self) -> None:
        self.request.settimeout(5.0)
        connection_id = self.server.state.open_connection(
            self.server.origin_index, self.client_address
        )
        close_reason = "peer_closed"
        try:
            while True:
                raw = bytearray()
                try:
                    while b"\r\n\r\n" not in raw and len(raw) <= 65_536:
                        chunk = self.request.recv(4096)
                        if not chunk:
                            return
                        raw.extend(chunk)
                except TimeoutError:
                    close_reason = "idle_expiry"
                    return
                if len(raw) > 65_536:
                    close_reason = "request_header_limit"
                    return
                request_bytes = bytes(raw)
                first = request_bytes.split(b"\r\n", 1)[0]
                try:
                    method, raw_target, protocol = first.decode("ascii").split(" ")
                    path = urlsplit(raw_target).path
                    header_lines = request_bytes.split(b"\r\n")[1:]
                    host = next(
                        line.split(b":", 1)[1].strip().decode("ascii")
                        for line in header_lines
                        if line.lower().startswith(b"host:")
                    )
                except (StopIteration, UnicodeDecodeError, ValueError):
                    close_reason = "invalid_request"
                    return
                response = (
                    self.server.state.response_for(path, host, self.server.origin_index)
                    if method == "GET"
                    else None
                )
                if response is None:
                    close_reason = "unregistered_request"
                    return
                started_ns = time.monotonic_ns()
                request_number = self.server.state.begin_request(
                    connection_id, response, self.server.origin_index
                )
                time.sleep(response.delay_ms / 1000.0)
                reason = {200: "OK", 500: "Internal Server Error"}[response.status]
                client_close = b"\r\nconnection: close\r\n" in request_bytes.lower()
                connection_header = "close" if client_close else "keep-alive"
                headers = (
                    f"HTTP/1.1 {response.status} {reason}\r\n"
                    "Content-Type: application/xml; charset=utf-8\r\n"
                    f"Content-Length: {len(response.body)}\r\n"
                    f"Connection: {connection_header}\r\n"
                    "\r\n"
                ).encode("ascii")
                wire_response = headers + response.body
                self.request.sendall(wire_response)
                self.server.state.finish_request(
                    connection_id=connection_id,
                    origin_index=self.server.origin_index,
                    response=response,
                    method=method,
                    protocol=protocol,
                    path=path,
                    request_wire_bytes=len(request_bytes),
                    response_wire_bytes=len(wire_response),
                    request_on_connection=request_number,
                    started_ns=started_ns,
                    peer_is_loopback=ipaddress.ip_address(self.client_address[0]).is_loopback,
                )
                if client_close:
                    close_reason = "client_close"
                    return
        finally:
            self.server.state.close_connection(connection_id, close_reason)


class FixtureFleet:
    def __init__(
        self,
        corpus: dict[str, Any],
        *,
        transcript_path: Path | None = None,
        bind_host: str = "127.0.0.1",
    ) -> None:
        if ipaddress.ip_address(bind_host).is_loopback is not True:
            raise ValueError("local fixture fleet must bind only to loopback")
        self._transcript_handle = (
            transcript_path.open("ab", buffering=0) if transcript_path is not None else None
        )
        self.state = FixtureState(corpus, self._transcript_handle)
        self.servers: list[_TCPServer] = []
        self.threads: list[threading.Thread] = []
        for origin_index in range(int(corpus["defaults"]["origin_count"])):
            server = _TCPServer(
                (bind_host, 0), _Handler, state=self.state, origin_index=origin_index
            )
            self.servers.append(server)
        self.state.configure_origins(self.origins)

    @property
    def origins(self) -> list[str]:
        values = [
            validate_fixture_origin(f"http://{server.server_address[0]}:{server.server_address[1]}")
            for server in self.servers
        ]
        if len(values) != len(set(values)):
            raise RuntimeError("fixture fleet did not create distinct origin keys")
        return values

    def start(self) -> None:
        for server in self.servers:
            thread = threading.Thread(
                target=lambda current=server: current.serve_forever(poll_interval=0.05),
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)

    def close(self) -> None:
        for server in self.servers:
            server.shutdown()
        for server in self.servers:
            server.server_close()
        for thread in self.threads:
            thread.join(timeout=5.0)
        if self._transcript_handle is not None:
            self._transcript_handle.close()

    def __enter__(self) -> FixtureFleet:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def raw_http_get(origin: str, path: str) -> bytes:
    """Minimal test helper; benchmark runners use their normal HTTP clients."""
    parsed = urlsplit(validate_fixture_origin(origin))
    request = (
        f"GET {path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    with socket.create_connection((parsed.hostname, parsed.port), timeout=2.0) as sock:
        sock.sendall(request)
        chunks: list[bytes] = []
        while chunk := sock.recv(65_536):
            chunks.append(chunk)
    return b"".join(chunks)
