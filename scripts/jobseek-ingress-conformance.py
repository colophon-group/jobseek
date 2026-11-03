#!/usr/bin/env python3
"""Audit the enforced Jobseek ingress and SSH baseline without printing addresses."""

from __future__ import annotations

import argparse
import hashlib
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
POSTGRES_HBA_PATH = "/var/lib/postgresql/data/pg_hba.conf"
POSTGRES_HBA_SOURCE = """# Managed by colophon-group/jobseek deploy/networking/harden-postgresql.sh.
# The Hetzner private path is isolated and source-restricted. TLS is not
# required by the current policy; SCRAM remains mandatory for every TCP path.
local all justup_feed_readonly reject
local all all trust
host crawler crawler 127.0.0.1/32 scram-sha-256
host crawler jobseek_labeller_readonly 127.0.0.1/32 scram-sha-256
host crawler crawler ::1/128 scram-sha-256
host crawler jobseek_labeller_readonly ::1/128 scram-sha-256
host crawler crawler {crawler_ip}/32 scram-sha-256
host crawler jobseek_labeller_readonly {crawler_ip}/32 scram-sha-256
# The Justup feed reader can connect only from the crawler host. It has no
# loopback or other host path and receives table grants separately.
host crawler justup_feed_readonly {crawler_ip}/32 scram-sha-256
"""


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


def _expected_postgres_hba_rules(crawler_ip: ipaddress.IPv4Address) -> list[dict[str, Any]]:
    # Match write_hba's complete policy, including precedence and singleton arrays.
    # Errors and authentication options must remain NULL; no parsed row is ignored.
    policy = [
        ("local", None, None, "all", "justup_feed_readonly", "reject"),
        ("local", None, None, "all", "all", "trust"),
        *[
            ("host", address, netmask, "crawler", user, "scram-sha-256")
            for address, netmask in (
                ("127.0.0.1", "255.255.255.255"),
                ("::1", "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"),
                (str(crawler_ip), "255.255.255.255"),
            )
            for user in ("crawler", "jobseek_labeller_readonly")
        ],
        (
            "host",
            str(crawler_ip),
            "255.255.255.255",
            "crawler",
            "justup_feed_readonly",
            "scram-sha-256",
        ),
    ]
    return [
        {
            "rule_number": number,
            "type": connection_type,
            "address": address,
            "netmask": netmask,
            "database": [database],
            "user_name": [user],
            "auth_method": method,
            "options": None,
            "error": None,
        }
        for number, (connection_type, address, netmask, database, user, method) in enumerate(
            policy, 1
        )
    ]


def _postgres_hba_exact(result: CommandResult, crawler_ip: ipaddress.IPv4Address) -> bool:
    if result.returncode != 0:
        return False
    try:
        inspected = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    if not isinstance(inspected, dict) or inspected.get("source_path_exact") is not True:
        return False
    actual = inspected.get("rules")
    if not isinstance(actual, list) or any(
        not isinstance(rule, dict) or type(rule.get("rule_number")) is not int for rule in actual
    ):
        return False

    expected = _expected_postgres_hba_rules(crawler_ip)
    # pg_hba_file_rules discards quoting, so parsed equality alone cannot prove
    # that the first database token is the wildcard all rather than literal "all".
    # Require the exact canonical source too; this excludes includes/substitutions.
    source = POSTGRES_HBA_SOURCE.format(crawler_ip=crawler_ip).encode("utf-8")
    return inspected == {
        "source_path_exact": True,
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "rules": expected,
    }


def _feed_hba_rule_diagnostics(
    result: CommandResult, crawler_ip: ipaddress.IPv4Address
) -> dict[str, Any]:
    """Classify the current file projection without exposing rows or addresses.

    pg_hba_file_rules describes the file on disk, not necessarily the policy
    last loaded by PostgreSQL. The local-socket probe below supplies separate
    evidence for the effective reject rule.
    """
    unknown: dict[str, Any] = {
        "source_exact": None,
        "parsed_first_local_reject": None,
        "parsed_second_local_trust": None,
        "parsed_feed_local_rule_count": "unproved",
        "parsed_feed_host_rule_count": "unproved",
        "parsed_feed_host_rule_at_nine": None,
        "parsed_errors_absent": None,
    }
    if result.returncode != 0 or len(result.stdout) > 65536:
        return unknown
    try:
        inspected = json.loads(result.stdout)
    except json.JSONDecodeError:
        return unknown
    if not isinstance(inspected, dict) or set(inspected) != {
        "source_path_exact",
        "source_sha256",
        "rules",
    }:
        return unknown
    rules = inspected["rules"]
    fields = {
        "rule_number",
        "type",
        "address",
        "netmask",
        "database",
        "user_name",
        "auth_method",
        "options",
        "error",
    }
    if (
        not isinstance(rules, list)
        or len(rules) > 1000
        or any(
            not isinstance(row, dict)
            or set(row) != fields
            or type(row["rule_number"]) is not int
            or not isinstance(row["user_name"], list)
            or not all(isinstance(user, str) for user in row["user_name"])
            for row in rules
        )
    ):
        return unknown
    source = POSTGRES_HBA_SOURCE.format(crawler_ip=crawler_ip).encode("utf-8")
    first, second, *_, feed_host = _expected_postgres_hba_rules(crawler_ip)

    def count_category(count: int) -> str:
        return "none" if count == 0 else "one" if count == 1 else "multiple"

    return {
        "source_exact": inspected["source_path_exact"] is True
        and inspected["source_sha256"] == hashlib.sha256(source).hexdigest(),
        "parsed_first_local_reject": any(row == first for row in rules),
        "parsed_second_local_trust": any(row == second for row in rules),
        "parsed_feed_local_rule_count": count_category(
            sum(
                row["type"] == "local" and "justup_feed_readonly" in row["user_name"]
                for row in rules
            )
        ),
        "parsed_feed_host_rule_count": count_category(
            sum(
                isinstance(row["type"], str)
                and row["type"].startswith("host")
                and "justup_feed_readonly" in row["user_name"]
                for row in rules
            )
        ),
        "parsed_feed_host_rule_at_nine": any(row == feed_host for row in rules),
        "parsed_errors_absent": all(row["error"] is None for row in rules),
    }


