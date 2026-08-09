#!/usr/bin/env python3
"""Manage Jobseek's public Hetzner firewall without exposing infrastructure IDs."""

from __future__ import annotations

import argparse
import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_ROOT = "https://api.hetzner.cloud/v1"
FIREWALL_NAME = "jobseek-public-ingress"
OWNER_LABEL = "jobseek-ingress-baseline"
ROLE_ENV = {
    "crawler": "HETZNER_CRAWLER_HOST",
    "postgresql": "HETZNER_POSTGRES_HOST",
    "typesense": "HETZNER_TYPESENSE_HOST",
}
SENSITIVE_PUBLIC_PORTS = {
    "crawler": (6379, 8080, 9093, 9094, 9095, 9096, 9097, 9098, 12346, 12347),
    "postgresql": (5432, 12347),
    "typesense": (8108, 12347),
}


class IngressError(RuntimeError):
    """The desired firewall state could not be proven or applied safely."""


def _redacted_api_path(path: str) -> str:
    """Keep the API operation useful while withholding provider resource IDs."""
    return re.sub(r"/(?P<id>[0-9]+)(?=/|$)", "/{id}", path)


def _desired_rules() -> list[dict[str, Any]]:
    return [
        {
            "direction": "in",
            "protocol": "tcp",
            "port": "22",
            "source_ips": ["0.0.0.0/0", "::/0"],
            "description": "Jobseek key-only SSH",
        },
        {
            "direction": "in",
            "protocol": "icmp",
            "source_ips": ["0.0.0.0/0", "::/0"],
            "description": "Jobseek network diagnostics and path MTU",
        },
    ]


def _rule_signature(rule: dict[str, Any]) -> tuple[Any, ...]:
    return (
        rule.get("direction"),
        rule.get("protocol"),
        str(rule.get("port") or ""),
        tuple(sorted(rule.get("source_ips") or [])),
        tuple(sorted(rule.get("destination_ips") or [])),
        str(rule.get("description") or ""),
    )


def _rules_match(actual: list[dict[str, Any]], desired: list[dict[str, Any]]) -> bool:
    return sorted(map(_rule_signature, actual)) == sorted(map(_rule_signature, desired))


