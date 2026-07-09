#!/usr/bin/env python3
"""Collect a redacted read-only evidence bundle for daily error review.

This script is intended to run as root from systemd ``ExecStartPre``. It
collects host signals and Docker logs, redacts common credential shapes, and
writes files readable by the unprivileged ``codex-runner`` account.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

LONG_RUNNING_CONTAINERS = (
    "deploy-worker-1-1",
    "deploy-worker-2-1",
    "deploy-worker-3-1",
    "deploy-browser-1-1",
    "deploy-exporter-1",
    "deploy-drain-1",
    "deploy-redis-1",
    "deploy-alloy-1",
)

MAX_FILE_BYTES = 25 * 1024 * 1024

REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(authorization|proxy-authorization)\s*[:=]\s*"
            r"(bearer|basic)\s+[A-Za-z0-9._~+/\-]+=*"
        ),
        r"\1: <redacted>",
    ),
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API[_-]?KEY|PRIVATE[_-]?KEY)"
            r"[A-Z0-9_]*)\s*[:=]\s*([^\s,;\"']+)"
        ),
        r"\1=<redacted>",
    ),
    (
        re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/\-]+=*"),
        r"\1 <redacted>",
    ),
    (
        re.compile(r"://([^:/\s]+):([^@/\s]+)@"),
        r"://\1:<redacted>@",
    ),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "-----BEGIN PRIVATE KEY-----<redacted>-----END PRIVATE KEY-----",
    ),
)


def _redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _utc_minute_floor() -> datetime:
    return datetime.now(tz=UTC).replace(second=0, microsecond=0)


def _run(cmd: list[str], *, timeout: int = 180) -> tuple[int | None, str]:
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"{type(exc).__name__}: {exc}\n"
    return result.returncode, result.stdout


def _run_shell(command: str, *, timeout: int = 180) -> tuple[int | None, str]:
    return _run(["/bin/bash", "-lc", command], timeout=timeout)


def _write(path: Path, text: str) -> dict[str, object]:
    redacted = _redact(text)
    data = redacted.encode("utf-8", errors="replace")
    truncated = len(data) > MAX_FILE_BYTES
    if truncated:
        data = data[:MAX_FILE_BYTES]
        data += b"\n\n[truncated by codex-error-review-bundle.py]\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o640)
    return {"path": str(path), "bytes": path.stat().st_size, "truncated": truncated}


def _error_lines(text: str) -> str:
    keep: list[str] = []
    for line in text.splitlines():
        lower = line.lower()
        if any(
            marker in lower
            for marker in (
                '"level": "error"',
                '"level":"error"',
                "level=error",
                "traceback",
                "exception",
                "error",
                "oom",
                "killed",
            )
        ):
            keep.append(line)
    return "\n".join(keep) + ("\n" if keep else "")


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "unknown"


def _parse_docker_timestamp(value: str) -> datetime | None:
    if not value or value.startswith("0001-01-01"):
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    if "." in normalized:
        prefix, suffix = normalized.split(".", 1)
        offset_index = max(suffix.rfind("+"), suffix.rfind("-"))
        if offset_index >= 0:
            fraction = suffix[:offset_index][:6]
            offset = suffix[offset_index:]
            normalized = f"{prefix}.{fraction}{offset}"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _collect_command(
    run_dir: Path,
    manifest: dict[str, object],
    name: str,
    cmd: list[str],
    *,
    timeout: int = 180,
) -> None:
    code, output = _run(cmd, timeout=timeout)
    file_info = _write(run_dir / "host" / f"{name}.txt", output)
    manifest.setdefault("commands", []).append(
        {"name": name, "cmd": cmd, "returncode": code, **file_info}
    )


def _collect_shell(
    run_dir: Path,
    manifest: dict[str, object],
    name: str,
    command: str,
    *,
    timeout: int = 180,
) -> None:
    code, output = _run_shell(command, timeout=timeout)
    file_info = _write(run_dir / "host" / f"{name}.txt", output)
    manifest.setdefault("commands", []).append(
        {"name": name, "cmd": command, "returncode": code, **file_info}
    )


def _collect_container_logs(
    run_dir: Path,
    manifest: dict[str, object],
    *,
    since: datetime,
    until: datetime,
) -> None:
    since_iso = since.isoformat().replace("+00:00", "Z")
    until_iso = until.isoformat().replace("+00:00", "Z")
    for container in LONG_RUNNING_CONTAINERS:
        code, output = _run(
            ["docker", "logs", "--since", since_iso, "--until", until_iso, container],
            timeout=600,
        )
        log_info = _write(run_dir / "logs" / f"{container}.log", output)
        err_info = _write(run_dir / "error-lines" / f"{container}.log", _error_lines(output))
        manifest.setdefault("container_logs", []).append(
            {
                "container": container,
                "returncode": code,
                "log": log_info,
                "error_lines": err_info,
            }
        )


def _collect_exited_containers(
    run_dir: Path,
    manifest: dict[str, object],
    *,
    since: datetime,
    until: datetime,
) -> None:
    code, output = _run(
        [
            "docker",
            "ps",
            "-a",
            "-q",
            "--filter",
            "status=exited",
        ],
        timeout=120,
    )
    candidates: list[dict[str, str]] = []
    inspect_errors: list[dict[str, object]] = []

    if code == 0:
        for container_id in [line.strip() for line in output.splitlines() if line.strip()]:
            inspect_code, inspect_output = _run(["docker", "inspect", container_id], timeout=60)
            if inspect_code != 0:
                inspect_errors.append(
                    {"id": container_id, "returncode": inspect_code, "output": inspect_output[:500]}
                )
                continue
            try:
                inspect_data = json.loads(inspect_output)[0]
            except (IndexError, json.JSONDecodeError) as exc:
                inspect_errors.append(
                    {
                        "id": container_id,
                        "returncode": inspect_code,
                        "output": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue

            image = str(inspect_data.get("Config", {}).get("Image", ""))
            if not (image.startswith("ghcr.io/") and "/jobseek-crawler" in image):
                continue

            state = inspect_data.get("State", {})
            finished_at = _parse_docker_timestamp(str(state.get("FinishedAt", "")))
            if finished_at is None or not (since <= finished_at <= until):
                continue

            candidates.append(
                {
                    "id": container_id,
                    "name": str(inspect_data.get("Name", "")).lstrip("/") or container_id,
                    "image": image,
                    "finished_at": finished_at.isoformat(),
                    "status": str(state.get("Status", "")),
                }
            )

    listing = "\n".join(
        "\t".join(
            (
                candidate["id"],
                candidate["name"],
                candidate["image"],
                candidate["finished_at"],
                candidate["status"],
            )
        )
        for candidate in candidates
    )
    if listing:
        listing += "\n"
    list_info = _write(
        run_dir / "host" / "docker-exited-containers.txt",
        output if code != 0 else listing,
    )
    manifest.setdefault("commands", []).append(
        {
            "name": "docker-exited-containers",
            "cmd": "docker ps -aq --filter status=exited + docker inspect",
            "returncode": code,
            "window_filtered": code == 0,
            **list_info,
        }
    )
    if inspect_errors:
        manifest["exited_container_inspect_errors"] = inspect_errors[:30]
    if code != 0:
        return
    since_iso = since.isoformat().replace("+00:00", "Z")
    until_iso = until.isoformat().replace("+00:00", "Z")
    for candidate in candidates[:30]:
        container_id = candidate["id"]
        name = candidate["name"]
        code, logs = _run(
            [
                "docker",
                "logs",
                "--since",
                since_iso,
                "--until",
                until_iso,
                "--tail",
                "1000",
                container_id,
            ],
            timeout=180,
        )
        info = _write(
            run_dir / "exited" / f"{_safe_name(name)}-{container_id}.log",
            logs,
        )
        manifest.setdefault("exited_container_logs", []).append(
            {
                "id": container_id,
                "name": name,
                "image": candidate["image"],
                "finished_at": candidate["finished_at"],
                "returncode": code,
                **info,
            }
        )


def _chgrp_readable(path: Path, *, group: str) -> None:
    import grp

    gid = grp.getgrnam(group).gr_gid
    paths = [path, *path.rglob("*")]
    for item in paths:
        try:
            os.chown(item, 0, gid)
            item.chmod(0o750 if item.is_dir() else 0o640)
        except OSError:
            continue


def collect_bundle(out_root: Path, *, window_hours: int, group: str) -> Path:
    until = _utc_minute_floor()
    since = until - timedelta(hours=window_hours)
    run_dir = out_root / until.strftime("%Y-%m-%dT%H%MZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "created_at": datetime.now(tz=UTC).isoformat(),
        "window": {
            "since": since.isoformat(),
            "until": until.isoformat(),
            "hours": window_hours,
        },
        "redaction": "common credential shapes redacted before codex-runner reads bundle",
        "max_file_bytes": MAX_FILE_BYTES,
    }

    _collect_command(run_dir, manifest, "df-root", ["df", "-h", "/"])
    _collect_command(run_dir, manifest, "df-docker", ["df", "-h", "/var/lib/docker"])
    _collect_command(run_dir, manifest, "free", ["free", "-h"])
    _collect_command(run_dir, manifest, "uptime", ["uptime"])
    _collect_command(
        run_dir,
        manifest,
        "docker-ps",
        ["docker", "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}"],
    )
    _collect_command(
        run_dir,
        manifest,
        "docker-stats",
        [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}",
        ],
        timeout=120,
    )
    inspect_state_command = (
        "ids=$(docker ps -aq); test -z \"$ids\" || docker inspect --format "
        "'{{.Name}} OOMKilled={{.State.OOMKilled}} Status={{.State.Status}} "
        "RestartCount={{.RestartCount}} FinishedAt={{.State.FinishedAt}}' $ids"
    )
    _collect_shell(
        run_dir,
        manifest,
        "docker-inspect-state",
        inspect_state_command,
        timeout=180,
    )
    kernel_log_command = (
        f"journalctl -k --since '{since.isoformat()}' --until '{until.isoformat()}' "
        "--no-pager 2>/dev/null | tail -500"
    )
    _collect_shell(
        run_dir,
        manifest,
        "kernel-log",
        kernel_log_command,
        timeout=180,
    )
    _collect_container_logs(run_dir, manifest, since=since, until=until)
    _collect_exited_containers(run_dir, manifest, since=since, until=until)

    _write(run_dir / "manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
    _chgrp_readable(run_dir, group=group)

    latest = out_root / "latest"
    tmp = out_root / ".latest.tmp"
    if tmp.exists() or tmp.is_symlink():
        tmp.unlink()
    tmp.symlink_to(run_dir.name, target_is_directory=True)
    tmp.replace(latest)
    try:
        import grp

        os.chown(out_root, 0, grp.getgrnam(group).gr_gid)
        out_root.chmod(0o750)
    except OSError:
        pass
    return run_dir


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect daily error-review evidence bundle.")
    parser.add_argument(
        "--out-root",
        type=Path,
        default=Path("/srv/jobseek-codex/inputs/error-review"),
    )
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--group", default="codex-runner")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if shutil.which("docker") is None:
        raise SystemExit("docker command not found")
    run_dir = collect_bundle(args.out_root, window_hours=args.window_hours, group=args.group)
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
