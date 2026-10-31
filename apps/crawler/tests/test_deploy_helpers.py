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
        "compose pull ordinary-go",
        "compose pull redis",
        "compose pull alloy",
    ]


def test_quiescence_stops_removed_and_current_services_by_project_labels(tmp_path: Path) -> None:
    old, native, redis = "a" * 64, "b" * 64, "c" * 64
    bin_dir, call_log = _fake_docker(
        tmp_path,
        'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
        'if [[ "$1" == ps ]]; then\n'
        '  [[ "$*" == *"label=com.docker.compose.project=crawler-test"* ]] || exit 1\n'
        f"  printf '%s\\n' '{old} browser-1' '{native} ordinary-go' '{redis} redis'\n"
        "fi\n",
    )
    result = _run_helper(
        tmp_path,
        bin_dir,
        call_log,
        "stop_crawler_services 60 browser-1 ordinary-go",
        COMPOSE_PROJECT_NAME="crawler-test",
    )
    assert result.returncode == 0, result.stderr
    calls = call_log.read_text().splitlines()
    assert calls[0].startswith("ps --no-trunc --filter label=com.docker.compose.project=")
    assert calls[1:] == [f"stop --timeout 60 {old} {native}"]


def test_quiescence_discovery_failure_does_not_stop_partial_set(tmp_path: Path) -> None:
    for name, output, status in (
        ("invalid", "not-a-container ordinary-go", 0),
        ("failed", "a" * 64 + " ordinary-go", 1),
    ):
        case = tmp_path / name
        case.mkdir()
        bin_dir, call_log = _fake_docker(
            case,
            f'printf "%s\\n" "$*" >> "$CALL_LOG"\nprintf \'%s\\n\' \'{output}\'\nexit {status}\n',
        )
        result = _run_helper(
            case,
            bin_dir,
            call_log,
            "stop_crawler_services 60 ordinary-go",
            COMPOSE_PROJECT_NAME="crawler-test",
        )
        assert result.returncode != 0
        assert len(call_log.read_text().splitlines()) == 1


def test_quiescence_propagates_stop_failure_and_allows_empty_first_deploy(tmp_path: Path) -> None:
    for name, output, status, expected in (
        ("empty", "", 0, 0),
        ("stop-failed", "a" * 64 + " ordinary-go", 1, 1),
    ):
        case = tmp_path / name
        case.mkdir()
        bin_dir, call_log = _fake_docker(
            case,
            'printf "%s\\n" "$*" >> "$CALL_LOG"\n'
            f'if [[ "$1" == ps ]]; then printf "%s\\n" "{output}"; else exit {status}; fi\n',
        )
        result = _run_helper(
            case,
            bin_dir,
            call_log,
            "stop_crawler_services 60 ordinary-go",
            COMPOSE_PROJECT_NAME="crawler-test",
        )
        assert result.returncode == expected


def test_quiescence_rejects_nonwriter_and_invalid_project_before_discovery(tmp_path: Path) -> None:
    bin_dir, call_log = _fake_docker(tmp_path, 'echo called >> "$CALL_LOG"\nexit 99\n')
    for command, project in (
        ("stop_crawler_services 60 redis", "crawler-test"),
        ("stop_crawler_services 0 ordinary-go", "crawler-test"),
        ("stop_crawler_services 60 ordinary-go", ""),
    ):
        result = _run_helper(tmp_path, bin_dir, call_log, command, COMPOSE_PROJECT_NAME=project)
        assert result.returncode != 0
        assert not call_log.exists()


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
    assert call_log.read_text().splitlines() == ["compose pull ordinary-go"] * 2
    assert "after 2 attempts" in result.stderr
