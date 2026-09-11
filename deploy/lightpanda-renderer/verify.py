#!/usr/bin/env python3
"""Fail-closed verifier for the one-service dormant Lightpanda deployment."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import platform
import pwd
import re
import stat
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

PROJECT = "jobseek-lightpanda"
SERVICE = "renderer"
CONTAINER = "jobseek-lightpanda-renderer"
NETWORK = "jobseek-lightpanda-renderer"
EGRESS_NETWORK = "jobseek-lightpanda-egress"
CONTROLLER_USER = "10001:10001"
SYSTEM_COMPOSE_PLUGIN_DIRECTORIES = (
    Path("/usr/local/lib/docker/cli-plugins"),
    Path("/usr/local/libexec/docker/cli-plugins"),
    Path("/usr/lib/docker/cli-plugins"),
    Path("/usr/libexec/docker/cli-plugins"),
)
BOOTSTRAP_ENTRYPOINT = [
    "/usr/bin/setpriv",
    "--reuid=10001",
    "--regid=10001",
    "--clear-groups",
    "--inh-caps=+kill,+setgid,+setuid",
    "--ambient-caps=+kill,+setgid,+setuid",
    "--nnp",
    "--",
    "/usr/local/bin/go-lightpanda",
]
COMPOSE_CAP_ADD = ["KILL", "SETGID", "SETUID"]
INSPECT_CAP_ADD = ["CAP_KILL", "CAP_SETGID", "CAP_SETUID"]
INVENTORY_KEYS = {
    "schema_version",
    "host_architecture",
    "service_ip",
    "public_ipv4",
    "public_ipv6_prefix",
    "public_ipv6_address",
    "private_ipv4",
    "private_interface",
    "crawler_private_ipv4",
    "production_public_ipv4",
    "production_public_ipv6",
    "dns_resolvers",
    "project_network",
    "default_docker_network",
    "provider_gateway",
    "renderer_network_name",
    "renderer_bridge_name",
    "renderer_network",
    "renderer_address",
    "egress_network_name",
    "egress_bridge_name",
    "egress_network",
    "egress_gateway",
    "egress_address",
    "published_address",
    "published_port",
}
CI_INVENTORY_KEY = "ci_test_only"
LEGACY_INVENTORY_KEYS = {
    "schema_version",
    "host_architecture",
    "service_ip",
    "public_ipv4",
    "public_ipv6_prefix",
    "public_ipv6_address",
    "private_ipv4",
    "project_network",
    "default_docker_network",
    "provider_gateway",
    "renderer_network_name",
    "renderer_network",
    "renderer_address",
}
PROTECTED = {
    "deploy-murmur-1": "murmur",
    "deploy-cloudflared-1": "cloudflared",
}
LEGACY_RELEASE_ENV_KEYS = {
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
RELEASE_ENV_KEYS = LEGACY_RELEASE_ENV_KEYS | {
    "NETWORK_POLICY_SHA256",
    "NETWORK_INVENTORY_SHA256",
}


class VerificationError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
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


def compose_plugin_path(
    search_directories: tuple[Path, ...] = SYSTEM_COMPOSE_PLUGIN_DIRECTORIES,
    *,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
    trust_boundary: Path = Path("/"),
) -> Path:
    """Resolve one immutable system Compose authority without ambient CLI discovery."""
    if not trust_boundary.is_absolute():
        fail("Docker Compose trust boundary is not absolute")
    for directory in search_directories:
        candidate = directory / "docker-compose"
        if not candidate.is_absolute() or not candidate.is_relative_to(trust_boundary):
            fail("Docker Compose search directory escaped its trust boundary")
        try:
            candidate_metadata = os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError as error:
            fail(f"system Docker Compose plugin cannot be inspected: {candidate}: {error}")
        if (
            not stat.S_ISREG(candidate_metadata.st_mode)
            or candidate_metadata.st_uid != trusted_uid
            or candidate_metadata.st_gid != trusted_gid
            or candidate_metadata.st_mode & 0o022
            or candidate_metadata.st_mode & 0o111 != 0o111
        ):
            fail(f"system Docker Compose plugin is not trusted: {candidate}")

        parent = candidate.parent
        while True:
            try:
                metadata = os.lstat(parent)
            except OSError as error:
                fail(f"system Docker Compose plugin parent cannot be inspected: {parent}: {error}")
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or metadata.st_uid != trusted_uid
                or metadata.st_gid != trusted_gid
                or metadata.st_mode & 0o022
            ):
                fail(f"system Docker Compose plugin parent is not trusted: {parent}")
            if parent == trust_boundary:
                break
            next_parent = parent.parent
            if next_parent == parent or not next_parent.is_relative_to(trust_boundary):
                fail("Docker Compose plugin parent chain escaped its trust boundary")
            parent = next_parent
        return candidate
    fail("trusted system Docker Compose plugin is absent")


def compose_version() -> str:
    return run_text([str(compose_plugin_path()), "version"]).strip()


def verify_egress_default_route(
    route_lines: list[str],
    expected_gateway: str,
    expected_endpoint_mac: object,
    read_interface_mac: Callable[[str], str],
) -> None:
    defaults = [
        line.split()
        for line in route_lines
        if len(line.split()) >= 3 and line.split()[1] == "00000000"
    ]
    gateway_hex = "".join(reversed([f"{int(part):02X}" for part in expected_gateway.split(".")]))
    if len(defaults) != 1 or defaults[0][2] != gateway_hex:
        fail("renderer default route does not use the fixed egress gateway")
    interface = defaults[0][0]
    if re.fullmatch(r"eth[0-9]+", interface) is None:
        fail("renderer default route interface is invalid")
    expected_mac = str(expected_endpoint_mac).lower()
    observed_mac = read_interface_mac(interface).strip().lower()
    mac_pattern = r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}"
    if (
        re.fullmatch(mac_pattern, expected_mac) is None
        or re.fullmatch(mac_pattern, observed_mac) is None
        or observed_mac != expected_mac
    ):
        fail("renderer default route interface is not the fixed egress endpoint")


def load_inventory(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ci_inventory = (
        isinstance(payload, dict)
        and set(payload) == INVENTORY_KEYS | {CI_INVENTORY_KEY}
        and payload.get(CI_INVENTORY_KEY) is True
    )
    if not isinstance(payload, dict) or (set(payload) != INVENTORY_KEYS and not ci_inventory):
        fail("deployment inventory keys are not the reviewed exact schema")
    if (
        payload["schema_version"] != 1
        or payload["host_architecture"] != "arm64"
        or payload["renderer_network_name"] != NETWORK
        or any(
            not isinstance(payload[key], str) or not payload[key]
            for key in INVENTORY_KEYS
            - {
                "schema_version",
                "production_public_ipv4",
                "production_public_ipv6",
                "dns_resolvers",
                "published_port",
            }
        )
    ):
        fail("deployment inventory identity fields are invalid")
    for key in (
        "public_ipv4",
        "public_ipv6_prefix",
        "private_ipv4",
        "project_network",
        "default_docker_network",
        "provider_gateway",
        "renderer_network",
        "crawler_private_ipv4",
        "egress_network",
    ):
        value = str(payload[key])
        if str(ipaddress.ip_network(value, strict=True)) != value:
            fail("deployment inventory contains a noncanonical prefix")
    public_ipv6_address = ipaddress.ip_address(str(payload["public_ipv6_address"]))
    public_ipv6_prefix = ipaddress.ip_network(str(payload["public_ipv6_prefix"]))
    public_ipv4 = ipaddress.ip_network(str(payload["public_ipv4"]))
    private_ipv4 = ipaddress.ip_network(str(payload["private_ipv4"]))
    project_network = ipaddress.ip_network(str(payload["project_network"]))
    provider_gateway = ipaddress.ip_network(str(payload["provider_gateway"]))
    renderer_network = ipaddress.ip_network(str(payload["renderer_network"]))
    crawler_private = ipaddress.ip_network(str(payload["crawler_private_ipv4"]))
    egress_network = ipaddress.ip_network(str(payload["egress_network"]))
    egress_gateway = ipaddress.ip_address(str(payload["egress_gateway"]))
    egress_address = ipaddress.ip_address(str(payload["egress_address"]))
    production_public = payload["production_public_ipv4"]
    production_public_ipv6 = payload["production_public_ipv6"]
    dns_resolvers = payload["dns_resolvers"]
    service_ip = ipaddress.ip_address(str(payload["service_ip"]))
    if (
        public_ipv4.version != 4
        or public_ipv4.prefixlen != 32
        or public_ipv6_prefix.version != 6
        or public_ipv6_address not in public_ipv6_prefix
        or private_ipv4.version != 4
        or private_ipv4.prefixlen != 32
        or service_ip != private_ipv4.network_address
        or not project_network.is_private
        or service_ip not in project_network
        or provider_gateway.version != 4
        or provider_gateway.prefixlen != 32
        or renderer_network.version != 4
        or renderer_network.prefixlen != 29
        or not renderer_network.is_private
        or crawler_private != ipaddress.ip_network("10.0.0.4/32")
        or egress_network != ipaddress.ip_network("172.30.94.8/29")
        or egress_gateway != ipaddress.ip_address("172.30.94.9")
        or egress_address != ipaddress.ip_address("172.30.94.10")
        or payload["egress_network_name"] != EGRESS_NETWORK
        or payload["egress_bridge_name"] != "br-jlp-egress"
        or payload["private_interface"] != "enp7s0"
        or payload["published_address"] != "10.0.0.5"
        or payload["published_port"] != 9443
        or production_public
        != [
            "116.203.192.19/32",
            "178.104.102.63/32",
            "178.104.132.47/32",
            "178.105.51.62/32",
        ]
        or production_public_ipv6
        != [
            "2a01:4f8:1c18:adf3::/64",
            "2a01:4f8:1c18:5f98::/64",
            "2a01:4f8:1c18:d03c::/64",
            "2a01:4f8:1c18:d64c::/64",
        ]
        or dns_resolvers != ["185.12.64.1", "185.12.64.2"]
        or (
            payload["renderer_bridge_name"] != "br-535f5f79245b"
            if not ci_inventory
            else re.fullmatch(r"br-[0-9a-f]{12}", str(payload["renderer_bridge_name"])) is None
        )
    ):
        fail("deployment inventory address roles are invalid")
    if not isinstance(renderer_network, ipaddress.IPv4Network):
        fail("renderer network is not IPv4")
    renderer_address = ipaddress.IPv4Address(str(payload["renderer_address"]))
    if renderer_address not in renderer_network or renderer_address in {
        renderer_network.network_address,
        renderer_network.broadcast_address,
    }:
        fail("renderer address is outside its dedicated network")
    if egress_address not in egress_network or egress_gateway not in egress_network:
        fail("renderer egress identities are outside their dedicated network")
    if not isinstance(payload["published_port"], int) or isinstance(
        payload["published_port"], bool
    ):
        fail("renderer published port is not an integer")
    return payload


def load_legacy_inventory(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    expected: dict[str, object] = {
        "schema_version": 1,
        "host_architecture": "arm64",
        "service_ip": "10.0.0.5",
        "public_ipv4": "178.105.51.62/32",
        "public_ipv6_prefix": "2a01:4f8:1c18:d64c::/64",
        "public_ipv6_address": "2a01:4f8:1c18:d64c::1",
        "private_ipv4": "10.0.0.5/32",
        "project_network": "10.0.0.0/16",
        "default_docker_network": "172.17.0.0/16",
        "provider_gateway": "172.31.1.1/32",
        "renderer_network_name": NETWORK,
        "renderer_network": "172.30.94.0/29",
        "renderer_address": "172.30.94.2",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != LEGACY_INVENTORY_KEYS
        or payload != expected
    ):
        fail("legacy deployment inventory drifted")
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
        if container["restart_policy"] != {"Name": "no", "MaximumRetryCount": 0}:
            fail("paused protected container restart policy drifted")
    return snapshot


def snapshot_protected() -> dict[str, Any]:
    return protected_snapshot_from_inspects(run_json(["docker", "inspect", *sorted(PROTECTED)]))


def assert_protected(expected_path: Path) -> None:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if snapshot_protected() != expected:
        fail("protected Murmur containers changed during renderer maintenance")


def verify_phase_a_receipt(
    path: Path, policy_sha256: str, inventory_sha256: str, unit_sha256: str
) -> dict[str, Any]:
    expected_path = Path("/var/lib/jobseek-lightpanda-acceptance/phase-a.json")
    deploy_gid = pwd.getpwnam("deploy").pw_gid
    parent_metadata = path.parent.stat(follow_symlinks=False)
    metadata = path.stat(follow_symlinks=False)
    if (
        path != expected_path
        or path.parent.is_symlink()
        or not stat.S_ISDIR(parent_metadata.st_mode)
        or (parent_metadata.st_uid, parent_metadata.st_gid) != (0, deploy_gid)
        or stat.S_IMODE(parent_metadata.st_mode) != 0o750
        or path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != deploy_gid
        or stat.S_IMODE(metadata.st_mode) != 0o640
        or metadata.st_nlink != 1
        or not 0 < metadata.st_size <= 512 * 1024
    ):
        fail("phase-A receipt metadata drifted")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    expected_keys = {
        "schema_version",
        "status",
        "acceptance_source_commit",
        "host_components",
        "before_boot_id",
        "after_boot_id",
        "protected_containers",
    }
    expected_components = {
        "policy_sha256": policy_sha256,
        "inventory_sha256": inventory_sha256,
        "unit_sha256": unit_sha256,
    }
    policy = "/usr/local/libexec/jobseek-lightpanda-network-policy"
    installed_unit_sha256 = hashlib.sha256(
        Path("/etc/systemd/system/jobseek-lightpanda-network.service").read_bytes()
    ).hexdigest()
    unit = "jobseek-lightpanda-network.service"
    boot_pattern = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    if (
        not isinstance(receipt, dict)
        or set(receipt) != expected_keys
        or receipt.get("schema_version") != 1
        or receipt.get("status") != "accepted"
        or re.fullmatch(r"[0-9a-f]{40}", str(receipt.get("acceptance_source_commit", ""))) is None
        or receipt.get("host_components") != expected_components
        or installed_unit_sha256 != unit_sha256
        or not isinstance(
            run_json(["sudo", "-n", policy, "verify-attested", policy_sha256, inventory_sha256]),
            dict,
        )
        or run_text(["systemctl", "is-active", unit]).strip() != "active"
        or run_text(["systemctl", "is-enabled", unit]).strip() != "enabled"
        or run_text(["systemctl", "show", "-p", "Result", "--value", unit]).strip() != "success"
        or re.fullmatch(boot_pattern, str(receipt.get("before_boot_id", ""))) is None
        or re.fullmatch(boot_pattern, str(receipt.get("after_boot_id", ""))) is None
        or receipt.get("before_boot_id") == receipt.get("after_boot_id")
        or receipt.get("protected_containers") != snapshot_protected()
    ):
        fail("phase-A receipt is stale or invalid")
    return receipt


def all_networks() -> list[dict[str, Any]]:
    ids = run_text(["docker", "network", "ls", "--quiet"]).split()
    return [] if not ids else run_json(["docker", "network", "inspect", *ids])


def expected_egress_route_identities(
    inventory: dict[str, object],
) -> set[tuple[str, ...]]:
    network = ipaddress.ip_network(str(inventory["egress_network"]))
    common = (
        str(inventory["egress_bridge_name"]),
        "kernel",
        str(inventory["egress_gateway"]),
    )
    # Linux 5.14+ treats the lowest subnet address as unicast, not the
    # historical/obsolete broadcast address. The target Docker/kernel path
    # therefore owns only the highest-address local broadcast route.
    return {
        ("main", "unicast", str(network), "link", *common, ""),
        ("local", "local", str(inventory["egress_gateway"]), "host", *common, ""),
        ("local", "broadcast", str(network.broadcast_address), "link", *common, ""),
    }


def verify_egress_kernel_routes(routes: list[dict[str, Any]], inventory: dict[str, object]) -> None:
    candidate = ipaddress.ip_network(str(inventory["egress_network"]))
    observed: list[tuple[str, ...]] = []
    for route in routes:
        destination = route.get("dst")
        if not destination or destination == "default":
            continue
        try:
            route_network = ipaddress.ip_network(str(destination), strict=False)
        except ValueError:
            fail("host route inventory contains a noncanonical prefix")
        if route_network.version != 4 or not route_network.overlaps(candidate):
            continue
        canonical_destination = (
            str(route_network.network_address)
            if route_network.prefixlen == route_network.max_prefixlen
            else str(route_network)
        )
        observed.append(
            (
                str(route.get("table", "main")),
                str(route.get("type", "unicast")),
                canonical_destination,
                str(route.get("scope", "")),
                str(route.get("dev", "")),
                str(route.get("protocol", "")),
                str(route.get("prefsrc", "")),
                str(route.get("gateway", "")),
            )
        )
    expected = expected_egress_route_identities(inventory)
    if len(observed) != len(expected) or set(observed) != expected:
        fail("renderer egress host routes are not the exact kernel route set")


def reconcile_renderer_networks(inventory: dict[str, object], *, renderer_exists: bool) -> None:
    addresses = run_json(["ip", "-j", "address", "show"])
    routes = run_json(["ip", "-j", "route", "show", "table", "all"])
    networks = all_networks()
    candidate = ipaddress.ip_network(str(inventory["renderer_network"]))
    egress_candidate = ipaddress.ip_network(str(inventory["egress_network"]))
    named_renderer = [network for network in networks if network.get("Name") == NETWORK]
    if renderer_exists and len(named_renderer) != 1:
        fail("renderer network is absent or duplicated")
    if len(named_renderer) > 1:
        fail("renderer network identity is duplicated")
    if named_renderer:
        renderer = named_renderer[0]
        network_id = str(renderer.get("Id", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", network_id):
            fail("renderer network content ID drifted")
        bridge_name = str(inventory["renderer_bridge_name"])
        bridge_links = [link for link in addresses if link.get("ifname") == bridge_name]
        if len(bridge_links) != 1:
            fail("isolated renderer bridge device is not exact")
        bridge_addresses = bridge_links[0].get("addr_info") or []
        if len(bridge_addresses) > 1 or any(
            info.get("family") != "inet6"
            or info.get("scope") != "link"
            or info.get("prefixlen") != 64
            or ipaddress.ip_address(str(info.get("local"))) not in ipaddress.ip_network("fe80::/10")
            for info in bridge_addresses
        ):
            fail("isolated renderer bridge unexpectedly has a routable host address")
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
            or renderer.get("Options")
            not in (
                {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
                {
                    "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
                    "com.docker.network.bridge.name": inventory["renderer_bridge_name"],
                },
            )
        ):
            fail("existing renderer network drifted from its no-egress contract")
        endpoints = renderer.get("Containers") or {}
        if renderer_exists:
            if len(endpoints) != 1:
                fail("renderer network endpoint inventory drifted")
            endpoint = next(iter(endpoints.values()))
            renderer_prefix = candidate.prefixlen
            if endpoint.get("Name") != CONTAINER or endpoint.get("IPv4Address") != (
                f"{inventory['renderer_address']}/{renderer_prefix}"
            ):
                fail("renderer network endpoint identity drifted")
        elif endpoints:
            fail("unused renderer network retained an endpoint")
    named_egress = [network for network in networks if network.get("Name") == EGRESS_NETWORK]
    if len(named_egress) != 1:
        fail("renderer egress network is absent or duplicated")
    egress = named_egress[0]
    egress_configs = egress.get("IPAM", {}).get("Config") or []
    egress_labels = egress.get("Labels") or {}
    if (
        egress.get("Internal") is not False
        or egress.get("EnableIPv6") is not False
        or egress.get("Attachable") is not False
        or [config.get("Subnet") for config in egress_configs] != [str(egress_candidate)]
        or [config.get("Gateway") for config in egress_configs] != [inventory["egress_gateway"]]
        or egress_labels.get("com.docker.compose.project") != PROJECT
        or egress_labels.get("com.docker.compose.network") != "egress"
        or egress.get("Options")
        != {
            "com.docker.network.bridge.enable_icc": "false",
            "com.docker.network.bridge.name": inventory["egress_bridge_name"],
        }
    ):
        fail("renderer egress network drifted")
    egress_links = [
        link for link in addresses if link.get("ifname") == inventory["egress_bridge_name"]
    ]
    if len(egress_links) != 1:
        fail("renderer egress bridge device is not exact")
    egress_addresses = egress_links[0].get("addr_info") or []
    if not any(
        info.get("family") == "inet"
        and info.get("local") == inventory["egress_gateway"]
        and info.get("prefixlen") == egress_candidate.prefixlen
        for info in egress_addresses
    ):
        fail("renderer egress bridge gateway drifted")
    egress_endpoints = egress.get("Containers") or {}
    if renderer_exists and egress_endpoints:
        if len(egress_endpoints) != 1:
            fail("renderer egress endpoint inventory drifted")
        endpoint = next(iter(egress_endpoints.values()))
        if endpoint.get("Name") != CONTAINER or endpoint.get("IPv4Address") != (
            f"{inventory['egress_address']}/{egress_candidate.prefixlen}"
        ):
            fail("renderer egress endpoint identity drifted")
    elif not renderer_exists and egress_endpoints:
        fail("unused renderer egress network retained an endpoint")
    for network in networks:
        if network.get("Name") in {NETWORK, EGRESS_NETWORK}:
            continue
        for config in network.get("IPAM", {}).get("Config") or []:
            subnet = config.get("Subnet")
            if subnet and any(
                ipaddress.ip_network(subnet).overlaps(prefix)
                for prefix in (candidate, egress_candidate)
            ):
                fail("renderer bridge overlaps an existing Docker network")
    verify_egress_kernel_routes(routes, inventory)
    for route in routes:
        prefix = route.get("dst")
        if not prefix or prefix == "default":
            continue
        try:
            route_network = ipaddress.ip_network(prefix)
        except ValueError:
            fail("host route inventory contains a noncanonical prefix")
        for prefix_network in (candidate, egress_candidate):
            if route_network.version != prefix_network.version or not route_network.overlaps(
                prefix_network
            ):
                continue
            if prefix_network == egress_candidate:
                continue
            fail("renderer bridge overlaps an existing host route")


def reconcile_host(inventory: dict[str, object], *, renderer_exists: bool) -> None:
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        fail("Murmur host is not ARM64")
    if run_json(["docker", "info", "--format", "{{json .LiveRestoreEnabled}}"]) is not False:
        fail("Docker live restore must be exactly disabled")

    addresses = run_json(["ip", "-j", "address", "show"])
    address_info = [
        info
        for link in addresses
        for info in link.get("addr_info", [])
        if "local" in info and "prefixlen" in info
    ]
    observed_addresses = {f"{info['local']}/{info['prefixlen']}" for info in address_info}
    for key in ("public_ipv4", "private_ipv4"):
        if inventory[key] not in observed_addresses:
            fail("live host address inventory drifted")
    public_ipv6_prefix = ipaddress.ip_network(str(inventory["public_ipv6_prefix"]))
    if not any(
        info["local"] == inventory["public_ipv6_address"]
        and info["prefixlen"] == public_ipv6_prefix.prefixlen
        for info in address_info
    ):
        fail("live host IPv6 address inventory drifted")

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

    reconcile_renderer_networks(inventory, renderer_exists=renderer_exists)

    if not renderer_exists:
        available_kib = None
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                available_kib = int(line.split()[1])
                break
        if available_kib is None or available_kib * 1024 < 1610612736:
            fail("less than 1.5 GiB is available for the first renderer start")


def deployment_deny_cidrs(inventory: dict[str, object]) -> list[str]:
    fixed = [
        str(inventory[key])
        for key in (
            "private_ipv4",
            "crawler_private_ipv4",
            "project_network",
            "default_docker_network",
            "provider_gateway",
            "renderer_network",
            "egress_network",
        )
    ]
    production_value = inventory["production_public_ipv4"]
    if not isinstance(production_value, list):
        fail("production public inventory is not a list")
    production = cast(list[object], production_value)
    production_ipv6_value = inventory["production_public_ipv6"]
    if not isinstance(production_ipv6_value, list):
        fail("production public IPv6 inventory is not a list")
    production_ipv6 = cast(list[object], production_ipv6_value)
    return [
        *fixed,
        *(str(value) for value in production),
        *(str(value) for value in production_ipv6),
    ]


def expected_service_command(env: dict[str, str], inventory: dict[str, object]) -> list[str]:
    deny_values = deployment_deny_cidrs(inventory)
    return [
        "--runtime-v1-service",
        "--listen",
        "0.0.0.0:9443",
        "--service-ip",
        str(inventory["service_ip"]),
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


def validate_compose_model(
    model: dict[str, Any], env: dict[str, str], inventory: dict[str, object]
) -> None:
    if set(model) != {"name", "services", "networks"}:
        fail("Compose model top-level keys drifted")
    if model.get("name") != PROJECT or set(model.get("services") or {}) != {SERVICE}:
        fail("Compose project contains anything except the renderer")
    service = model["services"][SERVICE]
    expected_service_keys = {
        "cap_add",
        "cap_drop",
        "cgroup",
        "command",
        "container_name",
        "cpus",
        "dns",
        "entrypoint",
        "image",
        "init",
        "labels",
        "logging",
        "mem_limit",
        "memswap_limit",
        "networks",
        "pids_limit",
        "platform",
        "ports",
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
    ci_release = re.fullmatch(r"sha-[0-9a-f]{40}-ci-r[0-9]+a[0-9]+", env["RELEASE_ID"])
    if service.get("image") != expected_ref or (
        "@sha256:" not in expected_ref
        and not (ci_release and expected_ref == "jobseek-lightpanda-renderer:pr")
    ):
        fail("Compose image is not the expected production digest or CI smoke image")
    exact = {
        "container_name": CONTAINER,
        "user": "0:0",
        "read_only": True,
        "init": True,
        "mem_limit": "1073741824",
        "memswap_limit": "1073741824",
        "pids_limit": 64,
        "restart": "on-failure:3",
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
        "org.jobseek.lightpanda.mode": "dormant-controlled-egress",
    }
    if service.get("labels") != expected_labels:
        fail("Compose renderer labels drifted")
    if service.get("cap_drop") != ["ALL"]:
        fail("Compose capability contract drifted")
    if service.get("cap_add") != COMPOSE_CAP_ADD:
        fail("Compose added-capability contract drifted")
    if service.get("security_opt") != ["no-new-privileges:true"]:
        fail("Compose privilege contract drifted")
    if service.get("stop_grace_period") != "30s":
        fail("Compose stop grace drifted")
    if service.get("logging") != {
        "driver": "local",
        "options": {"max-file": "3", "max-size": "10m"},
    }:
        fail("Compose logging bounds drifted")
    if service.get("dns") != inventory["dns_resolvers"]:
        fail("Compose resolver authority drifted")
    if service.get("ports") != [
        {
            "name": "private-mtls",
            "mode": "host",
            "host_ip": inventory["published_address"],
            "target": inventory["published_port"],
            "published": str(inventory["published_port"]),
            "protocol": "tcp",
            "app_protocol": "tls",
        }
    ]:
        fail("Compose private publication drifted")
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
        "uid=10002",
        "gid=10002",
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

    def bind_options_are_safe(value: object) -> bool:
        return isinstance(value, dict) and (
            not value or (set(value) == {"create_host_path"} and value["create_host_path"] is False)
        )

    if any(
        set(volume) != expected_volume_keys or not bind_options_are_safe(volume.get("bind"))
        for volume in volumes
    ):
        fail("rendered Compose credential mount keys drifted")

    if service.get("networks") != {
        SERVICE: {
            "ipv4_address": inventory["renderer_address"],
        },
        "egress": {
            "ipv4_address": inventory["egress_address"],
            "gw_priority": 1,
        },
    }:
        fail("renderer is attached to an unexpected network")
    networks = model.get("networks") or {}
    if set(networks) != {SERVICE, "egress"}:
        fail("Compose declares an unexpected network")
    network = networks[SERVICE]
    if set(network) != {"name", "ipam", "external"} or network.get("ipam") != {}:
        fail("renderer bridge keys drifted")
    if network.get("name") != NETWORK or network.get("external") is not True:
        fail("renderer bridge is not an exact root-owned external network")
    egress = networks["egress"]
    if set(egress) != {"name", "ipam", "external"} or egress.get("ipam") != {}:
        fail("renderer egress bridge keys drifted")
    if egress.get("name") != EGRESS_NETWORK or egress.get("external") is not True:
        fail("renderer egress bridge is not an exact root-owned external network")

    command = service.get("command") or []
    deny_values = [
        command[index + 1]
        for index, value in enumerate(command[:-1])
        if value == "--deployment-deny-cidr"
    ]
    if deny_values != deployment_deny_cidrs(inventory):
        fail("renderer startup deny inventory drifted")
    if command != expected_service_command(env, inventory):
        fail("renderer startup command drifted")


def read_env(path: Path, *, allow_legacy: bool = True) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            fail("release environment has an invalid record")
        key, value = line.split("=", 1)
        if key in result or not key or not value:
            fail("release environment is duplicate or incomplete")
        result[key] = value
    accepted = {frozenset(RELEASE_ENV_KEYS)}
    if allow_legacy:
        accepted.add(frozenset(LEGACY_RELEASE_ENV_KEYS))
    if frozenset(result) not in accepted:
        fail("release environment keys are not exact")
    for key in ("NETWORK_POLICY_SHA256", "NETWORK_INVENTORY_SHA256"):
        if key in result and not re.fullmatch(r"[0-9a-f]{64}", result[key]):
            fail("release policy digest is invalid")
    return result


def verify_compose(
    compose: Path,
    environment: Path,
    inventory_path: Path,
    *,
    allow_legacy_env: bool = False,
) -> None:
    env = read_env(environment, allow_legacy=allow_legacy_env)
    inventory = load_inventory(inventory_path)
    compose_plugin = compose_plugin_path()
    try:
        model = run_json(
            [
                str(compose_plugin),
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
    except subprocess.CalledProcessError as error:
        # Compose stderr contains only model/path diagnostics for this
        # credentials-free release environment. Bound and flatten it so CI can
        # identify a failed render without enabling shell tracing.
        detail = " ".join((error.stderr or "").split())[:1024]
        version = subprocess.run(
            [str(compose_plugin), "version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        version_detail = " ".join((version.stdout + version.stderr).split())[:256]
        fail(
            f"Docker Compose model render failed: {detail or 'no diagnostic'}; "
            f"compose-path={compose_plugin}; "
            f"compose-version-status={version.returncode} "
            f"compose-version={version_detail or 'no diagnostic'}"
        )
    validate_compose_model(model, env, inventory)


def release_policy_digests(environment: Path) -> tuple[str, str]:
    env = read_env(environment, allow_legacy=False)
    return env["NETWORK_POLICY_SHA256"], env["NETWORK_INVENTORY_SHA256"]


def verify_image(
    image_ref: str, source_commit: str, *, ci_release_id: str | None = None
) -> dict[str, Any]:
    images = run_json(["docker", "image", "inspect", image_ref])
    if len(images) != 1:
        fail("renderer image identity is not exact")
    image = images[0]
    if image.get("Architecture") != "arm64":
        fail("renderer image is not ARM64")
    if ci_release_id is None:
        if image_ref not in (image.get("RepoDigests") or []):
            fail("renderer image does not carry the requested immutable digest")
    elif image_ref != "jobseek-lightpanda-renderer:pr" or not re.fullmatch(
        rf"sha-{re.escape(source_commit)}-ci-r[0-9]+a[0-9]+", ci_release_id
    ):
        fail("renderer CI image identity is invalid")
    labels = image.get("Config", {}).get("Labels") or {}
    if labels.get("org.jobseek.lightpanda.source-commit") != source_commit:
        fail("renderer image source label drifted")
    if image.get("Config", {}).get("ExposedPorts") not in (None, {}):
        fail("dormant renderer image exposes a port")
    image_config = image.get("Config", {})
    if (
        image_config.get("User") != "0:0"
        or image_config.get("Entrypoint") != BOOTSTRAP_ENTRYPOINT
        or image_config.get("Cmd") != ["--runtime-v1-service"]
    ):
        fail("renderer image privilege bootstrap drifted")
    return image


def verify_cleanup_network(network_id: str, inventory_path: Path) -> None:
    inventory = load_inventory(inventory_path)
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
        or network.get("Options")
        not in (
            {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
            {
                "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
                "com.docker.network.bridge.name": inventory["renderer_bridge_name"],
            },
        )
        or labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.network") != SERVICE
        or [config.get("Subnet") for config in configs] != [inventory["renderer_network"]]
    ):
        fail("candidate renderer network is unsafe to remove")


def validate_cold_network_settings(
    network_settings: dict[str, Any],
    inventory: dict[str, object],
    expected_bindings: dict[str, list[dict[str, object]]],
    *,
    require_all_network_intents: bool,
) -> None:
    if network_settings.get("Ports") not in ({}, {"9443/tcp": None}, expected_bindings):
        fail("cold renderer runtime publication state drifted")
    networks = network_settings.get("Networks") or {}
    expected_addresses = {
        NETWORK: inventory["renderer_address"],
        EGRESS_NETWORK: inventory["egress_address"],
    }
    if not set(networks).issubset(expected_addresses) or (
        require_all_network_intents and set(networks) != set(expected_addresses)
    ):
        fail("cold renderer network intents drifted")
    for network_name, endpoint in networks.items():
        expected_address = expected_addresses[network_name]
        ipam = endpoint.get("IPAMConfig") or {}
        if (
            ipam.get("IPv4Address") != expected_address
            or ipam.get("IPv6Address") not in (None, "")
            or ipam.get("LinkLocalIPs") not in (None, [])
            or endpoint.get("GlobalIPv6Address") not in (None, "")
            or endpoint.get("GlobalIPv6PrefixLen") not in (None, 0)
        ):
            fail("cold renderer static network intent drifted")
        network_id = endpoint.get("NetworkID")
        endpoint_id = endpoint.get("EndpointID")
        if network_id not in (None, "") and not re.fullmatch(r"[0-9a-f]{64}", str(network_id)):
            fail("cold renderer network ID is invalid")
        if endpoint_id not in (None, "") and not re.fullmatch(r"[0-9a-f]{64}", str(endpoint_id)):
            fail("cold renderer endpoint ID is invalid")
        expected_gateway = inventory["egress_gateway"] if network_name == EGRESS_NETWORK else ""
        if (
            endpoint.get("Gateway") not in (None, "", expected_gateway)
            or endpoint.get("IPAddress") not in (None, "", expected_address)
            or endpoint.get("IPPrefixLen") not in (None, 0, 29)
        ):
            fail("cold renderer retained an unexpected network allocation")
        mac_address = endpoint.get("MacAddress")
        if mac_address not in (None, "") and not re.fullmatch(
            r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", str(mac_address)
        ):
            fail("cold renderer MAC address is invalid")


def validate_running_inspect(
    inspect: dict[str, Any],
    *,
    image_ref: str,
    image_id: str,
    source_commit: str,
    release_dir: str,
    release_id: str,
    environment: dict[str, str],
    inventory: dict[str, object],
    expected_state: Literal["running", "stopped", "created"] = "running",
) -> None:
    name = str(inspect.get("Name", "")).removeprefix("/")
    if name != CONTAINER or inspect.get("Image") != image_id:
        fail("running renderer image/container identity drifted")
    state = inspect.get("State") or {}
    if state.get("OOMKilled") is not False or not isinstance(state.get("Running"), bool):
        fail("renderer state is not an owned stable state")
    if expected_state == "created" and (
        state.get("Status") != "created"
        or state.get("Running") is not False
        or state.get("Paused") is not False
        or state.get("Restarting") is not False
        or state.get("Dead") is not False
        or state.get("ExitCode") != 0
        or state.get("Error") not in (None, "")
        or inspect.get("RestartCount") != 0
    ):
        fail("renderer is not an exact created candidate")
    if expected_state == "stopped" and (
        state.get("Status") != "exited"
        or state.get("Running") is not False
        or state.get("Paused") is not False
        or state.get("Restarting") is not False
        or state.get("Dead") is not False
        or state.get("Error") not in (None, "")
    ):
        fail("renderer is not an exact stopped predecessor")
    if expected_state == "running" and (
        state.get("Running") is not True or state.get("ExitCode") != 0
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
        "org.jobseek.lightpanda.mode": "dormant-controlled-egress",
    }
    if any(labels.get(key) != value for key, value in expected_labels.items()):
        fail("renderer labels drifted")
    if config.get("Image") != image_ref or config.get("User") != "0:0":
        fail("renderer image or user drifted")
    if config.get("Entrypoint") != BOOTSTRAP_ENTRYPOINT:
        fail("renderer entrypoint drifted")
    command = config.get("Cmd") or []
    if command != expected_service_command(environment, inventory):
        fail("renderer runtime command drifted")
    if config.get("ExposedPorts") != {"9443/tcp": {}}:
        fail("renderer container exposure metadata drifted")
    host = inspect.get("HostConfig") or {}
    if (
        host.get("ReadonlyRootfs") is not True
        or host.get("CapDrop") != ["ALL"]
        or host.get("CapAdd") != INSPECT_CAP_ADD
        or host.get("SecurityOpt") != ["no-new-privileges:true"]
        or host.get("Memory") != 1073741824
        or host.get("MemorySwap") != 1073741824
        or host.get("NanoCpus") != 1_000_000_000
        or host.get("PidsLimit") != 64
        or host.get("Init") is not True
        or host.get("NetworkMode") != EGRESS_NETWORK
        or host.get("PublishAllPorts") is not False
    ):
        fail("renderer containment drifted")
    if host.get("RestartPolicy") != {"Name": "on-failure", "MaximumRetryCount": 3}:
        fail("renderer restart policy drifted")
    if host.get("LogConfig") != {
        "Type": "local",
        "Config": {"max-file": "3", "max-size": "10m"},
    }:
        fail("renderer logging bounds drifted")
    if host.get("CgroupnsMode") != "private":
        fail("renderer cgroup namespace drifted")
    expected_bindings = {
        "9443/tcp": [
            {
                "HostIp": inventory["published_address"],
                "HostPort": str(inventory["published_port"]),
            }
        ]
    }
    if host.get("PortBindings") != expected_bindings:
        fail("renderer private host publication drifted")
    if host.get("Dns") != inventory["dns_resolvers"]:
        fail("renderer DNS authority drifted")
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
        "uid=10002",
        "gid=10002",
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

    network_settings = inspect.get("NetworkSettings") or {}
    networks = network_settings.get("Networks") or {}
    if expected_state in {"stopped", "created"}:
        validate_cold_network_settings(
            network_settings,
            inventory,
            expected_bindings,
            require_all_network_intents=expected_state == "created",
        )
        return
    if network_settings.get("Ports") != expected_bindings:
        fail("renderer runtime port publication state drifted")
    egress_endpoint_mac = (networks.get(EGRESS_NETWORK) or {}).get("MacAddress")
    if (
        set(networks) != {NETWORK, EGRESS_NETWORK}
        or networks[NETWORK].get("IPAddress") != inventory["renderer_address"]
        or networks[NETWORK].get("EndpointID") in (None, "")
        or networks[EGRESS_NETWORK].get("IPAddress") != inventory["egress_address"]
        or networks[EGRESS_NETWORK].get("EndpointID") in (None, "")
        or re.fullmatch(r"[0-9a-f]{2}(?::[0-9a-f]{2}){5}", str(egress_endpoint_mac).lower()) is None
    ):
        fail("renderer network attachment drifted")


def verify_idle_process_boundary(command: list[str]) -> None:
    lines = run_text(["docker", "top", CONTAINER, "-eo", "pid,uid,gid,comm,args"]).splitlines()
    if len(lines) != 3:
        fail("renderer idle process count drifted")
    rows = [line.split(None, 4) for line in lines[1:]]
    if any(len(row) != 5 or not row[0].isdigit() for row in rows):
        fail("renderer process inventory is invalid")
    by_command = {row[3]: row for row in rows}
    if set(by_command) != {"docker-init", "go-lightpanda"}:
        fail("renderer has an unexpected idle process")
    init = by_command["docker-init"]
    controller = by_command["go-lightpanda"]
    if init[1:3] != ["0", "0"] or init[4].split() != [
        "/sbin/docker-init",
        "--",
        *BOOTSTRAP_ENTRYPOINT,
        *command,
    ]:
        fail("renderer privilege bootstrap process drifted")
    if controller[1:3] != ["10001", "10001"] or controller[4].split() != [
        "/usr/local/bin/go-lightpanda",
        *command,
    ]:
        fail("renderer controller process identity drifted")


def verify_running_with_image(environment: Path, *, image_id: str, expected_id: str | None) -> str:
    env = read_env(environment)
    inventory = load_inventory(environment.parent / "inventory.json")
    reconcile_renderer_networks(inventory, renderer_exists=True)
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
        environment=env,
        inventory=inventory,
    )
    networks = run_json(["docker", "network", "inspect", NETWORK, EGRESS_NETWORK])
    if len(networks) != 2:
        fail("renderer network identities are not exact")
    by_name = {network.get("Name"): network for network in networks}
    if set(by_name) != {NETWORK, EGRESS_NETWORK}:
        fail("renderer network identities drifted")
    network = by_name[NETWORK]
    configs = network.get("IPAM", {}).get("Config") or []
    if (
        network.get("Internal") is not True
        or network.get("EnableIPv6") is not False
        or network.get("Attachable") is not False
        or [config.get("Subnet") for config in configs] != [inventory["renderer_network"]]
    ):
        fail("renderer does not have authoritative no-origin-egress networking")
    if network.get("Options") not in (
        {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
        {
            "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
            "com.docker.network.bridge.name": inventory["renderer_bridge_name"],
        },
    ):
        fail("renderer network does not isolate the host gateway")
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
    renderer_prefix = ipaddress.ip_network(str(inventory["renderer_network"])).prefixlen
    if endpoint.get("Name") != CONTAINER or endpoint.get("IPv4Address") != (
        f"{inventory['renderer_address']}/{renderer_prefix}"
    ):
        fail("renderer network endpoint drifted")
    egress = by_name[EGRESS_NETWORK]
    egress_configs = egress.get("IPAM", {}).get("Config") or []
    if (
        egress.get("Internal") is not False
        or egress.get("EnableIPv6") is not False
        or egress.get("Attachable") is not False
        or [config.get("Subnet") for config in egress_configs] != [inventory["egress_network"]]
        or [config.get("Gateway") for config in egress_configs] != [inventory["egress_gateway"]]
        or egress.get("Options")
        != {
            "com.docker.network.bridge.enable_icc": "false",
            "com.docker.network.bridge.name": inventory["egress_bridge_name"],
        }
    ):
        fail("renderer egress network drifted")
    egress_labels = egress.get("Labels") or {}
    if (
        egress_labels.get("com.docker.compose.project") != PROJECT
        or egress_labels.get("com.docker.compose.network") != "egress"
    ):
        fail("renderer egress network labels drifted")
    egress_endpoints = egress.get("Containers") or {}
    if set(egress_endpoints) != {inspect["Id"]}:
        fail("renderer egress network does not have its sole exact endpoint")
    egress_endpoint = egress_endpoints[inspect["Id"]]
    egress_prefix = ipaddress.ip_network(str(inventory["egress_network"])).prefixlen
    if egress_endpoint.get("Name") != CONTAINER or egress_endpoint.get("IPv4Address") != (
        f"{inventory['egress_address']}/{egress_prefix}"
    ):
        fail("renderer egress endpoint drifted")
    egress_endpoint_mac = (
        inspect.get("NetworkSettings", {}).get("Networks", {}).get(EGRESS_NETWORK, {})
    ).get("MacAddress")
    verify_idle_process_boundary(expected_service_command(env, inventory))
    docker_exec = ["docker", "exec", "--user", CONTROLLER_USER, CONTAINER]
    memory_max = run_text([*docker_exec, "cat", "/sys/fs/cgroup/memory.max"]).strip()
    memory_swap_max = run_text([*docker_exec, "cat", "/sys/fs/cgroup/memory.swap.max"]).strip()
    if memory_max != "1073741824" or memory_swap_max != "0":
        fail("renderer cgroup memory attestation drifted")
    ipv4_routes = run_text([*docker_exec, "cat", "/proc/net/route"]).splitlines()[1:]
    verify_egress_default_route(
        ipv4_routes,
        str(inventory["egress_gateway"]),
        egress_endpoint_mac,
        lambda interface: run_text([*docker_exec, "cat", f"/sys/class/net/{interface}/address"]),
    )
    ipv6_routes = run_text([*docker_exec, "cat", "/proc/net/ipv6_route"]).splitlines()
    if any(
        len(line.split()) >= 10
        and line.split()[0] == "0" * 32
        and line.split()[1] == "00"
        and line.split()[-1] != "lo"
        for line in ipv6_routes
    ):
        fail("internal renderer network unexpectedly has an IPv6 default route")
    run_text(
        [
            *docker_exec,
            "/usr/local/bin/go-lightpanda",
            "--runtime-v1-service-probe-no-client",
            "127.0.0.1:9443",
            "/run/credentials/ca.pem",
            str(inventory["service_ip"]),
        ]
    )
    return str(inspect["Id"])


def verify_running(environment: Path, *, expected_id: str | None) -> str:
    env = read_env(environment)
    ci_release_id = (
        env["RELEASE_ID"]
        if re.fullmatch(r"sha-[0-9a-f]{40}-ci-r[0-9]+a[0-9]+", env["RELEASE_ID"])
        else None
    )
    image = verify_image(
        env["RENDERER_IMAGE_REF"], env["SOURCE_COMMIT"], ci_release_id=ci_release_id
    )
    return verify_running_with_image(
        environment, image_id=str(image["Id"]), expected_id=expected_id
    )


def verify_owned(environment: Path, *, expected_id: str) -> str:
    env = read_env(environment)
    ci_release_id = (
        env["RELEASE_ID"]
        if re.fullmatch(r"sha-[0-9a-f]{40}-ci-r[0-9]+a[0-9]+", env["RELEASE_ID"])
        else None
    )
    image = verify_image(
        env["RENDERER_IMAGE_REF"], env["SOURCE_COMMIT"], ci_release_id=ci_release_id
    )
    inventory = load_inventory(environment.parent / "inventory.json")
    verify_compose(
        environment.parent / "compose.yml",
        environment,
        environment.parent / "inventory.json",
        allow_legacy_env=True,
    )
    inspects = run_json(["docker", "inspect", CONTAINER])
    if len(inspects) != 1 or inspects[0].get("Id") != expected_id:
        fail("owned renderer container identity is not exact")
    validate_running_inspect(
        inspects[0],
        image_ref=env["RENDERER_IMAGE_REF"],
        image_id=str(image["Id"]),
        source_commit=env["SOURCE_COMMIT"],
        release_dir=env["RENDERER_RELEASE_DIR"],
        release_id=env["RELEASE_ID"],
        environment=env,
        inventory=inventory,
        expected_state="stopped",
    )
    return expected_id


def verify_owned_created(environment: Path, *, expected_id: str) -> str:
    env = read_env(environment)
    ci_release_id = (
        env["RELEASE_ID"]
        if re.fullmatch(r"sha-[0-9a-f]{40}-ci-r[0-9]+a[0-9]+", env["RELEASE_ID"])
        else None
    )
    image = verify_image(
        env["RENDERER_IMAGE_REF"], env["SOURCE_COMMIT"], ci_release_id=ci_release_id
    )
    inventory = load_inventory(environment.parent / "inventory.json")
    verify_compose(
        environment.parent / "compose.yml",
        environment,
        environment.parent / "inventory.json",
    )
    inspects = run_json(["docker", "inspect", CONTAINER])
    if len(inspects) != 1 or inspects[0].get("Id") != expected_id:
        fail("created renderer container identity is not exact")
    validate_running_inspect(
        inspects[0],
        image_ref=env["RENDERER_IMAGE_REF"],
        image_id=str(image["Id"]),
        source_commit=env["SOURCE_COMMIT"],
        release_dir=env["RENDERER_RELEASE_DIR"],
        release_id=env["RELEASE_ID"],
        environment=env,
        inventory=inventory,
        expected_state="created",
    )
    return expected_id


def expected_legacy_command(environment: dict[str, str], inventory: dict[str, object]) -> list[str]:
    denied = [
        str(inventory["public_ipv4"]),
        str(inventory["public_ipv6_prefix"]),
        f"{inventory['service_ip']}/32",
        str(inventory["project_network"]),
        str(inventory["default_docker_network"]),
        str(inventory["provider_gateway"]),
        str(inventory["renderer_network"]),
    ]
    command = [
        "--runtime-v1-service",
        "--listen",
        "0.0.0.0:9443",
        "--service-ip",
        str(inventory["service_ip"]),
    ]
    for prefix in denied:
        command.extend(("--deployment-deny-cidr", prefix))
    command.extend(
        (
            "--tls-cert",
            "/run/credentials/server.pem",
            "--tls-key",
            "/run/credentials/server-key.pem",
            "--tls-ca",
            "/run/credentials/ca.pem",
            "--tls-ca-sha256",
            environment["CA_DER_SHA256"],
            "--client-leaf-sha256",
            environment["CLIENT_LEAF_SHA256"],
            "--client-spki-sha256",
            environment["CLIENT_SPKI_SHA256"],
        )
    )
    return command


def verify_legacy_owned(environment: Path, *, expected_id: str) -> str:
    env = read_env(environment)
    ci_release_id = (
        env["RELEASE_ID"]
        if re.fullmatch(r"sha-[0-9a-f]{40}-ci-r[0-9]+a[0-9]+", env["RELEASE_ID"])
        else None
    )
    image = verify_image(
        env["RENDERER_IMAGE_REF"], env["SOURCE_COMMIT"], ci_release_id=ci_release_id
    )
    inventory = load_legacy_inventory(environment.parent / "inventory.json")
    boundary_inventory = load_inventory(Path(__file__).resolve().parent / "inventory.json")
    for key in LEGACY_INVENTORY_KEYS:
        if inventory[key] != boundary_inventory[key]:
            fail("legacy and bootstrap inventory identities differ")
    inspects = run_json(["docker", "inspect", CONTAINER])
    if len(inspects) != 1:
        fail("legacy renderer identity is not exact")
    inspect = inspects[0]
    state = inspect.get("State") or {}
    config = inspect.get("Config") or {}
    host = inspect.get("HostConfig") or {}
    labels = config.get("Labels") or {}
    if (
        inspect.get("Id") != expected_id
        or str(inspect.get("Name", "")).removeprefix("/") != CONTAINER
        or inspect.get("Image") != image.get("Id")
        or state.get("OOMKilled") is not False
        or state.get("Running") is not False
        or state.get("Status") != "exited"
        or state.get("Paused") is not False
        or state.get("Restarting") is not False
        or state.get("Dead") is not False
        or labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != SERVICE
        or labels.get("org.jobseek.lightpanda.source-commit") != env["SOURCE_COMMIT"]
        or labels.get("org.jobseek.lightpanda.image-ref") != env["RENDERER_IMAGE_REF"]
        or labels.get("org.jobseek.lightpanda.release") != env["RELEASE_ID"]
        or labels.get("org.jobseek.lightpanda.mode") != "dormant-no-egress"
        or config.get("Image") != env["RENDERER_IMAGE_REF"]
        or config.get("User") != "0:0"
        or config.get("Entrypoint") != BOOTSTRAP_ENTRYPOINT
        or config.get("Cmd") != expected_legacy_command(env, inventory)
        or config.get("ExposedPorts") not in (None, {})
        or host.get("ReadonlyRootfs") is not True
        or host.get("CapDrop") != ["ALL"]
        or host.get("CapAdd") != INSPECT_CAP_ADD
        or host.get("SecurityOpt") != ["no-new-privileges:true"]
        or host.get("Memory") != 1073741824
        or host.get("MemorySwap") != 1073741824
        or host.get("NanoCpus") != 1_000_000_000
        or host.get("PidsLimit") != 64
        or host.get("Init") is not True
        or host.get("NetworkMode") != NETWORK
        or host.get("PortBindings") not in (None, {})
        or host.get("PublishAllPorts") is not False
        or host.get("RestartPolicy") != {"Name": "on-failure", "MaximumRetryCount": 3}
        or host.get("LogConfig")
        != {"Type": "local", "Config": {"max-file": "3", "max-size": "10m"}}
        or host.get("CgroupnsMode") != "private"
        or host.get("Ulimits") != [{"Name": "nofile", "Soft": 256, "Hard": 256}]
    ):
        fail("legacy renderer ownership metadata drifted")
    mounts = inspect.get("Mounts") or []
    targets = {
        "/run/credentials/ca.pem": "ca.pem",
        "/run/credentials/server.pem": "server.pem",
        "/run/credentials/server-key.pem": "server-key.pem",
    }
    if len(mounts) != 3 or any(
        mount.get("Type") != "bind"
        or mount.get("RW") is not False
        or mount.get("Destination") not in targets
        or mount.get("Source")
        != f"{env['RENDERER_RELEASE_DIR']}/pki/{targets.get(mount.get('Destination'), '')}"
        for mount in mounts
    ):
        fail("legacy renderer credential mounts drifted")
    networks = inspect.get("NetworkSettings", {}).get("Networks") or {}
    endpoint = networks.get(NETWORK) or {}
    ipam = endpoint.get("IPAMConfig") or {}
    if (
        set(networks) != {NETWORK}
        or ipam.get("IPv4Address") != inventory["renderer_address"]
        or ipam.get("IPv6Address") not in (None, "")
        or endpoint.get("IPAddress") not in (None, "", inventory["renderer_address"])
    ):
        fail("legacy stopped renderer static network intent drifted")
    network_id = endpoint.get("NetworkID")
    endpoint_id = endpoint.get("EndpointID")
    if network_id not in (None, "") and not re.fullmatch(r"[0-9a-f]{64}", str(network_id)):
        fail("legacy stopped renderer network ID is invalid")
    if endpoint_id not in (None, "") and not re.fullmatch(r"[0-9a-f]{64}", str(endpoint_id)):
        fail("legacy stopped renderer endpoint ID is invalid")
    return expected_id


def verify_owned_predecessor(environment: Path, *, expected_id: str) -> str:
    inspects = run_json(["docker", "inspect", CONTAINER])
    if len(inspects) != 1 or inspects[0].get("Id") != expected_id:
        fail("predecessor renderer identity is not exact")
    mode = (inspects[0].get("Config", {}).get("Labels") or {}).get("org.jobseek.lightpanda.mode")
    if mode == "dormant-controlled-egress":
        return verify_owned(environment, expected_id=expected_id)
    if mode == "dormant-no-egress":
        return verify_legacy_owned(environment, expected_id=expected_id)
    fail("predecessor renderer mode is not owned")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("path", type=Path)
    inventory.add_argument("--renderer-exists", action="store_true")
    inventory_file = sub.add_parser("inventory-file")
    inventory_file.add_argument("path", type=Path)
    snapshot = sub.add_parser("snapshot-protected")
    snapshot.add_argument("output", type=Path)
    protected = sub.add_parser("assert-protected")
    protected.add_argument("expected", type=Path)
    receipt = sub.add_parser("phase-a-receipt")
    receipt.add_argument("path", type=Path)
    receipt.add_argument("policy_sha256")
    receipt.add_argument("inventory_sha256")
    receipt.add_argument("unit_sha256")
    sub.add_parser("compose-path")
    sub.add_parser("compose-version")
    compose = sub.add_parser("compose")
    compose.add_argument("compose", type=Path)
    compose.add_argument("environment", type=Path)
    compose.add_argument("inventory", type=Path)
    image = sub.add_parser("image")
    image.add_argument("image_ref")
    image.add_argument("source_commit")
    image.add_argument("--ci-release-id")
    cleanup_network = sub.add_parser("cleanup-network")
    cleanup_network.add_argument("network_id")
    cleanup_network.add_argument("inventory", type=Path)
    policy_digests = sub.add_parser("policy-digests")
    policy_digests.add_argument("environment", type=Path)
    running = sub.add_parser("running")
    running.add_argument("environment", type=Path)
    running.add_argument("--expected-id")
    owned = sub.add_parser("owned")
    owned.add_argument("environment", type=Path)
    owned.add_argument("--expected-id", required=True)
    created = sub.add_parser("owned-created")
    created.add_argument("environment", type=Path)
    created.add_argument("--expected-id", required=True)
    predecessor = sub.add_parser("owned-predecessor")
    predecessor.add_argument("environment", type=Path)
    predecessor.add_argument("--expected-id", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "inventory":
            inventory = load_inventory(args.path)
            reconcile_host(inventory, renderer_exists=args.renderer_exists)
        elif args.command == "inventory-file":
            load_inventory(args.path)
        elif args.command == "snapshot-protected":
            payload = snapshot_protected()
            args.output.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        elif args.command == "assert-protected":
            assert_protected(args.expected)
        elif args.command == "phase-a-receipt":
            print(
                json.dumps(
                    verify_phase_a_receipt(
                        args.path,
                        args.policy_sha256,
                        args.inventory_sha256,
                        args.unit_sha256,
                    ),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        elif args.command == "compose-path":
            print(compose_plugin_path())
        elif args.command == "compose-version":
            print(compose_version())
        elif args.command == "compose":
            verify_compose(args.compose, args.environment, args.inventory)
        elif args.command == "image":
            verify_image(
                args.image_ref,
                args.source_commit,
                ci_release_id=args.ci_release_id,
            )
        elif args.command == "cleanup-network":
            verify_cleanup_network(args.network_id, args.inventory)
        elif args.command == "policy-digests":
            print(" ".join(release_policy_digests(args.environment)))
        elif args.command == "running":
            print(verify_running(args.environment, expected_id=args.expected_id))
        elif args.command == "owned":
            print(verify_owned(args.environment, expected_id=args.expected_id))
        elif args.command == "owned-created":
            print(verify_owned_created(args.environment, expected_id=args.expected_id))
        elif args.command == "owned-predecessor":
            print(verify_owned_predecessor(args.environment, expected_id=args.expected_id))
        return 0
    except (
        VerificationError,
        KeyError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"renderer deployment verification failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
