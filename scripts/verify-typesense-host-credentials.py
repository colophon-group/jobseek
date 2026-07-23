#!/usr/bin/env python3
"""Verify that live Typesense-host credentials cannot be recovered from metadata."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

TYPESENSE_CONFIG = Path("/etc/jobseek-typesense/typesense-server.ini")
CLOUDFLARED_TOKEN = Path("/etc/jobseek-typesense/cloudflare-tunnel-token")
CLOUDFLARED_UNIT = Path("/etc/systemd/system/cloudflared.service")
TYPESENSE_CONFIG_IN_CONTAINER = "/run/secrets/typesense-server.ini"
FORBIDDEN_TYPESENSE_ENV = {
    "TYPESENSE_ADMIN_KEY",
    "TYPESENSE_API_KEY",
    "TYPESENSE_BOOTSTRAP_KEY",
    "TYPESENSE_OPERATIONS_KEY",
}


def has_inline_flag(argv: Sequence[str], flag: str) -> bool:
    """Return whether argv embeds or follows the exact secret-bearing flag."""
    return any(argument == flag or argument.startswith(f"{flag}=") for argument in argv)


def _run(
    argv: Sequence[str],
    *,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        check=check,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _read_proc_argv(pid: int) -> list[str]:
    return [
        part.decode("utf-8", errors="replace")
        for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        if part
    ]


def _http_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url)
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError(f"{url} did not return a JSON object")
    return payload


def collect_typesense_checks() -> dict[str, bool]:
    inspect = json.loads(_run(["docker", "inspect", "typesense"]).stdout)[0]
    container_argv = inspect["Config"].get("Cmd") or []
    container_env = inspect["Config"].get("Env") or []
    mounts = inspect.get("Mounts") or []
    typesense_pid = int(inspect["State"]["Pid"])
    typesense_argv = _read_proc_argv(typesense_pid)

    nobody_config = _run(
        ["runuser", "-u", "nobody", "--", "test", "-r", str(TYPESENSE_CONFIG)],
        check=False,
    )
    nobody_docker = _run(
        ["runuser", "-u", "nobody", "--", "docker", "inspect", "typesense"],
        check=False,
    )

    config_stat = TYPESENSE_CONFIG.stat()
    mounted_config = any(
        mount.get("Source") == str(TYPESENSE_CONFIG)
        and mount.get("Destination") == TYPESENSE_CONFIG_IN_CONTAINER
        and mount.get("RW") is False
        for mount in mounts
    )
    environment_names = {value.split("=", 1)[0] for value in container_env}
    health = _http_json("http://127.0.0.1:8108/health")

    return {
        "typesense_running": inspect["State"].get("Running") is True,
        "typesense_healthy": health.get("ok") is True,
        "typesense_restart_count_zero": int(inspect.get("RestartCount") or 0) == 0,
        "typesense_not_oom_killed": inspect["State"].get("OOMKilled") is False,
        "typesense_image_pinned": inspect["Config"].get("Image") == "typesense/typesense:27.1",
        "typesense_host_network": inspect["HostConfig"].get("NetworkMode") == "host",
        "typesense_config_only_argv": container_argv
        == [f"--config={TYPESENSE_CONFIG_IN_CONTAINER}"],
        "typesense_process_has_no_inline_api_key": not has_inline_flag(typesense_argv, "--api-key"),
        "typesense_inspect_has_no_secret_env": not (environment_names & FORBIDDEN_TYPESENSE_ENV),
        "typesense_config_mounted_read_only": mounted_config,
        "typesense_config_root_owned_0600": config_stat.st_uid == 0
        and config_stat.st_gid == 0
        and _mode(TYPESENSE_CONFIG) == 0o600,
        "typesense_config_denied_to_nobody": nobody_config.returncode != 0,
        "docker_inspect_denied_to_nobody": nobody_docker.returncode != 0,
    }


def collect_cloudflared_checks() -> dict[str, bool]:
    cloudflared_pid = int(
        _run(
            ["systemctl", "show", "cloudflared.service", "-p", "MainPID", "--value"]
        ).stdout.strip()
    )
    cloudflared_argv = _read_proc_argv(cloudflared_pid)
    unit_text = _run(["systemctl", "cat", "cloudflared.service"]).stdout
    unit_properties = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in _run(
            [
                "systemctl",
                "show",
                "cloudflared.service",
                "-p",
                "User",
                "-p",
                "Group",
                "-p",
                "NoNewPrivileges",
                "-p",
                "ProtectSystem",
            ]
        ).stdout.splitlines()
        if "=" in line
    }
    nobody_token = _run(
        ["runuser", "-u", "nobody", "--", "test", "-r", str(CLOUDFLARED_TOKEN)],
        check=False,
    )
    token_stat = CLOUDFLARED_TOKEN.stat()

    return {
        "cloudflared_active": _run(
            ["systemctl", "is-active", "cloudflared.service"], check=False
        ).returncode
        == 0,
        "cloudflared_process_has_no_inline_token": not has_inline_flag(cloudflared_argv, "--token"),
        "cloudflared_unit_has_no_inline_token": "--token " not in unit_text
        and "--token=" not in unit_text,
        "cloudflared_uses_systemd_credential": (
            f"LoadCredential=cloudflare-tunnel-token:{CLOUDFLARED_TOKEN}" in unit_text
            and ("/run/credentials/cloudflared.service/cloudflare-tunnel-token" in cloudflared_argv)
        ),
        "cloudflared_runs_unprivileged": unit_properties.get("User") == "cloudflared"
        and unit_properties.get("Group") == "cloudflared",
        "cloudflared_no_new_privileges": unit_properties.get("NoNewPrivileges") == "yes",
        "cloudflared_protect_system_strict": unit_properties.get("ProtectSystem") == "strict",
        "cloudflared_token_root_owned_0600": token_stat.st_uid == 0
        and token_stat.st_gid == 0
        and _mode(CLOUDFLARED_TOKEN) == 0o600,
        "cloudflared_token_denied_to_nobody": nobody_token.returncode != 0,
    }


def collect_checks(component: str) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    if component in {"all", "typesense"}:
        checks.update(collect_typesense_checks())
    if component in {"all", "cloudflared"}:
        checks.update(collect_cloudflared_checks())
    return checks


def self_test() -> None:
    assert has_inline_flag(["server", "--api-key", "secret"], "--api-key")
    assert has_inline_flag(["server", "--api-key=secret"], "--api-key")
    assert not has_inline_flag(["server", "--config=/run/secret.ini"], "--api-key")
    assert has_inline_flag(["cloudflared", "--token", "secret"], "--token")
    assert has_inline_flag(["cloudflared", "--token=secret"], "--token")
    assert not has_inline_flag(["cloudflared", "--token-file", "/run/credential"], "--token")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--component",
        choices=("all", "typesense", "cloudflared"),
        default="all",
    )
    args = parser.parse_args()
    if args.self_test:
        self_test()
        print("Typesense host credential conformance self-test passed")
        return 0

    if os.geteuid() != 0:
        raise SystemExit("credential conformance must run as root")
    checks = collect_checks(args.component)
    print(json.dumps({"schema_version": 1, "checks": checks}, sort_keys=True))
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        print(
            f"credential conformance failed: {', '.join(failed)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
