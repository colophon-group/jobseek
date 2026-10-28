#!/usr/bin/env python3
"""Audit the enforced Jobseek ingress and SSH baseline without printing addresses."""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
import ssl
import stat
import struct
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KNOWN_PORTS = {
    22,
    5432,
    5433,
    6379,
    8108,
    8080,
    9093,
    9094,
    9095,
    9096,
    9097,
    9098,
    12346,
    12347,
}
CRAWLER_METRICS_PORTS = {9093, 9094, 9095, 9096, 9097, 9098}
POSTGRES_NETWORK_CONFIG = Path("/etc/jobseek-ingress/postgresql-network.env")
WEB_POSTGRES_CONFIG = Path("/etc/jobseek-web-postgresql/postgresql.conf")
POSTGRES_SHM_MIN_BYTES = 1024 * 1024 * 1024
POSTGRES_LOG_DRIVER = "json-file"
POSTGRES_LOG_OPTIONS = {"max-size": "50m", "max-file": "3"}


class ConformanceError(RuntimeError):
    """The host state could not be inspected safely."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str


def _run(command: Sequence[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConformanceError(f"required command failed: {command[0]}") from exc
    return CommandResult(completed.returncode, completed.stdout)


def _private_address(value: str) -> ipaddress.IPv4Address:
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ConformanceError("crawler private address is invalid") from exc
    if not isinstance(parsed, ipaddress.IPv4Address) or not parsed.is_private:
        raise ConformanceError("crawler address must be private IPv4")
    return parsed


def _address_scope(value: str) -> str:
    address = value.strip("[]").split("%")[0]
    if address in {"0.0.0.0", "::", "*"}:
        return "wildcard"
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return "other"
    if parsed.is_loopback:
        return "loopback"
    if parsed.is_private:
        return "private"
    return "public"


def _network_from_address_mask(
    address: str, netmask: str
) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    parsed_address = ipaddress.ip_address(address)
    parsed_mask = ipaddress.ip_address(netmask)
    if parsed_address.version != parsed_mask.version:
        raise ValueError("address and netmask families differ")
    bits = f"{int(parsed_mask):0{parsed_mask.max_prefixlen}b}"
    if "01" in bits:
        raise ValueError("non-contiguous netmask")
    prefix = bits.count("1")
    return ipaddress.ip_network(f"{parsed_address}/{prefix}", strict=False)


def _listener_scopes(output: str) -> dict[int, set[str]]:
    result: dict[int, set[str]] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        address, separator, raw_port = fields[3].rpartition(":")
        if not separator or not raw_port.isdecimal():
            continue
        port = int(raw_port)
        if port not in KNOWN_PORTS:
            continue
        result.setdefault(port, set()).add(_address_scope(address))
    return result


def _sshd_state(output: str) -> dict[str, Any]:
    values: dict[str, list[str]] = {}
    for line in output.splitlines():
        key, separator, value = line.partition(" ")
        if separator:
            values.setdefault(key, []).append(value.strip())
    allow_users = {user for directive in values.get("allowusers", []) for user in directive.split()}
    exact_values = {
        "authenticationmethods": "publickey",
        "passwordauthentication": "no",
        "kbdinteractiveauthentication": "no",
        "pubkeyauthentication": "yes",
        "disableforwarding": "yes",
        "gatewayports": "no",
        "permittunnel": "no",
        "permituserenvironment": "no",
        "x11forwarding": "no",
    }
    settings_match = all(
        values.get(key) == [value] for key, value in exact_values.items()
    ) and values.get("permitrootlogin") in (
        ["without-password"],
        ["prohibit-password"],
    )
    allowlist_match = (
        bool(allow_users)
        and "root" in allow_users
        and allow_users
        <= {
            "root",
            "deploy",
        }
    )
    return {
        "key_only": settings_match,
        "explicit_user_allowlist": allowlist_match,
        "compliant": settings_match and allowlist_match,
    }


def _ufw_state(
    output: str,
    added: str,
    role: str,
    crawler_ip: ipaddress.IPv4Address,
    *,
    web_postgresql: bool = False,
) -> dict[str, Any]:
    lines = [line.strip().lower() for line in output.splitlines()]
    active = "status: active" in lines
    defaults = any(
        line.startswith("default:") and "deny (incoming)" in line and "allow (outgoing)" in line
        for line in lines
    )
    commands = [
        normalized
        for line in added.splitlines()
        if (normalized := " ".join(line.lower().split())).startswith("ufw ")
    ]
    ssh_allowed = any(command.startswith("ufw allow 22/tcp") for command in commands)
    service_allowed = True
    expected_commands = 1
    if role == "postgresql":
        service_allowed = any(
            command.startswith(f"ufw allow from {crawler_ip} to any port 5432 proto tcp")
            for command in commands
        )
        expected_commands = 2
        if web_postgresql:
            service_allowed = service_allowed and any(
                command.startswith("ufw allow 5433/tcp") for command in commands
            )
            expected_commands += 1
    elif role == "typesense":
        service_allowed = any(
            command.startswith(f"ufw allow from {crawler_ip} to any port 8108 proto tcp")
            for command in commands
        )
        expected_commands = 2
    exact_rules = len(commands) == expected_commands
    return {
        "active": active,
        "default_deny_incoming": defaults,
        "ssh_allowed": ssh_allowed,
        "required_private_service_allowed": service_allowed,
        "exact_managed_rules": exact_rules,
        "compliant": active and defaults and ssh_allowed and service_allowed and exact_rules,
    }


def _postgres_container() -> str | None:
    # The separate web instance must never be mistaken for crawler truth.
    result = _run(["docker", "inspect", "--format", "{{.State.Running}}", "postgres"])
    return "postgres" if result.returncode == 0 and result.stdout.strip() == "true" else None


def _web_postgresql_configured() -> bool:
    if not WEB_POSTGRES_CONFIG.exists():
        return False
    metadata = WEB_POSTGRES_CONFIG.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o444
    ):
        raise ConformanceError("web PostgreSQL configuration is not protected")
    return True


def _web_postgresql_state() -> dict[str, Any]:
    query = """SELECT current_setting('ssl')='on' AND current_setting('port')='5433'
        AND current_setting('password_encryption')='scram-sha-256'
        AND current_setting('ssl_min_protocol_version') IN ('TLSv1.2','TLSv1.3')
        AND NOT EXISTS(SELECT FROM pg_hba_file_rules WHERE error IS NOT NULL OR NOT (
          auth_method='reject' OR
          (type='local' AND user_name=ARRAY['postgres'] AND auth_method='trust') OR
          (type='hostssl' AND auth_method='scram-sha-256' AND user_name <@
            ARRAY['jobseek_web_owner','jobseek_migration_auditor','jobseek_narrowed_worker']::text[])))
        AND EXISTS(SELECT FROM pg_hba_file_rules WHERE type='hostssl'
          AND user_name=ARRAY['jobseek_web_owner'])
        AND EXISTS(SELECT FROM pg_hba_file_rules WHERE type='hostssl'
          AND user_name=ARRAY['jobseek_migration_auditor'])"""
    result = _run(
        [
            "docker",
            "exec",
            "--user",
            "70:70",
            "jobseek-web-postgresql",
            "psql",
            "-X",
            "-w",
            "-A",
            "-t",
            "-p",
            "5433",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-c",
            query,
        ]
    )
    tls = False
    try:
        context = ssl.create_default_context()
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with socket.create_connection(("127.0.0.1", 5433), timeout=3) as connection:
            connection.sendall(struct.pack("!II", 8, 80877103))
            if connection.recv(1) == b"S":
                with context.wrap_socket(connection, server_hostname="web-postgres.jseek.co"):
                    tls = True
    except (OSError, ssl.SSLError):
        pass
    authentication = result.returncode == 0 and result.stdout.strip() == "t"
    return {
        "tls_certificate_verified": tls,
        "tls_scram_roles_exact": authentication,
        "compliant": tls and authentication,
    }


def _postgres_state(container: str, crawler_ip: ipaddress.IPv4Address) -> dict[str, Any]:
    shared_memory = _run(["docker", "inspect", "--format", "{{.HostConfig.ShmSize}}", container])
    log_config_result = _run(
        ["docker", "inspect", "--format", "{{json .HostConfig.LogConfig}}", container]
    )
    shared_memory_mount = _run(["docker", "exec", container, "df", "-B1", "/dev/shm"])
    try:
        shared_memory_bytes = int(shared_memory.stdout.strip())
        shared_memory_capacity_bytes = int(shared_memory_mount.stdout.splitlines()[-1].split()[1])
    except (IndexError, ValueError):
        shared_memory_bytes = 0
        shared_memory_capacity_bytes = 0
    shared_memory_match = (
        shared_memory.returncode == 0
        and shared_memory_mount.returncode == 0
        and shared_memory_bytes >= POSTGRES_SHM_MIN_BYTES
        and shared_memory_capacity_bytes >= POSTGRES_SHM_MIN_BYTES
    )
    try:
        log_config = json.loads(log_config_result.stdout)
    except json.JSONDecodeError:
        log_config = {}
    bounded_log_match = (
        log_config_result.returncode == 0
        and isinstance(log_config, dict)
        and log_config.get("Type") == POSTGRES_LOG_DRIVER
        and log_config.get("Config") == POSTGRES_LOG_OPTIONS
    )
    settings = _run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            "crawler",
            "-d",
            "crawler",
            "-AtF",
            "|",
            "-c",
            (
                "select name,setting from pg_settings where name in "
                "('listen_addresses','password_encryption','ssl') order by name"
            ),
        ]
    )
    if settings.returncode != 0:
        return {
            "readable": False,
            "shared_memory_bytes": shared_memory_bytes,
            "shared_memory_capacity_bytes": shared_memory_capacity_bytes,
            "shared_memory_contract": shared_memory_match,
            "bounded_container_log": bounded_log_match,
            "compliant": False,
        }
    parsed_settings = dict(
        line.split("|", 1) for line in settings.stdout.splitlines() if "|" in line
    )
    listeners = {item.strip() for item in parsed_settings.get("listen_addresses", "").split(",")}
    private_listeners = set()
    for item in listeners - {"127.0.0.1"}:
        try:
            address = ipaddress.ip_address(item)
        except ValueError:
            continue
        if isinstance(address, ipaddress.IPv4Address) and address.is_private:
            private_listeners.add(address)
    listen_match = "127.0.0.1" in listeners and len(listeners) == 2 and len(private_listeners) == 1
    config_match = False
    try:
        config_value = POSTGRES_NETWORK_CONFIG.read_text(encoding="utf-8").strip()
        stat = POSTGRES_NETWORK_CONFIG.stat()
        prefix = "JOBSEEK_POSTGRES_LISTEN_ADDRESSES="
        config_match = (
            config_value.startswith(prefix)
            and set(config_value.removeprefix(prefix).split(",")) == listeners
            and stat.st_uid == 0
            and stat.st_gid == 0
            and stat.st_mode & 0o777 == 0o600
        )
    except OSError:
        pass
    encryption_match = parsed_settings.get("password_encryption") == "scram-sha-256"

    hba = _run(
        [
            "docker",
            "exec",
            container,
            "psql",
            "-U",
            "crawler",
            "-d",
            "crawler",
            "-AtF",
            "|",
            "-c",
            (
                "select type,coalesce(address,''),coalesce(netmask,''),database[1],user_name[1],"
                "auth_method from pg_hba_file_rules where error is null order by line_number"
            ),
        ]
    )
    rules_match = hba.returncode == 0
    allowed_users = {"crawler", "jobseek_labeller_readonly"}
    expected_network_rules = {
        (str(network), "crawler", user, "scram-sha-256")
        for network in (
            ipaddress.ip_network("127.0.0.1/32"),
            ipaddress.ip_network("::1/128"),
            ipaddress.ip_network(f"{crawler_ip}/32"),
        )
        for user in allowed_users
    }
    actual_network_rules = []
    local_rules = []
    if rules_match:
        for line in hba.stdout.splitlines():
            parts = line.split("|", 5)
            if len(parts) != 6:
                rules_match = False
                break
            connection_type, address, netmask, database, user, method = parts
            if connection_type == "local":
                local_rules.append((database, user, method))
                continue
            if connection_type != "host":
                rules_match = False
                continue
            try:
                network = _network_from_address_mask(address, netmask)
            except ValueError:
                rules_match = False
                continue
            actual_network_rules.append((str(network), database, user, method))
    rules_match = (
        rules_match
        and local_rules == [("all", "all", "trust")]
        and len(actual_network_rules) == len(expected_network_rules)
        and set(actual_network_rules) == expected_network_rules
    )
    return {
        "readable": True,
        "shared_memory_bytes": shared_memory_bytes,
        "shared_memory_capacity_bytes": shared_memory_capacity_bytes,
        "shared_memory_contract": shared_memory_match,
        "bounded_container_log": bounded_log_match,
        "private_and_loopback_bind_only": listen_match,
        "repo_owned_listener_policy": config_match,
        "scram_password_encryption": encryption_match,
        "tls_required_by_policy": False,
        "hba_exact": rules_match,
        "compliant": (
            shared_memory_match
            and bounded_log_match
            and listen_match
            and config_match
            and encryption_match
            and rules_match
        ),
    }


def audit(role: str, crawler_private_ip: str, *, host_only: bool = False) -> dict[str, Any]:
    crawler_ip = _private_address(crawler_private_ip)
    web = role == "postgresql" and _web_postgresql_configured()
    ufw_status = _run(["ufw", "status", "verbose"])
    ufw_added = _run(["ufw", "show", "added"])
    sshd = _run(["sshd", "-T"])
    commands = [ufw_status, ufw_added, sshd]
    listeners = None
    if not host_only:
        listeners = _run(["ss", "-H", "-lnt"])
        commands.append(listeners)
    if any(item.returncode != 0 for item in commands):
        raise ConformanceError("host policy commands are unavailable")
    listener_policy = None
    if listeners is not None:
        listener_map = _listener_scopes(listeners.stdout)
        listener_policy = True
        if role == "crawler":
            listener_policy = all(
                bool(listener_map.get(port)) and listener_map[port] <= {"loopback"}
                for port in CRAWLER_METRICS_PORTS
            )
        elif role == "postgresql":
            listener_policy = bool(listener_map.get(5432)) and listener_map[5432] <= {
                "loopback",
                "private",
            }
    root_status = _run(["passwd", "-S", "root"])
    root_parts = root_status.stdout.split()
    root_locked = (
        root_status.returncode == 0
        and len(root_parts) >= 2
        and root_parts[1]
        in {
            "L",
            "LK",
        }
    )
    result: dict[str, Any] = {
        "role": role,
        "scope": "host" if host_only else "full",
        "ufw": _ufw_state(
            ufw_status.stdout, ufw_added.stdout, role, crawler_ip, web_postgresql=web
        ),
        "sshd": _sshd_state(sshd.stdout),
        "required_listener_scope": listener_policy,
        "root_password_locked": root_locked if role == "postgresql" else None,
    }
    if role == "postgresql" and not host_only:
        container = _postgres_container()
        result["postgresql"] = (
            _postgres_state(container, crawler_ip)
            if container
            else {"readable": False, "compliant": False}
        )
        if web:
            result["web_postgresql"] = _web_postgresql_state()
    result["compliant"] = (
        result["ufw"]["compliant"]
        and result["sshd"]["compliant"]
        and (host_only or listener_policy)
        and (role != "postgresql" or root_locked)
        and (host_only or role != "postgresql" or result["postgresql"]["compliant"])
        and (host_only or not web or result["web_postgresql"]["compliant"])
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=("crawler", "postgresql", "typesense"))
    parser.add_argument("--crawler-private-ip", required=True)
    parser.add_argument(
        "--host-only",
        action="store_true",
        help="require only the SSH, UFW, and PostgreSQL root-lock host baseline",
    )
    parser.add_argument("--require-enforced", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = audit(args.role, args.crawler_private_ip, host_only=args.host_only)
    print(json.dumps(result, sort_keys=True))
    if args.require_enforced and not result["compliant"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