class HetznerClient:
    def __init__(self, token: str) -> None:
        if not token or any(char.isspace() for char in token):
            raise IngressError("invalid Hetzner API token")
        self._token = token
        self._context = ssl.create_default_context()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            API_ROOT + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=30, context=self._context
            ) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return {}
            raise IngressError(
                f"Hetzner {method} {_redacted_api_path(path)} returned HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IngressError(
                f"Hetzner {method} {_redacted_api_path(path)} failed"
            ) from exc
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise IngressError("Hetzner returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise IngressError("Hetzner returned an unexpected response")
        return parsed

    def wait_action(self, action: dict[str, Any] | None) -> None:
        if not action:
            return
        action_id = action.get("id")
        if not isinstance(action_id, int):
            raise IngressError("Hetzner response omitted an action ID")
        for _ in range(60):
            current = self.request("GET", f"/actions/{action_id}").get("action") or {}
            status = current.get("status")
            if status == "success":
                return
            if status == "error":
                raise IngressError("Hetzner firewall action failed")
            time.sleep(1)
        raise IngressError("Hetzner firewall action timed out")


def _host_addresses(host: str) -> set[str]:
    value = host.strip()
    if not value or any(char.isspace() for char in value):
        raise IngressError("invalid production host value")
    if value.startswith("[") and "]" in value:
        value = value[1 : value.index("]")]
    elif value.count(":") == 1 and value.rsplit(":", 1)[1].isdigit():
        value = value.rsplit(":", 1)[0]
    try:
        return {str(ipaddress.ip_address(value))}
    except ValueError:
        try:
            return {
                str(ipaddress.ip_address(item[4][0]))
                for item in socket.getaddrinfo(value, None, type=socket.SOCK_STREAM)
            }
        except socket.gaierror as exc:
            raise IngressError("production host did not resolve") from exc


def _server_public_addresses(server: dict[str, Any]) -> set[str]:
    public = server.get("public_net") or {}
    addresses = set()
    for family in ("ipv4", "ipv6"):
        raw = (public.get(family) or {}).get("ip")
        if raw:
            addresses.add(str(ipaddress.ip_address(str(raw).split("/")[0])))
    return addresses


def discover_targets(
    client: HetznerClient,
    role_hosts: dict[str, str],
) -> dict[str, dict[str, Any]]:
    servers = client.request("GET", "/servers?per_page=50").get("servers")
    if not isinstance(servers, list):
        raise IngressError("Hetzner server inventory is unavailable")
    targets: dict[str, dict[str, Any]] = {}
    used: set[int] = set()
    for role, host in role_hosts.items():
        expected = _host_addresses(host)
        matches = [
            server for server in servers if _server_public_addresses(server) & expected
        ]
        if len(matches) != 1:
            raise IngressError(f"expected exactly one {role} server")
        server = matches[0]
        server_id = server.get("id")
        if not isinstance(server_id, int) or server_id in used:
            raise IngressError("production host mapping is not one-to-one")
        if "murmur" in str(server.get("name") or "").lower():
            raise IngressError("refusing to include Murmur in the ingress baseline")
        used.add(server_id)
        targets[role] = server
    if set(targets) != set(ROLE_ENV):
        raise IngressError("all three non-Murmur roles are required")
    return targets


def _managed_firewall(client: HetznerClient) -> tuple[dict[str, Any] | None, int]:
    firewalls = client.request("GET", "/firewalls?per_page=50").get("firewalls")
    if not isinstance(firewalls, list):
        raise IngressError("Hetzner firewall inventory is unavailable")
    matches = [item for item in firewalls if item.get("name") == FIREWALL_NAME]
    if len(matches) > 1:
        raise IngressError("multiple managed firewalls have the same name")
    firewall = matches[0] if matches else None
    if (
        firewall is not None
        and (firewall.get("labels") or {}).get("owner") != OWNER_LABEL
    ):
        raise IngressError("refusing to modify an unowned firewall")
    return firewall, len(firewalls) - len(matches)


def _applied_server_ids(firewall: dict[str, Any]) -> set[int]:
    result = set()
    for resource in firewall.get("applied_to") or []:
        if resource.get("type") == "server" and isinstance(
            resource.get("server", {}).get("id"), int
        ):
            result.add(resource["server"]["id"])
    return result


def _summary(
    firewall: dict[str, Any] | None,
    targets: dict[str, dict[str, Any]],
    unrelated_count: int,
) -> dict[str, Any]:
    target_ids = {server["id"] for server in targets.values()}
    applied = _applied_server_ids(firewall or {})
    return {
        "managed_firewall_present": firewall is not None,
        "desired_rules_exact": bool(firewall)
        and _rules_match(firewall.get("rules") or [], _desired_rules()),
        "target_coverage_exact": bool(firewall) and applied == target_ids,
        "target_count": len(target_ids),
        "unrelated_firewall_count": unrelated_count,
    }


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def _write_private_inventory(path: Path, targets: dict[str, dict[str, Any]]) -> None:
    values = {}
    for role, server in targets.items():
        private = server.get("private_net") or []
        if len(private) != 1:
            raise IngressError(f"expected exactly one private network on {role}")
        raw = private[0].get("ip")
        try:
            address = ipaddress.ip_address(str(raw))
        except ValueError as exc:
            raise IngressError(f"invalid private address on {role}") from exc
        if not isinstance(address, ipaddress.IPv4Address) or not address.is_private:
            raise IngressError(f"{role} is missing private IPv4")
        values[role] = str(address)
    with path.open("a", encoding="utf-8") as output:
        for role, value in sorted(values.items()):
            if os.environ.get("GITHUB_ACTIONS") == "true":
                print(f"::add-mask::{value}")
            key_role = "POSTGRES" if role == "postgresql" else role.upper()
            output.write(f"JOBSEEK_{key_role}_PRIVATE_IP={value}\n")


def _probe_port(address: str, port: int) -> bool:
    try:
        with socket.create_connection((address, port), timeout=3):
            return True
    except OSError:
        return False


def verify_external(targets: dict[str, dict[str, Any]]) -> dict[str, Any]:
    checks: list[tuple[str, str, int, bool]] = []
    for role, server in targets.items():
        public = server.get("public_net") or {}
        address = (public.get("ipv4") or {}).get("ip")
        if not address:
            raise IngressError(f"{role} is missing public IPv4 for the external probe")
        checks.append((role, str(address), 22, True))
        checks.extend(
            (role, str(address), port, False) for port in SENSITIVE_PUBLIC_PORTS[role]
        )
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
        states = list(pool.map(lambda item: _probe_port(item[1], item[2]), checks))
    ssh_open = 0
    sensitive_open = []
    for (role, _address, port, expected_open), is_open in zip(
        checks, states, strict=True
    ):
        if port == 22 and is_open:
            ssh_open += 1
        elif not expected_open and is_open:
            sensitive_open.append({"role": role, "port": port})
    result = {
        "ssh_open_roles": ssh_open,
        "sensitive_open": sensitive_open,
        "compliant": ssh_open == len(targets) and not sensitive_open,
    }
    if not result["compliant"]:
        raise IngressError("external ingress verification failed")
    return result


def _set_rules(
    client: HetznerClient, firewall_id: int, rules: list[dict[str, Any]]
) -> None:
    response = client.request(
        "POST", f"/firewalls/{firewall_id}/actions/set_rules", body={"rules": rules}
    )
    client.wait_action(response.get("action"))


def _apply_server(client: HetznerClient, firewall_id: int, server_id: int) -> None:
    response = client.request(
        "POST",
        f"/firewalls/{firewall_id}/actions/apply_to_resources",
        body={"apply_to": [{"type": "server", "server": {"id": server_id}}]},
    )
    client.wait_action(response.get("action"))


def _remove_server(client: HetznerClient, firewall_id: int, server_id: int) -> None:
    response = client.request(
        "POST",
        f"/firewalls/{firewall_id}/actions/remove_from_resources",
        body={"remove_from": [{"type": "server", "server": {"id": server_id}}]},
        allow_not_found=True,
    )
    client.wait_action(response.get("action"))


def _refresh_firewall(client: HetznerClient, firewall_id: int) -> dict[str, Any]:
    firewall = client.request("GET", f"/firewalls/{firewall_id}").get("firewall")
    if not isinstance(firewall, dict):
        raise IngressError("managed firewall disappeared")
    return firewall


def apply(
    client: HetznerClient,
    targets: dict[str, dict[str, Any]],
    state_path: Path,
) -> dict[str, Any]:
    firewall, unrelated_count = _managed_firewall(client)
    previous = {
        "existed": firewall is not None,
        "rules": (firewall or {}).get("rules") or [],
        "applied_server_ids": sorted(_applied_server_ids(firewall or {})),
    }
    _write_state(state_path, previous)
    target_ids = {server["id"] for server in targets.values()}
    try:
        if firewall is None:
            response = client.request(
                "POST",
                "/firewalls",
                body={
                    "name": FIREWALL_NAME,
                    "labels": {"owner": OWNER_LABEL, "environment": "production"},
                    "rules": _desired_rules(),
                },
            )
            firewall = response.get("firewall")
            if not isinstance(firewall, dict) or not isinstance(
                firewall.get("id"), int
            ):
                raise IngressError("Hetzner did not return the created firewall")
            for action in response.get("actions") or []:
                client.wait_action(action)
        else:
            _set_rules(client, firewall["id"], _desired_rules())
        firewall_id = firewall["id"]
        current = _refresh_firewall(client, firewall_id)
        for server_id in sorted(target_ids - _applied_server_ids(current)):
            _apply_server(client, firewall_id, server_id)
        current = _refresh_firewall(client, firewall_id)
        for server_id in sorted(_applied_server_ids(current) - target_ids):
            _remove_server(client, firewall_id, server_id)
        current = _refresh_firewall(client, firewall_id)
        summary = _summary(current, targets, unrelated_count)
        if not summary["desired_rules_exact"] or not summary["target_coverage_exact"]:
            raise IngressError("managed firewall did not converge")
        return summary
    except Exception:
        rollback(client, state_path)
        raise


def rollback(client: HetznerClient, state_path: Path) -> None:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngressError("rollback state is unavailable") from exc
    firewall, _ = _managed_firewall(client)
    if firewall is None:
        if state.get("existed"):
            raise IngressError("cannot restore a missing pre-existing firewall")
        return
    firewall_id = firewall["id"]
    if not state.get("existed"):
        current = _refresh_firewall(client, firewall_id)
        for server_id in sorted(_applied_server_ids(current)):
            _remove_server(client, firewall_id, server_id)
        response = client.request("DELETE", f"/firewalls/{firewall_id}")
        client.wait_action(response.get("action"))
        return
    _set_rules(client, firewall_id, state.get("rules") or [])
    expected = {int(item) for item in state.get("applied_server_ids") or []}
    current = _refresh_firewall(client, firewall_id)
    for server_id in sorted(expected - _applied_server_ids(current)):
        _apply_server(client, firewall_id, server_id)
    current = _refresh_firewall(client, firewall_id)
    for server_id in sorted(_applied_server_ids(current) - expected):
        _remove_server(client, firewall_id, server_id)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("audit", "apply", "rollback", "verify"))
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--github-env", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    token = os.environ.get("HETZNER_API_TOKEN", "")
    client = HetznerClient(token)
    if args.mode == "rollback":
        rollback(client, args.state)
        print(json.dumps({"rollback": "complete"}))
        return 0
    role_hosts = {role: os.environ.get(name, "") for role, name in ROLE_ENV.items()}
    targets = discover_targets(client, role_hosts)
    if args.github_env:
        _write_private_inventory(args.github_env, targets)
    if args.mode == "apply":
        summary = apply(client, targets, args.state)
    elif args.mode == "verify":
        firewall, unrelated = _managed_firewall(client)
        summary = _summary(firewall, targets, unrelated)
        if not summary["desired_rules_exact"] or not summary["target_coverage_exact"]:
            raise IngressError("managed firewall is not exact")
        summary["external"] = verify_external(targets)
    else:
        firewall, unrelated = _managed_firewall(client)
        summary = _summary(firewall, targets, unrelated)
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
