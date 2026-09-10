#!/usr/bin/env python3
"""Fail-closed verifier for the one-service dormant Lightpanda deployment."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import platform
import re
import socket
import ssl
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT = "jobseek-lightpanda"
SERVICE = "renderer"
CONTAINER = "jobseek-lightpanda-renderer"
NETWORK = "jobseek-lightpanda-renderer"
EXPECTED_INVENTORY: dict[str, object] = {
    "schema_version": 1,
    "host_architecture": "arm64",
    "service_ip": "10.0.0.5",
    "public_ipv4": "178.105.51.62/32",
    "public_ipv6": "2a01:4f8:1c18:d64c::/64",
    "private_ipv4": "10.0.0.5/32",
    "project_network": "10.0.0.0/16",
    "default_docker_network": "172.17.0.0/16",
    "provider_gateway": "172.31.1.1/32",
    "renderer_network_name": NETWORK,
    "renderer_network": "172.30.94.0/29",
    "renderer_address": "172.30.94.2",
}
PROTECTED = {
    "deploy-murmur-1": "murmur",
    "deploy-cloudflared-1": "cloudflared",
}


class VerificationError(RuntimeError):
    pass


def fail(message: str) -> None:
    raise VerificationError(message)


def run_json(arguments: list[str]) -> Any:
    result = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def run_text(arguments: list[str]) -> str:
    result = subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def load_inventory(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload != EXPECTED_INVENTORY:
        fail("observed deployment inventory is not the reviewed exact registry")
    for key in (
        "public_ipv4",
        "public_ipv6",
        "private_ipv4",
        "project_network",
        "default_docker_network",
        "provider_gateway",
        "renderer_network",
    ):
        value = str(payload[key])
        if str(ipaddress.ip_network(value, strict=True)) != value:
            fail("deployment inventory contains a noncanonical prefix")
    renderer_address = str(payload["renderer_address"])
    renderer_network = ipaddress.ip_network(str(payload["renderer_network"]))
    if ipaddress.ip_address(renderer_address) not in renderer_network:
        fail("renderer address is outside its dedicated network")
    return payload


def canonical_mount(mount: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": mount.get("Type"),
        "name": mount.get("Name", ""),
        "source": mount.get("Source", ""),
        "destination": mount.get("Destination"),
        "driver": mount.get("Driver", ""),
        "mode": mount.get("Mode", ""),
        "rw": mount.get("RW"),
        "propagation": mount.get("Propagation", ""),
    }


def object_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def canonical_network(name: str, network: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "network_id": network.get("NetworkID"),
        "endpoint_id": network.get("EndpointID"),
        "gateway": network.get("Gateway", ""),
        "ip_address": network.get("IPAddress", ""),
        "global_ipv6_address": network.get("GlobalIPv6Address", ""),
        "mac_address": network.get("MacAddress", ""),
        "aliases": sorted(network.get("Aliases") or []),
    }


def protected_snapshot_from_inspects(inspects: list[dict[str, Any]]) -> dict[str, Any]:
    if len(inspects) != len(PROTECTED):
        fail("protected container inventory is incomplete")
    snapshot: dict[str, Any] = {"schema_version": 1, "containers": {}}
    for inspect in inspects:
        name = str(inspect.get("Name", "")).removeprefix("/")
        expected_service = PROTECTED.get(name)
        if expected_service is None or name in snapshot["containers"]:
            fail("protected container identity is unexpected or duplicated")
        labels = inspect.get("Config", {}).get("Labels") or {}
        if labels.get("com.docker.compose.project") != "deploy":
            fail("protected container project identity drifted")
        if labels.get("com.docker.compose.service") != expected_service:
            fail("protected container service identity drifted")
        state = inspect.get("State") or {}
        if (
            state.get("Running") is not False
            or state.get("Status") != "exited"
            or state.get("OOMKilled") is not False
        ):
            fail("paused protected container is not exactly stopped")
        networks = inspect.get("NetworkSettings", {}).get("Networks") or {}
        snapshot["containers"][name] = {
            "id": inspect.get("Id"),
            "image_id": inspect.get("Image"),
            "config_image": inspect.get("Config", {}).get("Image"),
            "created": inspect.get("Created"),
            "started_at": state.get("StartedAt"),
            "running": state.get("Running"),
            "status": state.get("Status"),
            "exit_code": state.get("ExitCode"),
            "oom_killed": state.get("OOMKilled"),
            "restart_count": inspect.get("RestartCount"),
            "finished_at": state.get("FinishedAt"),
            "network_mode": inspect.get("HostConfig", {}).get("NetworkMode"),
            "restart_policy": inspect.get("HostConfig", {}).get("RestartPolicy"),
            "config_sha256": object_sha256(inspect.get("Config") or {}),
            "host_config_sha256": object_sha256(inspect.get("HostConfig") or {}),
            "compose_project": labels.get("com.docker.compose.project"),
            "compose_service": labels.get("com.docker.compose.service"),
            "networks": [
                canonical_network(network_name, networks[network_name])
                for network_name in sorted(networks)
            ],
            "mounts": sorted(
                (canonical_mount(mount) for mount in inspect.get("Mounts") or []),
                key=lambda item: (str(item["destination"]), str(item["source"])),
            ),
        }
    if set(snapshot["containers"]) != set(PROTECTED):
        fail("protected container inventory is not exact")
    for container in snapshot["containers"].values():
        if container["restart_policy"] != {
            "Name": "unless-stopped",
            "MaximumRetryCount": 0,
        }:
            fail("paused protected container restart policy drifted")
    return snapshot


def snapshot_protected() -> dict[str, Any]:
    return protected_snapshot_from_inspects(run_json(["docker", "inspect", *sorted(PROTECTED)]))


def assert_protected(expected_path: Path) -> None:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if snapshot_protected() != expected:
        fail("protected Murmur containers changed during renderer transaction")


def all_networks() -> list[dict[str, Any]]:
    ids = run_text(["docker", "network", "ls", "--quiet"]).split()
    return [] if not ids else run_json(["docker", "network", "inspect", *ids])


def reconcile_host(inventory: dict[str, object], *, renderer_exists: bool) -> None:
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        fail("Murmur host is not ARM64")

    addresses = run_json(["ip", "-j", "address", "show"])
    observed_addresses = {
        f"{info['local']}/{info['prefixlen']}"
        for link in addresses
        for info in link.get("addr_info", [])
        if "local" in info and "prefixlen" in info
    }
    for key in ("public_ipv4", "public_ipv6", "private_ipv4"):
        if inventory[key] not in observed_addresses:
            fail("live host address inventory drifted")

    routes = run_json(["ip", "-j", "route", "show", "table", "all"])
    if not any(route.get("dst") == inventory["project_network"] for route in routes):
        fail("live project route inventory drifted")
    provider_gateway = str(ipaddress.ip_network(str(inventory["provider_gateway"])).network_address)
    if not any(route.get("gateway") == provider_gateway for route in routes):
        fail("live provider gateway inventory drifted")

    networks = all_networks()
    bridge = [network for network in networks if network.get("Name") == "bridge"]
    if len(bridge) != 1:
        fail("default Docker bridge identity is not exact")
    bridge_subnets = {
        config.get("Subnet") for config in (bridge[0].get("IPAM", {}).get("Config") or [])
    }
    if bridge_subnets != {inventory["default_docker_network"]}:
        fail("default Docker bridge prefix drifted")

    candidate = ipaddress.ip_network(str(inventory["renderer_network"]))
    named_renderer = [network for network in networks if network.get("Name") == NETWORK]
    renderer_device = ""
    if named_renderer:
        if len(named_renderer) != 1:
            fail("renderer network identity is duplicated")
        renderer = named_renderer[0]
        network_id = str(renderer.get("Id", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", network_id):
            fail("renderer network content ID drifted")
        renderer_device = f"br-{network_id[:12]}"
        subnets = {
            config.get("Subnet") for config in (renderer.get("IPAM", {}).get("Config") or [])
        }
        labels = renderer.get("Labels") or {}
        if (
            renderer.get("Internal") is not True
            or renderer.get("EnableIPv6") is not False
            or renderer.get("Attachable") is not False
            or subnets != {str(candidate)}
            or labels.get("com.docker.compose.project") != PROJECT
            or labels.get("com.docker.compose.network") != SERVICE
        ):
            fail("existing renderer network drifted from its no-egress contract")
        endpoints = renderer.get("Containers") or {}
        if renderer_exists:
            if len(endpoints) != 1:
                fail("renderer network endpoint inventory drifted")
            endpoint = next(iter(endpoints.values()))
            if endpoint.get("Name") != CONTAINER or endpoint.get("IPv4Address") != "172.30.94.2/29":
                fail("renderer network endpoint identity drifted")
        elif endpoints:
            fail("unused renderer network retained an endpoint")
    for network in networks:
        if network.get("Name") == NETWORK:
            continue
        for config in network.get("IPAM", {}).get("Config") or []:
            subnet = config.get("Subnet")
            if subnet and ipaddress.ip_network(subnet).overlaps(candidate):
                fail("renderer bridge overlaps an existing Docker network")
    expected_renderer_routes = {
        ("unicast", "172.30.94.0/29", renderer_device, "link", "main"),
        ("local", "172.30.94.1", renderer_device, "host", "local"),
        ("broadcast", "172.30.94.7", renderer_device, "link", "local"),
    }
    observed_renderer_routes: set[tuple[str, str, str, str, str]] = set()
    for route in routes:
        prefix = route.get("dst")
        if not prefix or prefix == "default":
            continue
        try:
            route_network = ipaddress.ip_network(prefix)
        except ValueError:
            fail("host route inventory contains a noncanonical prefix")
        if route_network.version != candidate.version or not route_network.overlaps(candidate):
            continue
        route_identity = (
            str(route.get("type", "unicast")),
            str(prefix),
            str(route.get("dev", "")),
            str(route.get("scope", "")),
            str(route.get("table", "main")),
        )
        if renderer_exists and route_identity in expected_renderer_routes:
            observed_renderer_routes.add(route_identity)
            continue
        fail("renderer bridge overlaps an existing host route")
    if renderer_exists and observed_renderer_routes != expected_renderer_routes:
        fail("renderer bridge route inventory drifted")

    if not renderer_exists:
        available_kib = None
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                available_kib = int(line.split()[1])
                break
        if available_kib is None or available_kib * 1024 < 1610612736:
            fail("less than 1.5 GiB is available for the first renderer start")


def expected_service_command(env: dict[str, str]) -> list[str]:
    deny_values = [
        "178.105.51.62/32",
        "2a01:4f8:1c18:d64c::/64",
        "10.0.0.5/32",
        "10.0.0.0/16",
        "172.17.0.0/16",
        "172.31.1.1/32",
        "172.30.94.0/29",
    ]
    return [
        "--listen",
        "0.0.0.0:9443",
        "--service-ip",
        "10.0.0.5",
        *sum((["--deployment-deny-cidr", value] for value in deny_values), []),
        "--tls-cert",
        "/run/credentials/server.pem",
        "--tls-key",
        "/run/credentials/server-key.pem",
        "--tls-ca",
        "/run/credentials/ca.pem",
        "--tls-ca-sha256",
        env["CA_DER_SHA256"],
        "--client-leaf-sha256",
        env["CLIENT_LEAF_SHA256"],
        "--client-spki-sha256",
        env["CLIENT_SPKI_SHA256"],
    ]


def validate_compose_model(model: dict[str, Any], env: dict[str, str]) -> None:
    if set(model) != {"name", "services", "networks"}:
        fail("Compose model top-level keys drifted")
    if model.get("name") != PROJECT or set(model.get("services") or {}) != {SERVICE}:
        fail("Compose project contains anything except the renderer")
    service = model["services"][SERVICE]
    expected_service_keys = {
        "cap_drop",
        "cgroup",
        "command",
        "container_name",
        "cpus",
        "entrypoint",
        "image",
        "init",
        "labels",
        "mem_limit",
        "memswap_limit",
        "networks",
        "pids_limit",
        "platform",
        "pull_policy",
        "read_only",
        "restart",
        "security_opt",
        "stop_grace_period",
        "tmpfs",
        "ulimits",
        "user",
        "volumes",
    }
    if set(service) != expected_service_keys:
        fail("Compose renderer keys are not the exact rendered allowlist")
    expected_ref = env["RENDERER_IMAGE_REF"]
    if service.get("image") != expected_ref or "@sha256:" not in expected_ref:
        fail("Compose image is not the expected immutable digest")
    exact = {
        "container_name": CONTAINER,
        "user": "10001:10001",
        "read_only": True,
        "init": True,
        "mem_limit": "1073741824",
        "memswap_limit": "1073741824",
        "pids_limit": 64,
        "restart": "unless-stopped",
        "cgroup": "private",
    }
    for key, value in exact.items():
        if service.get(key) != value:
            fail(f"Compose renderer {key} drifted")
    if float(service.get("cpus", 0)) != 1.0:
        fail("Compose renderer CPU ceiling drifted")
    if service.get("platform") != "linux/arm64":
        fail("Compose renderer platform drifted")
    if service.get("entrypoint") not in (None, []):
        fail("Compose must retain the image service entrypoint")
    if service.get("pull_policy") != "never":
        fail("Compose renderer pull authority drifted")
    expected_labels = {
        "org.jobseek.lightpanda.source-commit": env["SOURCE_COMMIT"],
        "org.jobseek.lightpanda.image-ref": env["RENDERER_IMAGE_REF"],
        "org.jobseek.lightpanda.release": env["RELEASE_ID"],
        "org.jobseek.lightpanda.mode": "dormant-no-egress",
    }
    if service.get("labels") != expected_labels:
        fail("Compose renderer labels drifted")
    if service.get("cap_drop") != ["ALL"]:
        fail("Compose capability contract drifted")
    if service.get("security_opt") != ["no-new-privileges:true"]:
        fail("Compose privilege contract drifted")
    if service.get("stop_grace_period") != "30s":
        fail("Compose stop grace drifted")
    ulimit = (service.get("ulimits") or {}).get("nofile") or {}
    if ulimit != {"soft": 256, "hard": 256}:
        fail("Compose nofile contract drifted")
    tmpfs = service.get("tmpfs") or []
    if len(tmpfs) != 1 or not str(tmpfs[0]).startswith("/tmp:"):
        fail("Compose tmpfs contract drifted")
    tmpfs_options = set(str(tmpfs[0]).split(":", 1)[1].split(","))
    exact_tmpfs_options = {
        "rw",
        "noexec",
        "nosuid",
        "nodev",
        "size=16m",
        "uid=10001",
        "gid=10001",
        "mode=0700",
    }
    exact_tmpfs_bytes = (exact_tmpfs_options - {"size=16m"}) | {"size=16777216"}
    if frozenset(tmpfs_options) not in {
        frozenset(exact_tmpfs_options),
        frozenset(exact_tmpfs_bytes),
    }:
        fail("Compose tmpfs options drifted")

    volumes = service.get("volumes") or []
    if len(volumes) != 3:
        fail("Compose credential mounts are not exact")
    expected_targets = {
        "/run/credentials/ca.pem",
        "/run/credentials/server.pem",
        "/run/credentials/server-key.pem",
    }
    if {volume.get("target") for volume in volumes} != expected_targets:
        fail("Compose credential mount targets drifted")
    if any(
        volume.get("type") != "bind" or volume.get("read_only") is not True for volume in volumes
    ):
        fail("Compose credentials must be read-only bind mounts")
    release_dir = env["RENDERER_RELEASE_DIR"]
    if not re.fullmatch(
        r"/home/deploy/\.local/share/jobseek-lightpanda/releases/[a-z0-9.-]+",
        release_dir,
    ):
        fail("release directory is outside the dedicated deploy-owned root")
    expected_sources = {
        f"{release_dir}/pki/ca.pem",
        f"{release_dir}/pki/server.pem",
        f"{release_dir}/pki/server-key.pem",
    }
    if {str(volume.get("source", "")) for volume in volumes} != expected_sources:
        fail("Compose mount escaped its exact release generation")
    expected_volume_keys = {"type", "source", "target", "read_only", "bind"}
    if any(set(volume) != expected_volume_keys or volume.get("bind") != {} for volume in volumes):
        fail("rendered Compose credential mount keys drifted")

    if service.get("networks") != {SERVICE: {"ipv4_address": "172.30.94.2"}}:
        fail("renderer is attached to an unexpected network")
    networks = model.get("networks") or {}
    if set(networks) != {SERVICE}:
        fail("Compose declares an unexpected network")
    network = networks[SERVICE]
    if set(network) != {"name", "driver", "ipam", "internal", "enable_ipv6"}:
        fail("renderer bridge keys drifted")
    if (
        network.get("name") != NETWORK
        or network.get("driver") != "bridge"
        or network.get("internal") is not True
        or network.get("enable_ipv6") is not False
    ):
        fail("renderer bridge is not exact and internal")
    ipam = network.get("ipam") or {}
    if set(ipam) != {"config"} or ipam.get("config") != [{"subnet": "172.30.94.0/29"}]:
        fail("renderer bridge prefix drifted")

    command = service.get("command") or []
    deny_values = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--deployment-deny-cidr"
    ]
    if deny_values != [
        "178.105.51.62/32",
        "2a01:4f8:1c18:d64c::/64",
        "10.0.0.5/32",
        "10.0.0.0/16",
        "172.17.0.0/16",
        "172.31.1.1/32",
        "172.30.94.0/29",
    ]:
        fail("renderer startup deny inventory drifted")
    if command != expected_service_command(env):
        fail("renderer startup command drifted")


def read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            fail("release environment has an invalid record")
        key, value = line.split("=", 1)
        if key in result or not key or not value:
            fail("release environment is duplicate or incomplete")
        result[key] = value
    required = {
        "RENDERER_IMAGE_REF",
        "RENDERER_RELEASE_DIR",
        "SOURCE_COMMIT",
        "RELEASE_ID",
        "CA_DER_SHA256",
        "SERVER_LEAF_SHA256",
        "SERVER_SPKI_SHA256",
        "CLIENT_LEAF_SHA256",
        "CLIENT_SPKI_SHA256",
    }
    if set(result) != required:
        fail("release environment keys are not exact")
    return result


def verify_compose(compose: Path, environment: Path) -> None:
    env = read_env(environment)
    model = run_json(
        [
            "docker",
            "compose",
            "--project-name",
            PROJECT,
            "--env-file",
            str(environment),
            "--file",
            str(compose),
            "config",
            "--format",
            "json",
        ]
    )
    validate_compose_model(model, env)


def verify_image(image_ref: str, source_commit: str) -> dict[str, Any]:
    images = run_json(["docker", "image", "inspect", image_ref])
    if len(images) != 1:
        fail("renderer image identity is not exact")
    image = images[0]
    if image.get("Architecture") != "arm64":
        fail("renderer image is not ARM64")
    if image_ref not in (image.get("RepoDigests") or []):
        fail("renderer image does not carry the requested immutable digest")
    labels = image.get("Config", {}).get("Labels") or {}
    if labels.get("org.jobseek.lightpanda.source-commit") != source_commit:
        fail("renderer image source label drifted")
    if image.get("Config", {}).get("ExposedPorts") not in (None, {}):
        fail("dormant renderer image exposes a port")
    return image


def verify_cleanup_network(network_id: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", network_id):
        fail("candidate renderer network ID is invalid")
    networks = run_json(["docker", "network", "inspect", network_id])
    if len(networks) != 1:
        fail("candidate renderer network identity is not exact")
    network = networks[0]
    labels = network.get("Labels") or {}
    configs = network.get("IPAM", {}).get("Config") or []
    if (
        network.get("Id") != network_id
        or network.get("Name") != NETWORK
        or network.get("Internal") is not True
        or network.get("EnableIPv6") is not False
        or network.get("Attachable") is not False
        or network.get("Containers") not in (None, {})
        or network.get("Options") not in (None, {})
        or labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.network") != SERVICE
        or [config.get("Subnet") for config in configs] != ["172.30.94.0/29"]
    ):
        fail("candidate renderer network is unsafe to remove")


def validate_running_inspect(
    inspect: dict[str, Any],
    *,
    image_ref: str,
    image_id: str,
    source_commit: str,
    release_dir: str,
    release_id: str,
) -> None:
    name = str(inspect.get("Name", "")).removeprefix("/")
    if name != CONTAINER or inspect.get("Image") != image_id:
        fail("running renderer image/container identity drifted")
    state = inspect.get("State") or {}
    if (
        state.get("Running") is not True
        or state.get("OOMKilled") is not False
        or state.get("ExitCode") != 0
    ):
        fail("renderer is not stably running")
    config = inspect.get("Config") or {}
    labels = config.get("Labels") or {}
    expected_labels = {
        "com.docker.compose.project": PROJECT,
        "com.docker.compose.service": SERVICE,
        "org.jobseek.lightpanda.source-commit": source_commit,
        "org.jobseek.lightpanda.image-ref": image_ref,
        "org.jobseek.lightpanda.release": release_id,
        "org.jobseek.lightpanda.mode": "dormant-no-egress",
    }
    if any(labels.get(key) != value for key, value in expected_labels.items()):
        fail("renderer labels drifted")
    if config.get("Image") != image_ref or config.get("User") != "10001:10001":
        fail("renderer image or user drifted")
    if config.get("Entrypoint") != [
        "/usr/local/bin/go-lightpanda",
        "--runtime-v1-service",
    ]:
        fail("renderer entrypoint drifted")
    runtime_env = {
        "CA_DER_SHA256": "",
        "CLIENT_LEAF_SHA256": "",
        "CLIENT_SPKI_SHA256": "",
    }
    command = config.get("Cmd") or []
    for flag, key in (
        ("--tls-ca-sha256", "CA_DER_SHA256"),
        ("--client-leaf-sha256", "CLIENT_LEAF_SHA256"),
        ("--client-spki-sha256", "CLIENT_SPKI_SHA256"),
    ):
        if flag not in command or command.index(flag) + 1 >= len(command):
            fail("renderer runtime command pins are incomplete")
        runtime_env[key] = str(command[command.index(flag) + 1])
    if command != expected_service_command(runtime_env):
        fail("renderer runtime command drifted")
    if config.get("ExposedPorts") not in (None, {}):
        fail("dormant renderer image/container exposes a port")
    host = inspect.get("HostConfig") or {}
    if (
        host.get("ReadonlyRootfs") is not True
        or host.get("CapDrop") != ["ALL"]
        or host.get("SecurityOpt") != ["no-new-privileges:true"]
        or host.get("Memory") != 1073741824
        or host.get("MemorySwap") != 1073741824
        or host.get("NanoCpus") != 1_000_000_000
        or host.get("PidsLimit") != 64
        or host.get("Init") is not True
        or host.get("NetworkMode") != NETWORK
        or host.get("PublishAllPorts") is not False
    ):
        fail("renderer containment drifted")
    if host.get("RestartPolicy") != {"Name": "unless-stopped", "MaximumRetryCount": 0}:
        fail("renderer restart policy drifted")
    if host.get("CgroupnsMode") != "private":
        fail("renderer cgroup namespace drifted")
    if host.get("PortBindings") not in (None, {}):
        fail("dormant renderer published a host port")
    ulimits = host.get("Ulimits") or []
    if ulimits != [{"Name": "nofile", "Soft": 256, "Hard": 256}]:
        fail("renderer nofile ceiling drifted")
    tmpfs = set((host.get("Tmpfs") or {}).get("/tmp", "").split(","))
    exact_tmpfs_options = {
        "rw",
        "noexec",
        "nosuid",
        "nodev",
        "size=16m",
        "uid=10001",
        "gid=10001",
        "mode=0700",
    }
    exact_tmpfs_bytes = (exact_tmpfs_options - {"size=16m"}) | {"size=16777216"}
    if frozenset(tmpfs) not in {
        frozenset(exact_tmpfs_options),
        frozenset(exact_tmpfs_bytes),
    }:
        fail("renderer tmpfs drifted")

    mounts = inspect.get("Mounts") or []
    if len(mounts) != 3 or any(
        mount.get("Type") != "bind" or mount.get("RW") is not False for mount in mounts
    ):
        fail("renderer mounts are not the three read-only credentials")
    targets = {
        "/run/credentials/ca.pem": "ca.pem",
        "/run/credentials/server.pem": "server.pem",
        "/run/credentials/server-key.pem": "server-key.pem",
    }
    for mount in mounts:
        destination = mount.get("Destination")
        if (
            destination not in targets
            or mount.get("Source") != f"{release_dir}/pki/{targets[destination]}"
        ):
            fail("renderer credential source escaped its release")

    networks = inspect.get("NetworkSettings", {}).get("Networks") or {}
    if inspect.get("NetworkSettings", {}).get("Ports") not in (None, {}):
        fail("dormant renderer has runtime port publication state")
    if set(networks) != {NETWORK} or networks[NETWORK].get("IPAddress") != "172.30.94.2":
        fail("renderer network attachment drifted")


def verify_running_with_image(environment: Path, *, image_id: str, expected_id: str | None) -> str:
    env = read_env(environment)
    inspects = run_json(["docker", "inspect", CONTAINER])
    if len(inspects) != 1:
        fail("renderer container identity is not exact")
    inspect = inspects[0]
    if expected_id is not None and inspect.get("Id") != expected_id:
        fail("renderer container changed during stability window")
    validate_running_inspect(
        inspect,
        image_ref=env["RENDERER_IMAGE_REF"],
        image_id=image_id,
        source_commit=env["SOURCE_COMMIT"],
        release_dir=env["RENDERER_RELEASE_DIR"],
        release_id=env["RELEASE_ID"],
    )
    networks = run_json(["docker", "network", "inspect", NETWORK])
    if len(networks) != 1:
        fail("renderer network identity is not exact")
    network = networks[0]
    configs = network.get("IPAM", {}).get("Config") or []
    if (
        network.get("Internal") is not True
        or network.get("EnableIPv6") is not False
        or network.get("Attachable") is not False
        or [config.get("Subnet") for config in configs] != ["172.30.94.0/29"]
    ):
        fail("renderer does not have authoritative no-origin-egress networking")
    if network.get("Options") not in (None, {}):
        fail("renderer network has unreviewed driver options")
    network_labels = network.get("Labels") or {}
    if (
        network_labels.get("com.docker.compose.project") != PROJECT
        or network_labels.get("com.docker.compose.network") != SERVICE
    ):
        fail("renderer network labels drifted")
    endpoints = network.get("Containers") or {}
    if set(endpoints) != {inspect["Id"]}:
        fail("renderer network does not have its sole exact endpoint")
    endpoint = endpoints[inspect["Id"]]
    if endpoint.get("Name") != CONTAINER or endpoint.get("IPv4Address") != "172.30.94.2/29":
        fail("renderer network endpoint drifted")
    memory_max = run_text(["docker", "exec", CONTAINER, "cat", "/sys/fs/cgroup/memory.max"]).strip()
    memory_swap_max = run_text(
        ["docker", "exec", CONTAINER, "cat", "/sys/fs/cgroup/memory.swap.max"]
    ).strip()
    if memory_max != "1073741824" or memory_swap_max != "0":
        fail("renderer cgroup memory attestation drifted")
    ipv4_routes = run_text(["docker", "exec", CONTAINER, "cat", "/proc/net/route"]).splitlines()[1:]
    if any(len(line.split()) >= 2 and line.split()[1] == "00000000" for line in ipv4_routes):
        fail("internal renderer network unexpectedly has an IPv4 default route")
    ipv6_routes = run_text(
        ["docker", "exec", CONTAINER, "cat", "/proc/net/ipv6_route"]
    ).splitlines()
    if any(
        len(line.split()) >= 2 and line.split()[0] == "0" * 32 and line.split()[1] == "00"
        for line in ipv6_routes
    ):
        fail("internal renderer network unexpectedly has an IPv6 default route")
    try:
        with socket.create_connection(("172.30.94.2", 9443), timeout=3):
            pass
    except OSError as error:
        raise VerificationError("renderer bridge listener is unreachable") from error
    context = ssl.create_default_context(cafile=env["RENDERER_RELEASE_DIR"] + "/pki/ca.pem")
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["jobseek-lightpanda-b0/1"])
    try:
        with (
            socket.create_connection(("172.30.94.2", 9443), timeout=3) as raw,
            context.wrap_socket(raw, server_hostname="10.0.0.5"),
        ):
            fail("renderer accepted TLS without the required client certificate")
    except ssl.SSLError as error:
        message = str(error).lower()
        if "tlsv13 alert certificate required" not in message:
            raise VerificationError(
                "renderer TLS rejection was not certificate_required"
            ) from error
    return str(inspect["Id"])


def verify_running(environment: Path, *, expected_id: str | None) -> str:
    env = read_env(environment)
    image = verify_image(env["RENDERER_IMAGE_REF"], env["SOURCE_COMMIT"])
    return verify_running_with_image(
        environment, image_id=str(image["Id"]), expected_id=expected_id
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("path", type=Path)
    inventory.add_argument("--renderer-exists", action="store_true")
    snapshot = sub.add_parser("snapshot-protected")
    snapshot.add_argument("output", type=Path)
    protected = sub.add_parser("assert-protected")
    protected.add_argument("expected", type=Path)
    compose = sub.add_parser("compose")
    compose.add_argument("compose", type=Path)
    compose.add_argument("environment", type=Path)
    image = sub.add_parser("image")
    image.add_argument("image_ref")
    image.add_argument("source_commit")
    cleanup_network = sub.add_parser("cleanup-network")
    cleanup_network.add_argument("network_id")
    running = sub.add_parser("running")
    running.add_argument("environment", type=Path)
    running.add_argument("--expected-id")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "inventory":
            inventory = load_inventory(args.path)
            reconcile_host(inventory, renderer_exists=args.renderer_exists)
        elif args.command == "snapshot-protected":
            payload = snapshot_protected()
            args.output.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        elif args.command == "assert-protected":
            assert_protected(args.expected)
        elif args.command == "compose":
            verify_compose(args.compose, args.environment)
        elif args.command == "image":
            verify_image(args.image_ref, args.source_commit)
        elif args.command == "cleanup-network":
            verify_cleanup_network(args.network_id)
        elif args.command == "running":
            print(verify_running(args.environment, expected_id=args.expected_id))
        return 0
    except (
        VerificationError,
        KeyError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"renderer deployment verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
