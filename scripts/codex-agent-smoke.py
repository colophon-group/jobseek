#!/usr/bin/env python3
"""Bounded Codex skill/custom-agent smoke in a disposable credential-free fixture.

Uses the runner's subscription auth, but copies no deployment secrets or data.
The shell sandbox has network disabled. Model traffic uses normal Codex auth.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/crawler"))

from src.labeller.render import render_task  # noqa: E402
from src.workspace.codex_agents import project_agent_overrides  # noqa: E402
from src.workspace.safe_cleanup import (  # noqa: E402
    open_absolute_directory_no_follow,
    unlink_child_at,
)
from src.workspace.trace_backfill import (  # noqa: E402
    _open_retained_file_no_follow,
    _read_session_metadata_no_follow,
    inventory_automation_sessions,
)


def _publish_archive_manifest(archive_fd: int, name: str, content: bytes) -> None:
    """Publish a complete, durable manifest without overwriting an existing entry."""
    staging = f".manifest-{uuid.uuid4().hex}"
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=archive_fd)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(staging, name, src_dir_fd=archive_fd, dst_dir_fd=archive_fd)
        except FileExistsError:
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=archive_fd
            )
            with os.fdopen(descriptor, "rb") as handle:
                if (
                    not stat.S_ISREG(os.fstat(handle.fileno()).st_mode)
                    or handle.read(len(content) + 1) != content
                ):
                    raise RuntimeError("legacy smoke archive manifest mismatch") from None
        os.fsync(archive_fd)
    finally:
        os.unlink(staging, dir_fd=archive_fd)


def archive_legacy_smoke_sessions(home: Path) -> int:
    """Preserve old deployment smoke evidence outside the automation session store.

    Call only while holding the runner deployment lock. A durable hard link and
    hash manifest precede removal of the original name; retries are idempotent.
    Unrelated, malformed, linked automation and recent sessions stay untouched.
    """
    inventory = inventory_automation_sessions(home)
    count = 0
    for path in inventory.unlinked:
        metadata = _read_session_metadata_no_follow(path, root=home / "sessions")
        if not metadata or not re.fullmatch(
            r"/tmp/jobseek-codex-smoke-[a-z0-9_]{8}", str(metadata.get("cwd", ""))
        ):
            continue
        if metadata.get("originator") != "codex_exec":
            continue
        source_fd = _open_retained_file_no_follow(root=home / "sessions", path=path)
        if source_fd is None:
            continue
        with os.fdopen(source_fd, "rb") as source:
            opened = os.fstat(source.fileno())
            if time.time() - opened.st_mtime < 3600:
                continue
            if opened.st_size > 64 * 1024 * 1024:
                raise RuntimeError("legacy smoke source exceeds archive size limit")
            data = source.read(64 * 1024 * 1024 + 1)
            digest = hashlib.sha256(data).hexdigest()
            if (
                len(data) != opened.st_size
                or json.loads(data.split(b"\n", 1)[0]).get("payload") != metadata
            ):
                raise RuntimeError("legacy smoke source changed during archival")
            archive_name = f"{digest}-{path.name}"
            home_fd = open_absolute_directory_no_follow(home)
            try:
                with suppress(FileExistsError):
                    os.mkdir("smoke-session-archive", mode=0o700, dir_fd=home_fd)
            finally:
                os.close(home_fd)
            archive_fd = open_absolute_directory_no_follow(home / "smoke-session-archive")
            parent_fd = open_absolute_directory_no_follow(path.parent)
            try:
                with suppress(FileExistsError):
                    os.link(
                        path.name,
                        archive_name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=archive_fd,
                        follow_symlinks=False,
                    )
                archived_fd = os.open(archive_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=archive_fd)
                with os.fdopen(archived_fd, "rb") as archived:
                    current = os.fstat(archived.fileno())
                    if (current.st_dev, current.st_ino) != (
                        opened.st_dev,
                        opened.st_ino,
                    ) or hashlib.sha256(archived.read(64 * 1024 * 1024 + 1)).hexdigest() != digest:
                        raise RuntimeError("legacy smoke archive identity or hash mismatch")
                    os.fsync(archived.fileno())
                manifest = (
                    json.dumps(
                        {
                            "source": str(path.relative_to(home)),
                            "archive": archive_name,
                            "sha256": digest,
                            "bytes": len(data),
                            "cwd": metadata["cwd"],
                        },
                        sort_keys=True,
                    ).encode()
                    + b"\n"
                )
                _publish_archive_manifest(archive_fd, archive_name + ".manifest.json", manifest)
                unlink_child_at(parent_fd, path.name, expected=opened)
                os.fsync(parent_fd)
                count += 1
            finally:
                os.close(parent_fd)
                os.close(archive_fd)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--trace-out", type=Path, help="Optional local trace for fixture debugging")
    parser.add_argument(
        "--archive-legacy-sessions",
        action="store_true",
        help="Archive old deployment smoke sessions under the held runner deployment lock",
    )
    args = parser.parse_args()
    persistent_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).resolve()
    if args.archive_legacy_sessions:
        count = archive_legacy_smoke_sessions(persistent_home)
        print(json.dumps({"event": "codex_legacy_smoke_archival", "archived": count}))
        return
    with (
        tempfile.TemporaryDirectory(prefix="jobseek-codex-smoke-") as tmp,
        tempfile.TemporaryDirectory(prefix="jobseek-codex-smoke-home-") as home_tmp,
    ):
        fixture = Path(tmp).resolve()
        subprocess.run(["git", "init", "--quiet", str(fixture)], check=True)
        for relative in (
            ".codex/agents/jobseek-labeller-normalizer.toml",
            ".agents/labeller/normalizer.md",
        ):
            destination = fixture / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)
        skill = fixture / ".agents/skills/jobseek-smoke/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("""---
