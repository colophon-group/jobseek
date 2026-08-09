from __future__ import annotations

import os
import subprocess
from pathlib import Path

DEPLOY_HELPERS_SH = Path(__file__).resolve().parent.parent / "deploy_helpers.sh"


def _fake_docker(tmp_path: Path, body: str) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    call_log = tmp_path / "calls.log"
    docker = bin_dir / "docker"
    docker.write_text("#!/usr/bin/env bash\n" + body)
    docker.chmod(0o755)
    return bin_dir, call_log


def _run_helper(
    tmp_path: Path,
    bin_dir: Path,
    call_log: Path,
    command: str,
    **extra_env: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "CALL_LOG": str(call_log),
            "DEPLOY_PULL_RETRY_DELAY_SECONDS": "0",
            **extra_env,
        }
    )
    return subprocess.run(
        ["bash", "-c", f'source "$HELPER"; {command}'],
        cwd=tmp_path,
        env={**env, "HELPER": str(DEPLOY_HELPERS_SH)},
        check=False,
        capture_output=True,
        text=True,
    )


def test_pull_deploy_images_serializes_unique_image_services(tmp_path: Path) -> None:
    bin_dir, call_log = _fake_docker(
        tmp_path,
        'echo "$*" >> "$CALL_LOG"\nexit 0\n',
    )

    result = _run_helper(tmp_path, bin_dir, call_log, "pull_deploy_images")

    assert result.returncode == 0, result.stderr
    assert call_log.read_text().splitlines() == [
        "compose pull worker-1",
        "compose pull browser-1",
        "compose pull redis",
        "compose pull alloy",
        "compose pull murmur-shim",
    ]


def test_pull_compose_service_retries_then_recovers(tmp_path: Path) -> None:
    state_file = tmp_path / "attempts"
    bin_dir, call_log = _fake_docker(
        tmp_path,
        'echo "$*" >> "$CALL_LOG"\n'
        'attempt=$(cat "$STATE_FILE" 2>/dev/null || echo 0)\n'
        "attempt=$((attempt + 1))\n"
        'echo "$attempt" > "$STATE_FILE"\n'
        "(( attempt >= 3 ))\n",
    )

    result = _run_helper(
        tmp_path,
        bin_dir,
        call_log,
        "pull_compose_service_with_retry worker-1",
        STATE_FILE=str(state_file),
    )

    assert result.returncode == 0, result.stderr
    assert call_log.read_text().splitlines() == ["compose pull worker-1"] * 3
    assert "retrying" in result.stderr


def test_pull_deploy_images_propagates_persistent_failure(tmp_path: Path) -> None:
    bin_dir, call_log = _fake_docker(
        tmp_path,
        'echo "$*" >> "$CALL_LOG"\nexit 1\n',
    )

    result = _run_helper(
        tmp_path,
        bin_dir,
        call_log,
        "pull_deploy_images",
        DEPLOY_PULL_ATTEMPTS="2",
    )

    assert result.returncode == 1
    assert call_log.read_text().splitlines() == ["compose pull worker-1"] * 2
    assert "after 2 attempts" in result.stderr
