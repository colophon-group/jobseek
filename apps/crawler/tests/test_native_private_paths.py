from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy/networking/verify-private-paths.sh"
BASH = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"


def run_probe(
    tmp_path: Path,
    *,
    exporters: str = "abcdef012345",
    ps_status: int = 0,
    exec_status: int = 0,
    postgres_ip: str = "10.0.0.2",
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    docker = tmp_path / "docker"
    calls = tmp_path / "calls.jsonl"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['PROBE_CALLS'], 'a') as stream:\n"
        "    stream.write(json.dumps(args) + '\\n')\n"
        "if args[0] == 'ps':\n"
        "    print(os.environ['PROBE_EXPORTERS'], end='')\n"
        "    if int(os.environ['PROBE_PS_STATUS']):\n"
        "        print('untrusted Docker endpoint detail', file=sys.stderr)\n"
        "    sys.exit(int(os.environ['PROBE_PS_STATUS']))\n"
        "if args[0] == 'exec':\n"
        "    sys.exit(int(os.environ['PROBE_EXEC_STATUS']))\n"
        "raise SystemExit(99)\n"
    )
    docker.chmod(0o700)
    result = subprocess.run(
        [BASH, str(SCRIPT)],
        env={
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "JOBSEEK_POSTGRES_PRIVATE_IP": postgres_ip,
            "JOBSEEK_TYPESENSE_PRIVATE_IP": "10.0.0.3",
            "PROBE_CALLS": str(calls),
            "PROBE_EXPORTERS": exporters,
            "PROBE_PS_STATUS": str(ps_status),
            "PROBE_EXEC_STATUS": str(exec_status),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    records = (
        [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    )
    return result, records


@pytest.mark.parametrize("exec_status", [0, 29])
def test_private_path_probe_execs_native_command_once_and_preserves_status(
    tmp_path: Path, exec_status: int
) -> None:
    result, calls = run_probe(tmp_path, exec_status=exec_status)
    assert result.returncode == exec_status, result.stderr
    assert calls == [
        ["ps", "--filter", "label=com.docker.compose.service=exporter", "--format", "{{.ID}}"],
        [
            "exec",
            "-e",
            "JOBSEEK_EXPECTED_POSTGRES_PRIVATE_IP=10.0.0.2",
            "-e",
            "JOBSEEK_EXPECTED_TYPESENSE_PRIVATE_IP=10.0.0.3",
            "abcdef012345",
            "/usr/local/bin/go-typesense-exporter",
            "--verify-private-paths",
        ],
    ]
    assert "10.0.0." not in result.stdout + result.stderr
    assert "uv " not in SCRIPT.read_text() and "python" not in SCRIPT.read_text()


@pytest.mark.parametrize("exporters", ["", "abcdef012345\nabcdef012346\n", "not-a-container"])
def test_private_path_probe_requires_exactly_one_live_exporter(
    tmp_path: Path, exporters: str
) -> None:
    result, calls = run_probe(tmp_path, exporters=exporters)
    assert result.returncode != 0
    assert "Expected exactly one live exporter container" in result.stderr
    assert len(calls) == 1 and calls[0][0] == "ps"


def test_private_path_probe_rejects_failed_discovery_even_with_one_partial_row(
    tmp_path: Path,
) -> None:
    result, calls = run_probe(tmp_path, ps_status=23)
    assert result.returncode != 0
    assert "Cannot select the live exporter container" in result.stderr
    assert "untrusted Docker endpoint detail" not in result.stderr
    assert len(calls) == 1 and calls[0][0] == "ps"


def test_private_path_probe_rejects_public_expected_host_before_docker(tmp_path: Path) -> None:
    result, calls = run_probe(tmp_path, postgres_ip="203.0.113.2")
    assert result.returncode != 0
    assert "A private service IPv4 value is required" in result.stderr
    assert calls == []