name: jobseek-smoke
description: Verify the Jobseek normalizer agent on one harmless fixed fixture.
---
Spawn exactly one jobseek-labeller-normalizer agent with this two-line task:
INPUT: input.md
OUTPUT: result.html
Wait for it to finish. Do not write its result for it. Reply exactly SMOKE_OK.
""")
        (fixture / "input.md").write_text(
            render_task(
                "normalize_html",
                input_data={
                    "input": {
                        "title_raw": "Jobseek smoke fixture",
                        "description_html_raw": '<p class="example">Jobseek smoke fixture</p>',
                    }
                },
                output_path="result.html",
            )
        )
        # Do not pass routine DSNs, GH tokens, HF tokens, SSH configuration or
        # runner environment files into the smoke. Auth stays in CODEX_HOME.
        child_env = {
            key: os.environ[key]
            for key in ("PATH", "HOME", "CODEX_HOME", "TMPDIR", "LANG")
            if key in os.environ
        }
        # Keep native rollouts available for delegation verification, but remove
        # the whole disposable session store on every outcome. Subscription auth
        # uses its existing file; no credential bytes are copied into the fixture.
        smoke_home = Path(home_tmp)
        (smoke_home / "auth.json").symlink_to(persistent_home / "auth.json")
        child_env["CODEX_HOME"] = str(smoke_home)
        command = [
            args.codex,
            "exec",
            "--json",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "-c",
            'approval_policy="never"',
            "-c",
            f'projects.{json.dumps(str(fixture))}.trust_level="trusted"',
            "-c",
            "sandbox_workspace_write.network_access=false",
            *project_agent_overrides(fixture),
            "-C",
            str(fixture),
            "-m",
            "gpt-6.1-sol",
            "-c",
            "model_reasoning_effort=low",
            "Use $jobseek-smoke to verify the named project agent on the fixture. "
            "This is a test only. Do not use network, MCP, or unrelated files.",
        ]
        with subprocess.Popen(
            command,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as proc:
            try:
                stdout, stderr = proc.communicate(timeout=240)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.communicate()
                raise SystemExit("Codex skill/custom-agent smoke timed out") from None
            result = subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)
        if args.trace_out:
            args.trace_out.write_text(result.stdout + result.stderr)
        events = [json.loads(line) for line in result.stdout.split("\n") if line.startswith("{")]
        # CLI JSONL currently omits spawn details for some collaboration events.
        # Check the native parent rollout for the actual named-role invocation.
        thread = next(
            (event["thread_id"] for event in events if event.get("type") == "thread.started"),
            "",
        )
        delegated = False
        if re.fullmatch(r"[a-f0-9-]{36}", thread):
            home = Path(child_env.get("CODEX_HOME", str(Path.home() / ".codex")))
            for trace in (home / "sessions").glob(f"**/*{thread}*.jsonl"):
                for line in trace.read_text().split("\n"):
                    if not line.strip():
                        continue
                    payload = json.loads(line).get("payload", {})
                    if (
                        payload.get("type") != "function_call"
                        or payload.get("name") != "spawn_agent"
                    ):
                        continue
                    arguments = json.loads(payload.get("arguments", "{}"))
                    delegated |= arguments.get("agent_type") == "jobseek-labeller-normalizer"
        completed = any(event.get("type") == "turn.completed" for event in events)
        output = fixture / "result.html"
        if (
            result.returncode
            or not delegated
            or not completed
            or not output.is_file()
            or output.read_text().strip() != "<p>Jobseek smoke fixture</p>"
        ):
            raise SystemExit(
                f"Codex smoke failed: exit={result.returncode}, delegated={delegated}, "
                f"completed={completed}, artifact={output.is_file()}"
            )
        print("Codex skill/custom-agent smoke passed")


if __name__ == "__main__":
    main()
