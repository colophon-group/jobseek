#!/usr/bin/env python3
"""Root-owned, renderer-scoped network policy for the Lightpanda B0 service."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

INVENTORY_PATH = Path("/etc/jobseek-lightpanda-network/inventory.json")
BOOTSTRAP_MARKER = Path("/var/lib/jobseek-lightpanda-network/bootstrap-complete.json")
PROJECT = "jobseek-lightpanda"
CONTAINER = "jobseek-lightpanda-renderer"
INTERNAL_NETWORK = "jobseek-lightpanda-renderer"
EGRESS_NETWORK = "jobseek-lightpanda-egress"
QUARANTINE_MAX_SCANS = 5

V4_FORWARD = "JSLP4-FWD"
V4_EGRESS = "JSLP4-EGRESS"
V4_INGRESS = "JSLP4-INGRESS"
V4_HOST = "JSLP4-HOST"
V4_OUTPUT = "JSLP4-OUTPUT"
V6_FORWARD = "JSLP6-FWD"
V6_HOST = "JSLP6-HOST"
V6_OUTPUT = "JSLP6-OUTPUT"
OWNED_CHAINS = {
    "iptables": (V4_FORWARD, V4_EGRESS, V4_INGRESS, V4_HOST, V4_OUTPUT),
    "ip6tables": (V6_FORWARD, V6_HOST, V6_OUTPUT),
}
SPECIAL_V4 = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.88.99.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/3",
)


class PolicyError(RuntimeError):
    pass


POLICY_FAILURES: tuple[type[Exception], ...] = (Exception,)


def fail(message: str) -> NoReturn:
    raise PolicyError(message)


def run(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        check=check,
        capture_output=True,
        text=True,
        timeout=45,
    )


def run_json(arguments: list[str]) -> Any:
    return json.loads(run(arguments).stdout)


def canonical_network(value: object, *, prefixlen: int | None = None) -> str:
    network = ipaddress.ip_network(str(value), strict=True)
    if network.version != 4 or (prefixlen is not None and network.prefixlen != prefixlen):
        fail("inventory contains an invalid IPv4 prefix")
    return str(network)


def canonical_address(value: object) -> str:
    address = ipaddress.ip_address(str(value))
    if address.version != 4:
        fail("inventory contains an invalid IPv4 address")
    return str(address)


@dataclass(frozen=True)
class Inventory:
    host_private: str
    private_interface: str
    crawler_private: str
    production_public: tuple[str, ...]
    production_public_ipv6: tuple[str, ...]
    dns_resolvers: tuple[str, str]
    internal_network: str
    internal_address: str
    internal_bridge: str
    egress_network: str
    egress_gateway: str
    egress_address: str
    egress_bridge: str
    published_address: str
    published_port: int

    @classmethod
    def load(cls, path: Path) -> Inventory:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "ci_test_only" in payload and payload["ci_test_only"] is not True:
            fail("CI inventory marker is invalid")
        ci_inventory = payload.get("ci_test_only") is True
        production = tuple(
            canonical_network(value, prefixlen=32) for value in payload["production_public_ipv4"]
        )
        dns = tuple(canonical_address(value) for value in payload["dns_resolvers"])
        production_ipv6 = tuple(
            str(ipaddress.ip_network(str(value), strict=True))
            for value in payload["production_public_ipv6"]
        )
        if production != (
            "116.203.192.19/32",
            "178.104.102.63/32",
            "178.104.132.47/32",
            "178.105.51.62/32",
        ) or dns != ("185.12.64.1", "185.12.64.2"):
            fail("production or resolver inventory drifted")
        if production_ipv6 != (
            "2a01:4f8:1c18:adf3::/64",
            "2a01:4f8:1c18:5f98::/64",
            "2a01:4f8:1c18:d03c::/64",
            "2a01:4f8:1c18:d64c::/64",
        ):
            fail("production IPv6 inventory drifted")
        interface = str(payload["private_interface"])
        bridge = str(payload["egress_bridge_name"])
        internal_bridge = str(payload["renderer_bridge_name"])
        expected_internal_bridge = (
            re.fullmatch(r"br-[0-9a-f]{12}", internal_bridge) is not None
            if ci_inventory
            else internal_bridge == "br-535f5f79245b"
        )
        if (
            interface != "enp7s0"
            or not re.fullmatch(r"[a-z0-9-]{1,15}", bridge)
            or not expected_internal_bridge
        ):
            fail("host interface inventory drifted")
        internal_network = canonical_network(payload["renderer_network"])
        egress_network = canonical_network(payload["egress_network"])
        internal_address = canonical_address(payload["renderer_address"])
        egress_address = canonical_address(payload["egress_address"])
        egress_gateway = canonical_address(payload["egress_gateway"])
        if (
            internal_network != "172.30.94.0/29"
            or egress_network != "172.30.94.8/29"
            or internal_address != "172.30.94.2"
            or egress_address != "172.30.94.10"
            or egress_gateway != "172.30.94.9"
            or ipaddress.ip_address(internal_address) not in ipaddress.ip_network(internal_network)
            or ipaddress.ip_address(egress_address) not in ipaddress.ip_network(egress_network)
            or ipaddress.ip_address(egress_gateway) not in ipaddress.ip_network(egress_network)
        ):
            fail("renderer subnet inventory drifted")
        port = payload["published_port"]
        if not isinstance(port, int) or isinstance(port, bool) or port != 9443:
            fail("renderer port inventory drifted")
        result = cls(
            host_private=canonical_network(payload["private_ipv4"], prefixlen=32),
            private_interface=interface,
            crawler_private=canonical_network(payload["crawler_private_ipv4"], prefixlen=32),
            production_public=production,
            production_public_ipv6=production_ipv6,
            dns_resolvers=(dns[0], dns[1]),
            internal_network=internal_network,
            internal_address=internal_address,
            internal_bridge=internal_bridge,
            egress_network=egress_network,
            egress_gateway=egress_gateway,
            egress_address=egress_address,
            egress_bridge=bridge,
            published_address=canonical_address(payload["published_address"]),
            published_port=port,
        )
        if (
            result.host_private != "10.0.0.5/32"
            or result.published_address != "10.0.0.5"
            or result.crawler_private != "10.0.0.4/32"
        ):
            fail("private service inventory drifted")
        return result


def docker_networks() -> list[dict[str, Any]]:
    identifiers = run(["docker", "network", "ls", "--quiet"]).stdout.split()
    return [] if not identifiers else run_json(["docker", "network", "inspect", *identifiers])


def network_bridge(network: dict[str, Any]) -> str:
    explicit = (network.get("Options") or {}).get("com.docker.network.bridge.name")
    if explicit:
        bridge = str(explicit)
    else:
        identifier = str(network.get("Id", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            fail("Docker network has an invalid content ID")
        bridge = f"br-{identifier[:12]}"
    if len(bridge) > 15:
        fail("Docker bridge name exceeds the kernel interface limit")
    return bridge


def network_by_name(name: str) -> dict[str, Any] | None:
    matches = [network for network in docker_networks() if network.get("Name") == name]
    if len(matches) > 1:
        fail("Docker network name is duplicated")
    return None if not matches else matches[0]


def expected_network(
    network: dict[str, Any], inventory: Inventory, *, role: str
) -> tuple[str, str]:
    configs = network.get("IPAM", {}).get("Config") or []
    labels = network.get("Labels") or {}
    subnets = [config.get("Subnet") for config in configs]
    gateways = [config.get("Gateway") for config in configs]
    common = (
        network.get("Driver") == "bridge"
        and network.get("EnableIPv6") is False
        and network.get("Attachable") is False
        and labels.get("com.docker.compose.project") == PROJECT
        and labels.get("com.docker.compose.network") == role
    )
    if role == "renderer":
        options = network.get("Options") or {}
        accepted_options = (
            {"com.docker.network.bridge.gateway_mode_ipv4": "isolated"},
            {
                "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
                "com.docker.network.bridge.name": inventory.internal_bridge,
            },
        )
        valid = (
            common
            and network.get("Name") == INTERNAL_NETWORK
            and network.get("Internal") is True
            and subnets == [inventory.internal_network]
            and options in accepted_options
            and network_bridge(network) == inventory.internal_bridge
        )
        address = inventory.internal_address
    else:
        valid = (
            common
            and network.get("Name") == EGRESS_NETWORK
            and network.get("Internal") is False
            and subnets == [inventory.egress_network]
            and gateways == [inventory.egress_gateway]
            and (network.get("Options") or {})
            == {
                "com.docker.network.bridge.enable_icc": "false",
                "com.docker.network.bridge.name": inventory.egress_bridge,
            }
        )
        address = inventory.egress_address
    if not valid:
        fail(f"{role} Docker network drifted")
    return network_bridge(network), address


def expected_egress_route_identities(inventory: Inventory) -> set[tuple[str, ...]]:
    network = ipaddress.ip_network(inventory.egress_network)
    common = (inventory.egress_bridge, "kernel", inventory.egress_gateway)
    return {
        ("main", "unicast", str(network), "link", *common, ""),
        ("local", "local", inventory.egress_gateway, "host", *common, ""),
        ("local", "broadcast", str(network.network_address), "link", *common, ""),
        ("local", "broadcast", str(network.broadcast_address), "link", *common, ""),
    }


def verify_egress_routes(
    routes: list[dict[str, Any]], inventory: Inventory, *, network_exists: bool
) -> None:
    candidate = ipaddress.ip_network(inventory.egress_network)
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
        route_type = str(route.get("type", "unicast"))
        table = str(route.get("table", "main"))
        canonical_destination = (
            str(route_network.network_address)
            if route_network.prefixlen == route_network.max_prefixlen
            else str(route_network)
        )
        observed.append(
            (
                table,
                route_type,
                canonical_destination,
                str(route.get("scope", "")),
                str(route.get("dev", "")),
                str(route.get("protocol", "")),
                str(route.get("prefsrc", "")),
                str(route.get("gateway", "")),
            )
        )
    expected = expected_egress_route_identities(inventory) if network_exists else set()
    if len(observed) != len(expected) or set(observed) != expected:
        fail("renderer egress host routes are not the exact kernel route set")


def audit_candidate(inventory: Inventory) -> None:
    addresses = run_json(["ip", "-j", "address", "show"])
    private_matches = [
        link
        for link in addresses
        if link.get("ifname") == inventory.private_interface
        and any(
            info.get("family") == "inet"
            and f"{info.get('local')}/{info.get('prefixlen')}" == inventory.host_private
            for info in link.get("addr_info") or []
        )
    ]
    if len(private_matches) != 1:
        fail("private service address is not exact on enp7s0")

    candidate = ipaddress.ip_network(inventory.egress_network)
    egress_network_exists = False
    for network in docker_networks():
        if network.get("Name") == EGRESS_NETWORK:
            egress_network_exists = True
        for config in network.get("IPAM", {}).get("Config") or []:
            subnet = config.get("Subnet")
            if not subnet:
                continue
            existing = ipaddress.ip_network(str(subnet))
            if existing.version != 4 or not existing.overlaps(candidate):
                continue
            if network.get("Name") != EGRESS_NETWORK or str(existing) != str(candidate):
                fail("candidate egress subnet overlaps another Docker network")
    verify_egress_routes(
        run_json(["ip", "-j", "route", "show", "table", "all"]),
        inventory,
        network_exists=egress_network_exists,
    )
    for link in addresses:
        for info in link.get("addr_info") or []:
            if info.get("family") != "inet":
                continue
            existing = ipaddress.ip_interface(
                f"{info.get('local')}/{info.get('prefixlen')}"
            ).network
            if existing.overlaps(candidate) and link.get("ifname") != inventory.egress_bridge:
                fail("candidate egress subnet overlaps a host interface")

    container_ids = run(["docker", "ps", "--all", "--quiet"]).stdout.split()
    inspected = [] if not container_ids else run_json(["docker", "inspect", *container_ids])
    exact_renderer_claim = audit_container_port_bindings(inspected, inventory)
    if not exact_renderer_claim:
        listeners = run(["ss", "-H", "-ltn", f"sport = :{inventory.published_port}"]).stdout.strip()
        if listeners:
            fail("private renderer port has a non-Docker listener")


def audit_container_port_bindings(
    inspected_containers: list[dict[str, Any]], inventory: Inventory
) -> bool:
    """Reject every exact-address or wildcard Docker claim on the service port."""
    exact_renderer_claim = False
    expected = {
        "9443/tcp": [
            {
                "HostIp": inventory.published_address,
                "HostPort": str(inventory.published_port),
            }
        ]
    }
    wildcard_addresses = {"", "0.0.0.0", "::"}
    for inspected in inspected_containers:
        bindings = (inspected.get("HostConfig") or {}).get("PortBindings") or {}
        if not isinstance(bindings, dict):
            fail("Docker port-binding inventory is malformed")
        collisions: list[tuple[str, dict[str, Any]]] = []
        for container_port, host_bindings in bindings.items():
            if host_bindings is None:
                continue
            if not isinstance(host_bindings, list):
                fail("Docker port-binding inventory is malformed")
            for binding in host_bindings:
                if not isinstance(binding, dict):
                    fail("Docker port-binding inventory is malformed")
                host_address = str(binding.get("HostIp", ""))
                host_port = str(binding.get("HostPort", ""))
                if host_port == str(inventory.published_port) and host_address in {
                    inventory.published_address,
                    *wildcard_addresses,
                }:
                    collisions.append((str(container_port), binding))
        if not collisions:
            continue
        name = str(inspected.get("Name", "")).removeprefix("/")
        labels = inspected.get("Config", {}).get("Labels") or {}
        if (
            name != CONTAINER
            or labels.get("com.docker.compose.project") != PROJECT
            or labels.get("com.docker.compose.service") != "renderer"
            or bindings != expected
            or collisions
            != [
                (
                    "9443/tcp",
                    {
                        "HostIp": inventory.published_address,
                        "HostPort": str(inventory.published_port),
                    },
                )
            ]
            or exact_renderer_claim
        ):
            fail("private renderer port is already claimed unexpectedly")
        exact_renderer_claim = True
    return exact_renderer_claim


def create_networks(inventory: Inventory) -> tuple[dict[str, Any], dict[str, Any], set[str]]:
    created: set[str] = set()
    internal = network_by_name(INTERNAL_NETWORK)
    if internal is None:
        run(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--internal",
                "--ipv6=false",
                "--subnet",
                inventory.internal_network,
                "--opt",
                "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                "--opt",
                f"com.docker.network.bridge.name={inventory.internal_bridge}",
                "--label",
                f"com.docker.compose.project={PROJECT}",
                "--label",
                "com.docker.compose.network=renderer",
                INTERNAL_NETWORK,
            ]
        )
        created.add(INTERNAL_NETWORK)
        internal = network_by_name(INTERNAL_NETWORK)
    egress = network_by_name(EGRESS_NETWORK)
    if egress is None:
        run(
            [
                "docker",
                "network",
                "create",
                "--driver",
                "bridge",
                "--ipv6=false",
                "--subnet",
                inventory.egress_network,
                "--gateway",
                inventory.egress_gateway,
                "--opt",
                f"com.docker.network.bridge.name={inventory.egress_bridge}",
                "--opt",
                "com.docker.network.bridge.enable_icc=false",
                "--label",
                f"com.docker.compose.project={PROJECT}",
                "--label",
                "com.docker.compose.network=egress",
                EGRESS_NETWORK,
            ]
        )
        created.add(EGRESS_NETWORK)
        egress = network_by_name(EGRESS_NETWORK)
    if internal is None or egress is None:
        fail("renderer Docker networks could not be created")
    expected_network(internal, inventory, role="renderer")
    expected_network(egress, inventory, role="egress")
    return internal, egress, created


Rule = tuple[str, ...]


def policy_rules(inventory: Inventory, internal_bridge: str) -> dict[str, dict[str, list[Rule]]]:
    drop = ("-j", "DROP")
    v4_egress: list[Rule] = [
        ("-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT")
    ]
    v4_egress.extend(("-d", cidr, *drop) for cidr in SPECIAL_V4)
    v4_egress.extend(("-d", cidr, *drop) for cidr in inventory.production_public)
    for resolver in inventory.dns_resolvers:
        v4_egress.extend(
            (
                (
                    "-d",
                    f"{resolver}/32",
                    "-p",
                    "udp",
                    "-m",
                    "udp",
                    "--dport",
                    "53",
                    "-j",
                    "ACCEPT",
                ),
                (
                    "-d",
                    f"{resolver}/32",
                    "-p",
                    "tcp",
                    "-m",
                    "tcp",
                    "--dport",
                    "53",
                    "-j",
                    "ACCEPT",
                ),
            )
        )
    v4_egress.extend(
        (
            ("-p", "tcp", "-m", "tcp", "--dport", "443", "-j", "ACCEPT"),
            drop,
        )
    )
    return {
        "iptables": {
            V4_EGRESS: v4_egress,
            V4_INGRESS: [
                ("-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT"),
                (
                    "-i",
                    inventory.private_interface,
                    "-s",
                    inventory.crawler_private,
                    "-d",
                    f"{inventory.egress_address}/32",
                    "-p",
                    "tcp",
                    "-m",
                    "tcp",
                    "--dport",
                    str(inventory.published_port),
                    "-m",
                    "conntrack",
                    "--ctstate",
                    "NEW",
                    "-j",
                    "ACCEPT",
                ),
                drop,
            ],
            V4_FORWARD: [
                (
                    "-i",
                    inventory.egress_bridge,
                    "-s",
                    f"{inventory.egress_address}/32",
                    "-j",
                    V4_EGRESS,
                ),
                ("-i", inventory.egress_bridge, *drop),
                (
                    "-o",
                    inventory.egress_bridge,
                    "-d",
                    f"{inventory.egress_address}/32",
                    "-j",
                    V4_INGRESS,
                ),
                ("-o", inventory.egress_bridge, *drop),
                ("-i", internal_bridge, "-s", f"{inventory.internal_address}/32", *drop),
                ("-i", internal_bridge, *drop),
                ("-o", internal_bridge, *drop),
                ("-j", "RETURN"),
            ],
            V4_HOST: [
                ("-i", inventory.egress_bridge, "-s", f"{inventory.egress_address}/32", *drop),
                ("-i", internal_bridge, "-s", f"{inventory.internal_address}/32", *drop),
                (
                    "-i",
                    inventory.private_interface,
                    "-s",
                    inventory.crawler_private,
                    "-d",
                    inventory.host_private,
                    "-p",
                    "tcp",
                    "-m",
                    "tcp",
                    "--dport",
                    str(inventory.published_port),
                    "-j",
                    "ACCEPT",
                ),
                (
                    "-d",
                    inventory.host_private,
                    "-p",
                    "tcp",
                    "-m",
                    "tcp",
                    "--dport",
                    str(inventory.published_port),
                    *drop,
                ),
                ("-j", "RETURN"),
            ],
            V4_OUTPUT: [
                ("-o", inventory.egress_bridge, "-d", f"{inventory.egress_address}/32", *drop),
                ("-o", internal_bridge, "-d", f"{inventory.internal_address}/32", *drop),
                (
                    "-d",
                    inventory.host_private,
                    "-p",
                    "tcp",
                    "-m",
                    "tcp",
                    "--dport",
                    str(inventory.published_port),
                    *drop,
                ),
                ("-j", "RETURN"),
            ],
        },
        "ip6tables": {
            V6_FORWARD: [
                ("-i", inventory.egress_bridge, *drop),
                ("-o", inventory.egress_bridge, *drop),
                ("-i", internal_bridge, *drop),
                ("-o", internal_bridge, *drop),
                ("-j", "RETURN"),
            ],
            V6_HOST: [
                ("-i", inventory.egress_bridge, *drop),
                ("-i", internal_bridge, *drop),
                ("-j", "RETURN"),
            ],
            V6_OUTPUT: [
                ("-o", inventory.egress_bridge, *drop),
                ("-o", internal_bridge, *drop),
                ("-j", "RETURN"),
            ],
        },
    }


HOOKS = {
    "iptables": {"DOCKER-USER": V4_FORWARD, "INPUT": V4_HOST, "OUTPUT": V4_OUTPUT},
    "ip6tables": {"FORWARD": V6_FORWARD, "INPUT": V6_HOST, "OUTPUT": V6_OUTPUT},
}


def backend_preflight() -> None:
    if os.geteuid() != 0:
        fail("network policy requires root")
    driver = run(["docker", "info", "--format", "{{.FirewallBackend.Driver}}"]).stdout.strip()
    if driver != "iptables":
        fail("Docker is not using the reviewed iptables firewall backend")
    live_restore = run_json(["docker", "info", "--format", "{{json .LiveRestoreEnabled}}"])
    if live_restore is not False:
        fail("Docker live restore must be exactly disabled")
    for binary in ("iptables", "ip6tables"):
        if "nf_tables" not in run([binary, "--version"]).stdout:
            fail("host does not use the reviewed iptables-nft backend")


def chain_exists(binary: str, chain: str) -> bool:
    return run([binary, "--wait", "30", "-n", "-L", chain], check=False).returncode == 0


def observed_rules(binary: str, chain: str, *, table: str = "filter") -> list[Rule]:
    output = run([binary, "--wait", "30", "-t", table, "-S", chain]).stdout.splitlines()
    prefix = f"-A {chain} "
    return [tuple(shlex.split(line[len(prefix) :])) for line in output if line.startswith(prefix)]


def replace_chain(binary: str, chain: str, rules: list[Rule]) -> None:
    if chain_exists(binary, chain):
        run([binary, "--wait", "30", "-F", chain])
    else:
        run([binary, "--wait", "30", "-N", chain])
    # Install the terminal rule first. A mistakenly hooked partially-built
    # chain is fail-closed for every renderer-scoped chain.
    terminal = rules[-1]
    run([binary, "--wait", "30", "-A", chain, *terminal])
    for rule in reversed(rules[:-1]):
        run([binary, "--wait", "30", "-I", chain, "1", *rule])
    if observed_rules(binary, chain) != rules:
        fail(f"owned firewall chain {chain} was not built exactly")


def hook_rules(binary: str, parent: str, target: str) -> list[Rule]:
    return [rule for rule in observed_rules(binary, parent) if rule[-2:] == ("-j", target)]


def ensure_hook(binary: str, parent: str, target: str) -> None:
    matches = hook_rules(binary, parent, target)
    if not matches:
        run([binary, "--wait", "30", "-I", parent, "1", "-j", target])
        matches = hook_rules(binary, parent, target)
    if matches != [("-j", target)]:
        fail(f"owned firewall hook {parent}->{target} drifted")
    rules = observed_rules(binary, parent)
    position = rules.index(("-j", target))
    if position != 0:
        fail(f"owned firewall hook {parent}->{target} is not rule zero")


def remove_owned_hooks() -> None:
    for binary, hooks in HOOKS.items():
        for parent, target in hooks.items():
            while hook_rules(binary, parent, target):
                run([binary, "--wait", "30", "-D", parent, "-j", target])


def exact_running_renderer_id(inventory: Inventory) -> str | None:
    inspected = run(["docker", "inspect", CONTAINER], check=False)
    if inspected.returncode != 0:
        return None
    payload = json.loads(inspected.stdout)
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        fail("Docker returned an ambiguous named renderer identity")
    container = payload[0]
    if container.get("State", {}).get("Running") is not True:
        return None
    labels = container.get("Config", {}).get("Labels") or {}
    networks = container.get("NetworkSettings", {}).get("Networks") or {}
    if (
        str(container.get("Name", "")).removeprefix("/") != CONTAINER
        or labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != "renderer"
        or not re.fullmatch(
            r"sha-[0-9a-f]{40}-(ci-)?r[0-9]+a[0-9]+",
            str(labels.get("org.jobseek.lightpanda.release", "")),
        )
        or set(networks) != {INTERNAL_NETWORK, EGRESS_NETWORK}
        or networks[INTERNAL_NETWORK].get("IPAddress") != inventory.internal_address
        or networks[EGRESS_NETWORK].get("IPAddress") != inventory.egress_address
    ):
        fail("running named renderer identity drifted")
    identifier = str(container.get("Id", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        fail("running named renderer has an invalid Docker identity")
    return identifier


def inspect_container(reference: str, *, expected_name: str | None = None) -> dict[str, Any] | None:
    result = run(["docker", "inspect", reference], check=False)
    if result.returncode != 0:
        return None
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        fail("Docker returned an ambiguous container identity during quarantine")
    if (
        expected_name is not None
        and str(payload[0].get("Name", "")).removeprefix("/") != expected_name
    ):
        fail("Docker returned an ambiguous container identity during quarantine")
    return payload[0]


def stop_container_for_quarantine(identifier: str) -> bool:
    stopped = run(["docker", "stop", "--time", "30", identifier], check=False)
    state = run(
        ["docker", "inspect", "--format", "{{json .State.Running}}", identifier],
        check=False,
    )
    if state.returncode == 0:
        return state.stdout.strip() == "false"
    return stopped.returncode == 0


def stop_named_container_for_quarantine(name: str, errors: list[str]) -> None:
    try:
        inspected = inspect_container(name, expected_name=name)
    except Exception:
        errors.append("named container metadata could not be inspected")
        return
    if inspected is None:
        return
    identifier = str(inspected.get("Id", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", identifier):
        errors.append("named container has an invalid Docker identity")
        return
    try:
        stopped = stop_container_for_quarantine(identifier)
    except Exception:
        stopped = False
    if not stopped:
        errors.append("named container could not be stopped")


def quarantine_running_renderer() -> None:
    errors: list[str] = []
    empty_scans = 0
    for _scan in range(QUARANTINE_MAX_SCANS):
        stop_named_container_for_quarantine(CONTAINER, errors)

        try:
            egress = network_by_name(EGRESS_NETWORK)
        except Exception:
            errors.append("dedicated egress network metadata could not be inspected")
            empty_scans = 0
            continue
        if egress is None:
            empty_scans += 1
            if empty_scans >= 2:
                break
            continue
        observed_endpoints = egress.get("Containers")
        if observed_endpoints is None:
            observed_endpoints = {}
        if not isinstance(observed_endpoints, dict):
            errors.append("dedicated egress network endpoint inventory is malformed")
            empty_scans = 0
            continue
        if not observed_endpoints:
            empty_scans += 1
            if empty_scans >= 2:
                break
            continue

        empty_scans = 0
        for raw_identifier, endpoint in list(observed_endpoints.items()):
            identifier = str(raw_identifier)
            if not re.fullmatch(r"[0-9a-f]{64}", identifier):
                errors.append("dedicated egress endpoint has an invalid Docker identity")
                continue
            endpoint_name = ""
            if isinstance(endpoint, dict):
                endpoint_name = str(endpoint.get("Name", "")).removeprefix("/")
            else:
                errors.append("dedicated egress endpoint metadata is malformed")
            try:
                inspected = inspect_container(identifier)
            except Exception:
                inspected = None
                errors.append("dedicated egress endpoint metadata could not be inspected")
            if inspected is None:
                errors.append("dedicated egress endpoint container is not inspectable")
            else:
                inspected_name = str(inspected.get("Name", "")).removeprefix("/")
                if endpoint_name and inspected_name != endpoint_name:
                    errors.append("dedicated egress endpoint name metadata drifted")
            try:
                stopped = stop_container_for_quarantine(identifier)
            except Exception:
                stopped = False
            if not stopped:
                errors.append("dedicated egress endpoint could not be stopped")
            try:
                disconnected = run(
                    [
                        "docker",
                        "network",
                        "disconnect",
                        "--force",
                        EGRESS_NETWORK,
                        identifier,
                    ],
                    check=False,
                )
            except Exception:
                disconnected = None
            if disconnected is None or disconnected.returncode != 0:
                errors.append("dedicated egress endpoint could not be disconnected")

    try:
        final_egress = network_by_name(EGRESS_NETWORK)
    except Exception:
        final_egress = None
        errors.append("dedicated egress network final attestation failed")
    if final_egress is not None:
        try:
            final_endpoints = endpoints(final_egress)
        except Exception:
            final_endpoints = {"metadata-error": {}}
        if final_endpoints:
            errors.append("dedicated egress network is not empty after bounded quarantine")
    if empty_scans < 2:
        errors.append("dedicated egress network did not remain empty across bounded rescans")
    if errors:
        unique_errors = list(dict.fromkeys(errors))
        fail("; ".join(unique_errors))


def quarantine_after_policy_failure(error: BaseException) -> NoReturn:
    try:
        quarantine_running_renderer()
    except POLICY_FAILURES as exc:
        raise PolicyError(f"network policy drift and renderer quarantine failed: {exc}") from error
    raise error


def policy_digest(rules: dict[str, dict[str, list[Rule]]]) -> str:
    serializable = {
        binary: {
            chain: [list(rule) for rule in chain_rules] for chain, chain_rules in chains.items()
        }
        for binary, chains in rules.items()
    }
    return hashlib.sha256(
        json.dumps(serializable, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def endpoints(network: dict[str, Any]) -> dict[str, Any]:
    observed = network.get("Containers")
    if observed is None:
        return {}
    if not isinstance(observed, dict):
        fail("Docker network endpoint inventory is malformed")
    return observed


def require_networks_stably_empty(*names: str) -> None:
    for _scan in range(2):
        for name in names:
            network = network_by_name(name)
            if network is not None and endpoints(network):
                fail(f"Docker network {name} is not empty")


def verify_exact_egress_endpoint(
    egress: dict[str, Any], renderer_id: str, inventory: Inventory
) -> None:
    verify_exact_network_endpoint(
        egress,
        renderer_id,
        address=inventory.egress_address,
        network=inventory.egress_network,
        role="egress",
    )


def verify_exact_network_endpoint(
    docker_network: dict[str, Any],
    renderer_id: str,
    *,
    address: str,
    network: str,
    role: str,
) -> None:
    observed = endpoints(docker_network)
    if set(observed) != {renderer_id}:
        fail(f"dedicated {role} endpoint inventory is not the exact renderer")
    endpoint = observed[renderer_id]
    prefix = ipaddress.ip_network(network).prefixlen
    if not isinstance(endpoint, dict) or (
        endpoint.get("Name") != CONTAINER or endpoint.get("IPv4Address") != f"{address}/{prefix}"
    ):
        fail(f"dedicated {role} endpoint identity drifted")


def ensure_policy_unchecked(inventory: Inventory) -> dict[str, object]:
    backend_preflight()
    internal = network_by_name(INTERNAL_NETWORK)
    egress = network_by_name(EGRESS_NETWORK)
    if internal is None or egress is None:
        fail("renderer networks must exist before firewall hooks are installed")
    internal_bridge, _ = expected_network(internal, inventory, role="renderer")
    egress_bridge, _ = expected_network(egress, inventory, role="egress")
    if egress_bridge != inventory.egress_bridge:
        fail("stable egress bridge identity drifted")
    rules = policy_rules(inventory, internal_bridge)
    renderer_id = exact_running_renderer_id(inventory)
    if endpoints(egress):
        if renderer_id is None:
            fail("refusing to mutate policy around an unexpected routed endpoint")
        verify_exact_egress_endpoint(egress, renderer_id, inventory)
        # Never repair a missing boundary around a live routed endpoint.
        return attest_policy(inventory, internal, egress, rules)
    if renderer_id is not None:
        fail("running renderer is absent from its dedicated egress network")
    require_networks_stably_empty(INTERNAL_NETWORK, EGRESS_NETWORK)
    try:
        return attest_policy(inventory, internal, egress, rules)
    except PolicyError:
        pass

    # Bootstrap and Docker-restart replay are monotonic: with both dedicated
    # networks empty, detach only our exact hooks, rebuild only our owned
    # chains, and publish hooks after every complete terminal-DROP chain.
    remove_owned_hooks()
    for binary, chains in rules.items():
        for chain, chain_rules in chains.items():
            require_networks_stably_empty(INTERNAL_NETWORK, EGRESS_NETWORK)
            replace_chain(binary, chain, chain_rules)
    require_networks_stably_empty(INTERNAL_NETWORK, EGRESS_NETWORK)
    for binary, hooks in HOOKS.items():
        for parent, target in hooks.items():
            ensure_hook(binary, parent, target)
    return attest_policy(inventory, internal, egress, rules)


def attest_policy(
    inventory: Inventory,
    internal: dict[str, Any],
    egress: dict[str, Any],
    rules: dict[str, dict[str, list[Rule]]],
) -> dict[str, object]:
    internal_bridge, _ = expected_network(internal, inventory, role="renderer")
    egress_bridge, _ = expected_network(egress, inventory, role="egress")
    for binary, chains in rules.items():
        for chain, expected in chains.items():
            if not chain_exists(binary, chain) or observed_rules(binary, chain) != expected:
                fail(f"owned firewall chain {chain} drifted")
    for binary, hooks in HOOKS.items():
        for parent, target in hooks.items():
            matches = hook_rules(binary, parent, target)
            if matches != [("-j", target)]:
                fail(f"owned firewall hook {parent}->{target} drifted")
            parent_rules = observed_rules(binary, parent)
            if parent_rules.index(("-j", target)) != 0:
                fail(f"owned firewall hook {parent}->{target} is not rule zero")
    verify_egress_routes(
        run_json(["ip", "-j", "route", "show", "table", "all"]),
        inventory,
        network_exists=True,
    )
    return {
        "schema_version": 1,
        "policy_sha256": policy_digest(rules),
        "internal_bridge": internal_bridge,
        "egress_bridge": egress_bridge,
        "internal_network_id": internal["Id"],
        "egress_network_id": egress["Id"],
    }


def verify_container(inventory: Inventory, attestation: dict[str, object]) -> None:
    inspects = run_json(["docker", "inspect", CONTAINER])
    if len(inspects) != 1:
        fail("renderer container identity is not exact")
    inspect = inspects[0]
    if inspect.get("State", {}).get("Running") is not True:
        fail("renderer container is not running")
    host = inspect.get("HostConfig") or {}
    expected_binding = {"9443/tcp": [{"HostIp": inventory.published_address, "HostPort": "9443"}]}
    if host.get("PortBindings") != expected_binding or host.get("Dns") != list(
        inventory.dns_resolvers
    ):
        fail("renderer published-port or DNS authority drifted")
    networks = inspect.get("NetworkSettings", {}).get("Networks") or {}
    if set(networks) != {INTERNAL_NETWORK, EGRESS_NETWORK}:
        fail("renderer network attachments drifted")
    if (
        networks[INTERNAL_NETWORK].get("IPAddress") != inventory.internal_address
        or networks[EGRESS_NETWORK].get("IPAddress") != inventory.egress_address
    ):
        fail("renderer endpoint address drifted")
    if inspect.get("NetworkSettings", {}).get("Ports") != expected_binding:
        fail("renderer runtime publication drifted")
    renderer_id = str(inspect.get("Id", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", renderer_id):
        fail("renderer container has an invalid Docker identity")
    internal = network_by_name(INTERNAL_NETWORK)
    egress = network_by_name(EGRESS_NETWORK)
    if internal is None or egress is None:
        fail("renderer networks disappeared during running attestation")
    verify_exact_network_endpoint(
        internal,
        renderer_id,
        address=inventory.internal_address,
        network=inventory.internal_network,
        role="internal",
    )
    verify_exact_egress_endpoint(egress, renderer_id, inventory)
    route_lines = run(
        ["docker", "exec", "--user", "10001:10001", CONTAINER, "cat", "/proc/net/route"]
    ).stdout.splitlines()[1:]
    defaults = [
        line.split()
        for line in route_lines
        if len(line.split()) >= 3 and line.split()[1] == "00000000"
    ]
    gateway_hex = "".join(
        reversed([f"{int(part):02X}" for part in inventory.egress_gateway.split(".")])
    )
    if len(defaults) != 1 or defaults[0][0] != "eth0" or defaults[0][2] != gateway_hex:
        fail("renderer default route does not use the fixed egress endpoint")
    ipv6_routes = run(
        ["docker", "exec", "--user", "10001:10001", CONTAINER, "cat", "/proc/net/ipv6_route"]
    ).stdout.splitlines()
    if any(
        len(line.split()) >= 10
        and line.split()[0] == "0" * 32
        and line.split()[1] == "00"
        and line.split()[-1] != "lo"
        for line in ipv6_routes
    ):
        fail("renderer unexpectedly has an IPv6 default route")

    nat_rules = observed_rules("iptables", "DOCKER", table="nat")
    expected_destination = f"{inventory.egress_address}:{inventory.published_port}"
    matches = [
        rule
        for rule in nat_rules
        if "--dport" in rule
        and rule[rule.index("--dport") + 1] == str(inventory.published_port)
        and "--to-destination" in rule
        and rule[rule.index("--to-destination") + 1] == expected_destination
        and "-d" in rule
        and rule[rule.index("-d") + 1] == inventory.host_private
    ]
    if len(matches) != 1:
        fail("Docker DNAT does not target the renderer egress endpoint exactly")
    current = verify_policy(inventory)
    if attestation["policy_sha256"] != current["policy_sha256"]:
        fail("network policy changed during running attestation")


def verify_policy(inventory: Inventory) -> dict[str, object]:
    backend_preflight()
    internal = network_by_name(INTERNAL_NETWORK)
    egress = network_by_name(EGRESS_NETWORK)
    if internal is None or egress is None:
        fail("renderer networks must exist for policy attestation")
    internal_bridge, _ = expected_network(internal, inventory, role="renderer")
    expected_network(egress, inventory, role="egress")
    return attest_policy(
        inventory,
        internal,
        egress,
        policy_rules(inventory, internal_bridge),
    )


def verify_release_binding(
    marker: dict[str, object],
    policy_digest: str,
    inventory_digest: str,
    expected_policy_sha256: str,
    expected_inventory_sha256: str,
) -> None:
    if (
        marker["policy_sha256"] != policy_digest
        or marker["inventory_sha256"] != inventory_digest
        or marker["policy_sha256"] != expected_policy_sha256
        or marker["inventory_sha256"] != expected_inventory_sha256
    ):
        fail("bootstrap completion marker artifact digest drifted")


def verify_bootstrap_marker(
    inventory_path: Path,
    expected_policy_sha256: str,
    expected_inventory_sha256: str,
) -> None:
    if inventory_path != INVENTORY_PATH:
        fail("bootstrap marker may attest only the fixed inventory path")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_policy_sha256) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_inventory_sha256
    ):
        fail("deployment supplied an invalid policy digest")
    marker_stat = BOOTSTRAP_MARKER.stat(follow_symlinks=False)
    if (
        not BOOTSTRAP_MARKER.is_file()
        or BOOTSTRAP_MARKER.is_symlink()
        or marker_stat.st_uid != 0
        or marker_stat.st_gid != 0
        or marker_stat.st_mode & 0o777 != 0o600
        or marker_stat.st_nlink != 1
    ):
        fail("bootstrap completion marker metadata drifted")
    payload = json.loads(BOOTSTRAP_MARKER.read_text(encoding="ascii"))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "source_commit",
        "policy_sha256",
        "inventory_sha256",
    }:
        fail("bootstrap completion marker schema drifted")
    if payload["schema_version"] != 1 or not re.fullmatch(
        r"[0-9a-f]{40}", str(payload["source_commit"])
    ):
        fail("bootstrap completion marker identity drifted")
    policy_digest = hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()
    inventory_digest = hashlib.sha256(INVENTORY_PATH.resolve().read_bytes()).hexdigest()
    verify_release_binding(
        payload,
        policy_digest,
        inventory_digest,
        expected_policy_sha256,
        expected_inventory_sha256,
    )


def bootstrap_policy(inventory: Inventory) -> dict[str, object]:
    backend_preflight()
    audit_candidate(inventory)
    internal, egress, created = create_networks(inventory)
    del internal, egress
    require_networks_stably_empty(INTERNAL_NETWORK, EGRESS_NETWORK)
    payload = ensure_policy_unchecked(inventory)
    payload["created_networks"] = sorted(created)
    return payload


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--inventory", type=Path, default=INVENTORY_PATH)
    result.add_argument(
        "command",
        choices=(
            "bootstrap",
            "ensure",
            "verify",
            "verify-running",
            "verify-ready",
            "verify-running-ready",
            "verify-empty",
            "quarantine",
        ),
    )
    result.add_argument("expected_policy_sha256", nargs="?")
    result.add_argument("expected_inventory_sha256", nargs="?")
    return result


def execute_guarded_command(args: argparse.Namespace) -> None:
    try:
        inventory = Inventory.load(args.inventory)
        expects_release_binding = args.command in {"verify-ready", "verify-running-ready"}
        if expects_release_binding:
            if args.expected_policy_sha256 is None or args.expected_inventory_sha256 is None:
                fail("deployment policy digests are required")
        elif args.expected_policy_sha256 is not None or args.expected_inventory_sha256 is not None:
            fail("policy digests are accepted only for deployment readiness")
        if args.command == "bootstrap":
            payload = bootstrap_policy(inventory)
        elif args.command == "ensure":
            payload = ensure_policy_unchecked(inventory)
        else:
            payload = verify_policy(inventory)
            if args.command in {"verify-ready", "verify-running-ready"}:
                verify_bootstrap_marker(
                    args.inventory,
                    cast(str, args.expected_policy_sha256),
                    cast(str, args.expected_inventory_sha256),
                )
            if args.command in {"verify-running", "verify-running-ready"}:
                verify_container(inventory, payload)
            elif args.command in {"verify-ready", "verify-empty"}:
                require_networks_stably_empty(INTERNAL_NETWORK, EGRESS_NETWORK)
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except POLICY_FAILURES as error:
        quarantine_after_policy_failure(error)


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "quarantine":
            if (
                args.expected_policy_sha256 is not None
                or args.expected_inventory_sha256 is not None
            ):
                fail("quarantine accepts no policy digests")
            quarantine_running_renderer()
        else:
            execute_guarded_command(args)
        return 0
    except (
        PolicyError,
        KeyError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"Lightpanda network policy failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
