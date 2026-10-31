"""Private-network upstream inputs and a bounded S3 PUT sink for both arms."""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import ssl
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from admission.native_lane.workload import (
    ORIGINS,
    PROFILES,
    detail_body,
    jobs,
    listing_body,
    workload_sha256,
)


class FixtureState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests: Counter[str] = Counter()
        self.objects: dict[str, bytes] = {}
        self.unexpected = 0
        self.active = self.peak_active = 0
        self.details = {(row["origin"], row["path"]): row for row in jobs()}

    def response(self, host: str, path: str) -> tuple[bytes, str, int] | None:
        if host not in ORIGINS:
            return None
        if path == "/listing":
            profile = PROFILES[ORIGINS.index(host)]
            return (
                listing_body(profile),
                "application/json" if profile == "api_rich" else "text/html",
                0,
            )
        row = self.details.get((host, path))
        if row is None:
            return None
        return detail_body(row), "text/html", row["delay_ms"]

    def report(self) -> dict[str, Any]:
        with self.lock:
            return {
                "workload_sha256": workload_sha256(),
                "requests": dict(sorted(self.requests.items())),
                "unexpected_requests": self.unexpected,
                "peak_upstream_inflight": self.peak_active,
                "uploaded_objects": {
                    key: hashlib.sha256(value).hexdigest()
                    for key, value in sorted(self.objects.items())
                },
            }


def handler(state: FixtureState, *, storage: bool) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: Any) -> None:
            pass

        def send(
            self, body: bytes, content_type: str = "application/json", status: int = 200
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if storage and path == "/report":
                self.send(json.dumps(state.report()).encode())
                return
            if storage and path.startswith("/descriptions/"):
                with state.lock:
                    body = state.objects.get(unquote(path))
                self.send(body or b"", "text/html", 200 if body is not None else 404)
                return
            if storage and path == "/health":
                self.send(b"{}")
                return
            value = state.response(self.headers.get("Host", "").split(":", 1)[0], path)
            if value is None:
                with state.lock:
                    state.unexpected += 1
                self.send(b"{}", status=404)
                return
            body, content_type, delay_ms = value
            with state.lock:
                state.requests[self.headers["Host"].split(":", 1)[0] + path] += 1
                state.active += 1
                state.peak_active = max(state.peak_active, state.active)
            try:
                time.sleep(delay_ms / 1000)
                self.send(body, content_type)
            finally:
                with state.lock:
                    state.active -= 1

        def do_PUT(self) -> None:
            path = unquote(urlsplit(self.path).path)
            try:
                size = int(self.headers.get("Content-Length", "-1"))
            except ValueError:
                size = -1
            if (
                not storage
                or not path.startswith("/descriptions/job/")
                or not 0 < size <= 8 * 1024 * 1024
            ):
                self.send(b"{}", status=400)
                return
            body = self.rfile.read(size)
            if len(body) != size:
                self.send(b"{}", status=400)
                return
            with state.lock:
                state.objects[path] = body
            self.send(b"{}")

    return Handler


def tls_server(
    address: tuple[str, int],
    state: FixtureState,
    certificate: Path,
    private_key: Path,
    *,
    storage: bool,
) -> http.server.ThreadingHTTPServer:
    service = http.server.ThreadingHTTPServer(address, handler(state, storage=storage))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, private_key)
    service.socket = context.wrap_socket(service.socket, server_side=True)
    return service


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--certificate", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    args = parser.parse_args()
    state = FixtureState()
    upstream = tls_server(
        ("0.0.0.0", 443), state, args.certificate, args.private_key, storage=False
    )
    sink = tls_server(("0.0.0.0", 8080), state, args.certificate, args.private_key, storage=True)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    Path("/tmp/ready").touch()
    sink.serve_forever()


if __name__ == "__main__":
    main()
