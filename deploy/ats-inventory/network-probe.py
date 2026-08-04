#!/usr/bin/env python3
"""Build and execute a credential-free ATS runner network boundary probe."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_REQUIRED_ENDPOINTS = (
    {"label": "github-api", "host": "api.github.com", "port": 443},
    {"label": "inventory-source", "host": "storage.stapply.ai", "port": 443},
)


def _read_exact_env(path: Path, key: str) -> str:
    matches = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            matches.append(line.split("=", 1)[1].strip())
    if len(matches) != 1 or not matches[0]:
        raise ValueError(f"{key} must appear exactly once")
    value = matches[0]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def build_endpoints(*, env_file: Path, gateway: str) -> dict[str, Any]:
    gateway_ip = ipaddress.ip_address(gateway)
    if gateway_ip.version != 4 or not any(
        gateway_ip in net for net in _PRIVATE_NETWORKS
    ):
        raise ValueError("ATS bridge gateway must be private IPv4")

    database_url = _read_exact_env(env_file, "LOCAL_DATABASE_URL")
    parsed = urlsplit(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
        raise ValueError("LOCAL_DATABASE_URL is not a PostgreSQL URL")
    database_port = parsed.port or 5432
    resolved = {
        ipaddress.ip_address(item[4][0])
        for item in socket.getaddrinfo(
            parsed.hostname,
            database_port,
            family=socket.AF_INET,
            type=socket.SOCK_STREAM,
        )
    }
    if not resolved:
        raise ValueError("production PostgreSQL hostname has no IPv4 address")
    if any(
        not any(address in net for net in _PRIVATE_NETWORKS) for address in resolved
    ):
        raise ValueError("production PostgreSQL must resolve only to private IPv4")

    blocked = [{"label": "crawler-host", "host": str(gateway_ip), "port": 22}]
    blocked.extend(
        {
            "label": f"production-postgresql-{index}",
            "host": str(address),
            "port": database_port,
        }
        for index, address in enumerate(sorted(resolved, key=int), start=1)
    )
    return {"required": list(_REQUIRED_ENDPOINTS), "blocked": blocked}


def _connect(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        return


def verify_endpoints(
    payload: dict[str, Any], *, timeout: float = 5.0
) -> dict[str, Any]:
    required = payload.get("required")
    blocked = payload.get("blocked")
    if not isinstance(required, list) or not isinstance(blocked, list) or not blocked:
        raise ValueError("network probe endpoint document is invalid")

    required_labels: list[str] = []
    blocked_labels: list[str] = []
    for endpoint in required:
        label, host, port = _validate_endpoint(endpoint)
        try:
            _connect(host, port, timeout)
        except OSError as exc:
            raise RuntimeError(f"required endpoint {label} is unreachable") from exc
        required_labels.append(label)

    for endpoint in blocked:
        label, host, port = _validate_endpoint(endpoint)
        try:
            _connect(host, port, timeout)
        except OSError:
            blocked_labels.append(label)
            continue
        raise RuntimeError(f"blocked endpoint {label} is reachable")

    return {
        "event": "ats_inventory.network_boundary_verified",
        "required": required_labels,
        "blocked": blocked_labels,
    }


def _validate_endpoint(endpoint: object) -> tuple[str, str, int]:
    if not isinstance(endpoint, dict):
        raise ValueError("network probe endpoint must be an object")
    label = endpoint.get("label")
    host = endpoint.get("host")
    port = endpoint.get("port")
    if not isinstance(label, str) or not label or not isinstance(host, str) or not host:
        raise ValueError("network probe endpoint label/host is invalid")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("network probe endpoint port is invalid")
    return label, host, port


def _write_payload(path: Path, payload: dict[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
        handle.write("\n")
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--env-file", type=Path, required=True)
    build.add_argument("--gateway", required=True)
    build.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("path", type=Path)
    args = parser.parse_args()

    try:
        if args.command == "build":
            _write_payload(
                args.output,
                build_endpoints(env_file=args.env_file, gateway=args.gateway),
            )
        else:
            payload = json.loads(args.path.read_text(encoding="utf-8"))
            print(
                json.dumps(
                    verify_endpoints(payload), sort_keys=True, separators=(",", ":")
                )
            )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(
            f"ERROR: ATS network probe {args.command} failed ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
