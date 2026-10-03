"""Freeze the deployed Python SSRF address policy, including stdlib exceptions."""

from __future__ import annotations

import ipaddress
import json
import socket
from pathlib import Path

from src.shared.ssrf import SSRFError, _public_address_from_infos, is_private_ip


def prefixes(constants):
    blocked = [
        *constants._private_networks,
        constants._multicast_network,
        constants._linklocal_network,
    ]
    reserved = getattr(constants, "_reserved_networks", None)
    if reserved is None:
        reserved = [constants._reserved_network]
    return {
        "private": [str(value) for value in constants._private_networks],
        "exceptions": [str(value) for value in constants._private_networks_exceptions],
        "always_blocked": [
            str(value)
            for value in [*reserved, constants._multicast_network, constants._linklocal_network]
        ],
        "boundaries": blocked + reserved + constants._private_networks_exceptions,
    }


v4, v6 = prefixes(ipaddress._IPv4Constants), prefixes(ipaddress._IPv6Constants)
samples = {
    "bad",
    "8.8.8.8",
    "1.1.1.1",
    "100.64.0.1",
    "2606:4700:4700::1111",
    "::ffff:8.8.8.8",
    "::ffff:10.0.0.1",
    "fec0::1",
    "fe80::1%en0",
}
for group in [v4, v6]:
    for network in group.pop("boundaries"):
        for address in [network.network_address, network.broadcast_address]:
            samples.add(str(address))
            for delta in [-1, 1]:
                value = int(address) + delta
                if 0 <= value < 1 << network.max_prefixlen:
                    samples.add(str(type(address)(value)))


def dns_case(name, addresses):
    infos = []
    for raw, zone in addresses:
        addr = ipaddress.ip_address(raw)
        infos.append(
            (
                socket.AF_INET6 if addr.version == 6 else socket.AF_INET,
                socket.SOCK_STREAM,
                6,
                "",
                (raw, 443, 0, zone) if addr.version == 6 else (raw, 443),
            )
        )
    result = {
        "name": name,
        "addresses": [{"ip": raw, "zone": str(zone) if zone else ""} for raw, zone in addresses],
    }
    try:
        result["first"] = _public_address_from_infos(
            "https://fixture.invalid", "fixture.invalid", infos
        )
        result["blocked"] = False
    except SSRFError:
        result.update(first="", blocked=True)
    return result


captured = {
    "literals": [{"ip": value, "blocked": is_private_ip(value)} for value in sorted(samples)],
    "dns": [
        dns_case("empty", []),
        dns_case("public-v4-v6", [("8.8.8.8", 0), ("2606:4700:4700::1111", 0)]),
        dns_case("public-private-mix", [("8.8.8.8", 0), ("10.0.0.1", 0)]),
        dns_case("public-unscoped-linklocal", [("fe80::1", 0), ("8.8.8.8", 0)]),
        dns_case("unscoped-linklocal-alone", [("fe80::1", 0)]),
        dns_case("public-scoped-linklocal", [("8.8.8.8", 0), ("fe80::1", 2)]),
        dns_case("mapped-public-reclassifies-v4", [("::ffff:8.8.8.8", 0)]),
        dns_case("private-v6", [("2606:4700:4700::1111", 0), ("fd00::1", 0)]),
        dns_case("public-v4-exceptions", [("192.0.0.9", 0), ("192.0.0.10", 0)]),
    ],
}
root = Path(__file__).parent
(root.parent / "ssrf_networks.json").write_text(json.dumps({"v4": v4, "v6": v6}, indent=2) + "\n")
(root / "python_ssrf.json").write_text(json.dumps(captured, indent=2) + "\n")
