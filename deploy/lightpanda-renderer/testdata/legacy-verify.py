#!/usr/bin/env python3
"""CI fixture for the exact internal-only predecessor deployed by b79b5b0e8."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
from pathlib import Path
from typing import Any

CONTAINER = "jobseek-lightpanda-renderer"
NETWORK = "jobseek-lightpanda-renderer"


def run_json(arguments: list[str]) -> Any:
    result = subprocess.run(arguments, check=True, capture_output=True, text=True, timeout=30)
    return json.loads(result.stdout)


def read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        result[key] = value
    return result


def verify_running(environment: Path, expected_id: str) -> str:
    env = read_env(environment)
    release_id = env["RELEASE_ID"]
    release_dir = env["RENDERER_RELEASE_DIR"]
    if not re.fullmatch(r"sha-[0-9a-f]{40}-ci-r[0-9]+a[0-9]+", release_id):
        raise RuntimeError("legacy release identity drifted")
    if release_dir != f"/home/deploy/.local/share/jobseek-lightpanda/releases/{release_id}":
        raise RuntimeError("legacy release path drifted")
    inspects = run_json(["docker", "inspect", CONTAINER])
    if len(inspects) != 1:
        raise RuntimeError("legacy renderer identity is not exact")
    inspect = inspects[0]
    labels = inspect.get("Config", {}).get("Labels") or {}
    networks = inspect.get("NetworkSettings", {}).get("Networks") or {}
    if (
        inspect.get("Id") != expected_id
        or inspect.get("State", {}).get("Running") is not True
        or inspect.get("State", {}).get("OOMKilled") is not False
        or labels.get("com.docker.compose.project") != "jobseek-lightpanda"
        or labels.get("com.docker.compose.service") != "renderer"
        or labels.get("org.jobseek.lightpanda.release") != release_id
        or labels.get("org.jobseek.lightpanda.mode") != "dormant-no-egress"
        or set(networks) != {NETWORK}
        or networks[NETWORK].get("IPAddress") != "172.30.94.2"
        or (inspect.get("HostConfig") or {}).get("NetworkMode") != NETWORK
        or (inspect.get("HostConfig") or {}).get("PortBindings") not in (None, {})
    ):
        raise RuntimeError("legacy renderer runtime drifted")
    network_inspects = run_json(["docker", "network", "inspect", NETWORK])
    if len(network_inspects) != 1:
        raise RuntimeError("legacy network identity is not exact")
    network = network_inspects[0]
    endpoints = network.get("Containers") or {}
    prefix = ipaddress.ip_network("172.30.94.0/29").prefixlen
    endpoint = endpoints.get(expected_id) or {}
    if (
        network.get("Internal") is not True
        or network.get("EnableIPv6") is not False
        or endpoint.get("Name") != CONTAINER
        or endpoint.get("IPv4Address") != f"172.30.94.2/{prefix}"
        or set(endpoints) != {expected_id}
    ):
        raise RuntimeError("legacy internal-only boundary drifted")
    routes = subprocess.run(
        ["docker", "exec", "--user", "10001:10001", CONTAINER, "cat", "/proc/net/route"],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout.splitlines()[1:]
    if any(len(line.split()) >= 2 and line.split()[1] == "00000000" for line in routes):
        raise RuntimeError("legacy internal-only renderer gained a default route")
    return expected_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("running",))
    parser.add_argument("environment", type=Path)
    parser.add_argument("--expected-id", required=True)
    args = parser.parse_args()
    print(verify_running(args.environment, args.expected_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
