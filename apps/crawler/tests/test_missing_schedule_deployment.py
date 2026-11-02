from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY_SH = Path(__file__).resolve().parent.parent / "deploy.sh"


@pytest.mark.parametrize(
    ("backend", "inspect_status", "apply_status", "expected_calls", "expected_status"),
    [("go", 0, 0, 2, 0), ("go", 23, 0, 1, 23), ("go", 0, 27, 2, 27), ("legacy", 0, 0, 0, 0)],
)
def test_missing_schedule_repair_preserves_authority_and_stops_on_failure(
    tmp_path: Path,
    backend: str,
    inspect_status: int,
    apply_status: int,
    expected_calls: int,
    expected_status: int,
) -> None:
    source = DEPLOY_SH.read_text()
    start = source.index("repair_default_missing_schedules() {")
    function = source[start : source.index("\n}\n", start) + 3]
    log = tmp_path / "calls.log"
    harness = "\n".join(
        (
            "set -u",
            'CRAWLER_RUNTIME_BACKEND="$TEST_BACKEND"',
            'CRAWLER_IMAGE_REF="ghcr.io/colophon-group/jobseek-crawler@sha256:' + "a" * 64 + '"',
            "MAINTENANCE_PROVENANCE_LABELS=(--label provenance=qualified)",
            """docker() { printf '%s\\n' "$*" >>"$TEST_LOG"; """
            'if [[ "$*" == *--apply* ]]; then return "$APPLY_STATUS"; '
            'else return "$INSPECT_STATUS"; fi; }',
            function,
            "if repair_default_missing_schedules inspect && "
            "repair_default_missing_schedules apply; then status=0; else status=$?; fi",
            """printf 'status=%s\\n' "$status" >>"$TEST_LOG" """,
        )
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "TEST_LOG": str(log),
            "TEST_BACKEND": backend,
            "INSPECT_STATUS": str(inspect_status),
            "APPLY_STATUS": str(apply_status),
        },
    )
    assert result.returncode == 0, result.stderr
    lines = log.read_text().splitlines()
    assert lines[-1] == f"status={expected_status}"
    assert len(lines) == expected_calls + 1
    for index, call in enumerate(lines[:-1]):
        assert "-e LOCAL_DATABASE_URL -e REDIS_URL" in call
        assert "-e CRAWLER_DB_POOL_MIN=0 -e CRAWLER_DB_POOL_MAX=1" in call
        assert "jobseek-lightpanda-b0-producer-socket:/run/jobseek-lightpanda-producer:ro" in call
        assert "--label provenance=qualified" in call
        assert "go-typesense-exporter --redis-capacity repair-missing --limit 50000" in call
        assert ("--apply" in call) == (index == 1)


def test_missing_schedule_repair_runs_quiesced_after_epoch_before_restart() -> None:
    source = DEPLOY_SH.read_text()
    quiesce = source.index("\nstop_crawler_services 60 worker-1")
    sync = source.index("go-typesense-exporter --sync-registry", quiesce)
    epoch = source.index("\nprepare_default_go_epoch\n", sync)
    repair = source.index("\nrepair_default_missing_schedules apply\n", epoch)
    restart = source.index('docker compose up -d "${CRAWLER_STACK_SERVICES[@]}"', repair)
    inspect = source.index("\ninspect_default_missing_schedules\n")
    assert inspect < quiesce < sync < epoch < repair < restart
    assert "requires a verified native Go rollback image" in source[:inspect]


@pytest.mark.parametrize("verification_status", [0, 23])
def test_missing_schedule_inspect_binds_previous_compose_environment_and_data(
    tmp_path: Path, verification_status: int
) -> None:
    import json

    source = DEPLOY_SH.read_text()

    def function(name):
        start = source.index(name + "() {")
        return source[start : source.index("\n}\n", start) + 3]

    previous = tmp_path / "previous"
    previous.mkdir()
    (previous / "environment.env").write_text("LOCAL_DATABASE_URL=previous-only\n")
    (previous / "docker-compose.yml").write_text("services: {}\n")
    (previous / "data").mkdir()
    log = tmp_path / "calls"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/usr/bin/env python3\nimport json, os, pathlib, sys\n"
        "keys = ['LOCAL_DATABASE_URL','REDIS_URL','COMPOSE_FILE','WEB_DATABASE_URL']\n"
        "payload = {'args':sys.argv[1:], 'leaked_keys':[k for k in keys if k in os.environ]}\n"
        f"pathlib.Path({str(log)!r}).write_text(json.dumps(payload))\n"
    )
    docker.chmod(0o700)
    verifier = tmp_path / "verifier"
    verifier.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        f"assert sys.argv[1:] == ['release-bridge', {str(previous)!r}, 'colophon-group']\n"
        f"raise SystemExit({verification_status})\n"
    )
    verifier.chmod(0o700)
    result = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                (
                    "set -eu",
                    "CRAWLER_RUNTIME_BACKEND=go",
                    "MAINTENANCE_PROVENANCE_LABELS=(--label provenance=qualified)",
                    "COMPOSE_PROJECT_NAME=fixture",
                    "ROLLBACK_ACTIVE_IMAGE_OVERRIDE=",
                    function("rollback_compose"),
                    function("inspect_default_missing_schedules"),
                    "inspect_default_missing_schedules",
                )
            ),
        ],
        env={
            **os.environ,
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "ROLLBACK_ACTIVE_RELEASE_TARGET": str(previous),
            "NATIVE_DEPLOY_TOOLS": str(verifier),
            "OWNER": "colophon-group",
            "LOCAL_DATABASE_URL": "incoming-must-not-leak",
            "REDIS_URL": "incoming-must-not-leak",
            "COMPOSE_FILE": "incoming-must-not-leak",
            "WEB_DATABASE_URL": "must-not-leak",
        },
        capture_output=True,
        text=True,
    )
    if verification_status:
        assert result.returncode != 0
        assert not log.exists()
        return
    assert result.returncode == 0, result.stderr
    receipt = json.loads(log.read_text())
    args = receipt["args"]
    assert receipt["leaked_keys"] == []
    assert args[args.index("--env-file") + 1] == str(previous / "environment.env")
    assert args[args.index("-f") + 1] == str(previous / "docker-compose.yml")
    assert str(previous / "data") + ":/app/data:ro" in args
    assert args[-5:] == ["worker-1", "--redis-capacity", "repair-missing", "--limit", "50000"]
    assert "--apply" not in args
    assert "/usr/local/bin/go-typesense-exporter" in args
    assert "incoming-must-not-leak" not in result.stdout + result.stderr + log.read_text()