def _feed_hba_file_diagnostics(container: str) -> dict[str, bool | None]:
    """Read only fixed-path file/load categories; never return source text."""
    unknown = {
        "source_within_4096_bytes": None,
        "first_active_source_line_reject": None,
        "file_mtime_at_or_before_config_load": None,
    }
    query = (
        "select json_build_object("
        "'source_within_4096_bytes',(pg_stat_file('" + POSTGRES_HBA_PATH + "')).size<=4096,"
        "'first_active_source_line_reject',coalesce((select line="
        "'local all justup_feed_readonly reject' from regexp_split_to_table("
        "pg_read_file('"
        + POSTGRES_HBA_PATH
        + "',0,4096),E'\\n') with ordinality as lines(line,ordinal) "
        "where btrim(line)<>'' and left(ltrim(line),1)<>'#' "
        "order by ordinal limit 1),false),"
        "'file_mtime_at_or_before_config_load',coalesce((pg_stat_file('"
        + POSTGRES_HBA_PATH
        + "')).modification<=pg_conf_load_time(),false))"
    )
    try:
        inspected = _run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                "crawler",
                "-d",
                "crawler",
                "-X",
                "-w",
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                query,
            ]
        )
    except ConformanceError:
        return unknown
    if inspected.returncode != 0 or len(inspected.stdout) > 1024:
        return unknown
    try:
        values = json.loads(inspected.stdout)
    except json.JSONDecodeError:
        return unknown
    if (
        not isinstance(values, dict)
        or set(values) != set(unknown)
        or any(type(value) is not bool for value in values.values())
    ):
        return unknown
    return values


def _feed_local_socket_outcome(container: str) -> str:
    """Classify a passwordless local reject probe; discard all raw output."""
    try:
        probe = subprocess.run(
            [
                "docker",
                "exec",
                "-e",
                "PGPASSWORD=",
                "-e",
                "PGPASSFILE=/dev/null",
                container,
                "psql",
                "-h",
                "/var/run/postgresql",
                "-U",
                "justup_feed_readonly",
                "-d",
                "crawler",
                "-X",
                "-w",
                "-qAt",
                "-c",
                "SELECT 1",
            ],
            capture_output=True,
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unproved"
    if len(probe.stdout) > 1024 or len(probe.stderr) > 1024:
        return "unproved"
    if probe.returncode == 0:
        return "accepted_unsafe"
    if b"pg_hba.conf rejects connection" in probe.stderr and not probe.stdout:
        return "reject_observed"
    return "unproved"


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


def _metrics_listener_diagnostics(listener_map: dict[int, set[str]]) -> dict[str, str]:
    """Report only fixed port/scope categories, never listener addresses."""
    return {
        str(port): (
            "missing"
            if not listener_map.get(port)
            else "loopback_only"
            if listener_map[port] <= {"loopback"}
            else "includes_non_loopback"
        )
        for port in sorted(CRAWLER_METRICS_PORTS)
    }


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
            "-At",
            "-c",
            (
                # rule_number is the effective order, including rules from included files.
                # The fixed source is shorter than the read bound: any additional
                # bytes, quotes or includes change its digest. Never read a supplied path.
                "select json_build_object('source_path_exact',current_setting('hba_file')="
                "'/var/lib/postgresql/data/pg_hba.conf','source_sha256',encode(sha256("
                "pg_read_binary_file('/var/lib/postgresql/data/pg_hba.conf',0,4096)),'hex'),"
                "'rules',coalesce(json_agg(row_to_json(rules) order by rule_number nulls last),"
                "'[]'::json)) from (select rule_number,type,address,netmask,database,user_name,"
                "auth_method,options,error from pg_hba_file_rules) rules"
            ),
        ]
    )
    rules_match = _postgres_hba_exact(hba, crawler_ip)
    feed_diagnostics = {
        **_feed_hba_rule_diagnostics(hba, crawler_ip),
        **_feed_hba_file_diagnostics(container),
        "effective_local_socket": _feed_local_socket_outcome(container),
        # A current file view and its mtime do not prove the TCP rule was loaded.
        "effective_feed_host_rule": "unproved",
    }
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
        "feed_hba_diagnostics": feed_diagnostics,
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
    if role == "crawler" and listeners is not None:
        result["metrics_listener_diagnostics"] = _metrics_listener_diagnostics(listener_map)
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
