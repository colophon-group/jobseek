#!/usr/bin/env python3
"""Bounded Codex skill/custom-agent smoke in a disposable credential-free fixture.

Uses the runner's subscription auth, but copies no deployment secrets or data.
The shell sandbox has network disabled. Model traffic uses normal Codex auth.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/crawler"))

from src.labeller.render import render_task  # noqa: E402
from src.workspace.codex_agents import project_agent_overrides  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex")
    parser.add_argument(
        "--trace-out", type=Path, help="Optional local trace for fixture debugging"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="jobseek-codex-smoke-") as tmp:
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
            result = subprocess.CompletedProcess(
                command, proc.returncode, stdout, stderr
            )
        if args.trace_out:
            args.trace_out.write_text(result.stdout + result.stderr)
        events = [
            json.loads(line)
            for line in result.stdout.splitlines()
            if line.startswith("{")
        ]
        # CLI JSONL currently omits spawn details for some collaboration events.
        # Check the native parent rollout for the actual named-role invocation.
        thread = next(
            (
                event["thread_id"]
                for event in events
                if event.get("type") == "thread.started"
            ),
            "",
        )
        delegated = False
        if re.fullmatch(r"[a-f0-9-]{36}", thread):
            home = Path(child_env.get("CODEX_HOME", str(Path.home() / ".codex")))
            for trace in (home / "sessions").glob(f"**/*{thread}*.jsonl"):
                for line in trace.read_text().splitlines():
                    payload = json.loads(line).get("payload", {})
                    if (
                        payload.get("type") != "function_call"
                        or payload.get("name") != "spawn_agent"
                    ):
                        continue
                    arguments = json.loads(payload.get("arguments", "{}"))
                    delegated |= (
                        arguments.get("agent_type") == "jobseek-labeller-normalizer"
                    )
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
