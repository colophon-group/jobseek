from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

DEPLOY_SH = Path(__file__).resolve().parent.parent / "deploy.sh"
BASH = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"


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


def _functions(*names: str) -> str:
    source = DEPLOY_SH.read_text()
    blocks = []
    for name in names:
        start = source.index(name + "() {")
        blocks.append(source[start : source.index("\n}\n", start) + 3])
    return "\n".join(blocks)


@pytest.mark.parametrize("errtrace", [False, True])
def test_failed_inspection_traps_restore_host_root_and_preserve_generation(
    tmp_path: Path, errtrace: bool
) -> None:
    root, previous = tmp_path / "host-root", tmp_path / "previous-generation"
    root.mkdir()
    previous.mkdir()
    (previous / "data").mkdir()
    (previous / "docker-compose.yml").write_text("services: {}\n")
    (root / ".env").write_text("incoming\n")
    previous_text = (
        "CRAWLER_RUNTIME_BACKEND=go\n"
        "CRAWLER_IMAGE_REF=ghcr.io/colophon-group/jobseek-crawler@sha256:"
        + "a" * 64
        + "\nLOCAL_DATABASE_URL=postgresql://previous/authority\n"
    )
    (root / ".env.rollback").write_text(previous_text)
    (previous / "environment.env").write_text(previous_text)
    (root / ".deploy-spec.rollback.tar").touch()
    docker, verifier = tmp_path / "docker", tmp_path / "verifier"
    # Only the external container invocation fails; actual Bash functions and
    # the production ERR/EXIT trap body handle all unwinding and env restoration.
    docker.write_text("#!/bin/sh\nexit 19\n")
    verifier.write_text("#!/bin/sh\nexit 0\n")
    docker.chmod(0o700)
    verifier.chmod(0o700)
    receipt = tmp_path / "rollback-targets"
    script = "\n".join(
        (
            "set -eu" + ("E" if errtrace else ""),
            'DEPLOY_DIR="$HOST_ROOT"',
            'ENV_FILE="$HOST_ROOT/.env"',
            'ROLLBACK_ENV_FILE="$HOST_ROOT/.env.rollback"',
            'ROLLBACK_SPEC_ARCHIVE="$HOST_ROOT/.deploy-spec.rollback.tar"',
            "CRAWLER_RUNTIME_BACKEND=go",
            "ROLLBACK_RUNNING=0",
            "ENV_FILE_WAS_PRESENT=1",
            "MIGRATION_CUTOVER_REACHED=0",
            "FORWARD_SYNC_STARTED=0",
            "COMPOSE_PROJECT_NAME=fixture",
            "STAGED_BRIDGE_VERIFIER=",
            "MAINTENANCE_PROVENANCE_LABELS=()",
            # Mock host boundaries, not trap entry or Bash function unwinding.
            "candidate_compose_defines_service() { return 1; }",
            "stop_crawler_services() { :; }",
            "activate_release_generation() { :; }",
            "verify_active_deploy_snapshot() { :; }",
            "restore_previous_deploy_specs() { :; }",
            "configure_rollback_compose_contract() { :; }",
            "rollback_compose() { printf '%s|%s|%s\\n' "
            '"$DEPLOY_DIR" "$ENV_FILE" "$*" > "$RECEIPT"; }',
            "wait_for_rollback_core_services() { :; }",
            "remove_previously_absent_deploy_specs() { :; }",
            "publish_legacy_success_marker() { :; }",
            "stop_maintenance_window() { :; }",
            _functions(
                "read_exact_release_value",
                "previous_native_maintenance",
                "rollback_deploy",
                "arm_deploy_rollback",
                "inspect_default_missing_schedules",
            ),
            "arm_deploy_rollback",
            "inspect_default_missing_schedules",
        )
    )
    result = subprocess.run(
        [BASH, "-c", script],
        env={
            **os.environ,
            "HOST_ROOT": str(root),
            "ROLLBACK_ACTIVE_RELEASE_TARGET": str(previous),
            "RECEIPT": str(receipt),
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "NATIVE_DEPLOY_TOOLS": str(verifier),
            "OWNER": "colophon-group",
        },
        capture_output=True,
        text=True,
    )
    assert result.returncode == 19, result.stderr
    assert receipt.read_text().splitlines() == [
        str(root)
        + "|"
        + str(root / ".env")
        + "|up -d redis ordinary-go lease-reaper exporter drain alloy"
    ]
    assert (root / ".env").read_text() == previous_text
    assert (previous / "environment.env").read_text() == previous_text
    assert "Crawler rollback restored the previous image" in result.stderr


@pytest.mark.parametrize("operation", ["inspect", "sync"])
@pytest.mark.parametrize("verification_status", [0, 23])
def test_previous_native_maintenance_binds_exact_old_env_and_avoids_compose(
    tmp_path: Path, operation: str, verification_status: int
) -> None:
    import json

    previous = tmp_path / "previous"
    previous.mkdir()
    image = "ghcr.io/colophon-group/jobseek-crawler@sha256:" + "a" * 64
    values = {
        "LOCAL_DATABASE_URL": "postgresql://previous/authority?literal=$not_evaluated",
        "TYPESENSE_HOST": "fixture.invalid",
        "TYPESENSE_PORT": "8108",
        "TYPESENSE_PROTOCOL": "https",
        "TYPESENSE_OPERATIONS_KEY": "previous-only",
    }
    (previous / "environment.env").write_text(
        "CRAWLER_IMAGE_REF="
        + image
        + "\n"
        + "\n".join(key + "=" + value for key, value in values.items())
        + "\nWEB_DATABASE_URL=must-not-forward\n"
    )
    (previous / "docker-compose.yml").write_text(
        "services:\n  worker-1:\n    network_mode: host\n    links: [redis]\n"
    )
    (previous / "data").mkdir()
    log, docker, verifier = tmp_path / "calls", tmp_path / "docker", tmp_path / "verifier"
    docker.write_text(
        "#!/usr/bin/env python3\nimport json, os, pathlib, sys\n"
        + "keys = "
        + repr([*values, "REDIS_URL", "COMPOSE_FILE", "WEB_DATABASE_URL"])
        + "\n"
        + "payload = {'args':sys.argv[1:], "
        "'environment':{k:os.environ[k] for k in keys if k in os.environ}}\n"
        + f"pathlib.Path({str(log)!r}).write_text(json.dumps(payload))\n"
    )
    docker.chmod(0o700)
    verifier.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        + f"assert sys.argv[1:] == ['release-bridge', {str(previous)!r}, 'colophon-group']\n"
        + f"raise SystemExit({verification_status})\n"
    )
    verifier.chmod(0o700)
    script = "\n".join(
        (
            "set -eu",
            "MAINTENANCE_PROVENANCE_LABELS=(--label provenance=qualified)",
            _functions("read_exact_release_value", "previous_native_maintenance"),
            'previous_native_maintenance "$PREVIOUS" "$OPERATION"',
        )
    )
    result = subprocess.run(
        [BASH, "-c", script],
        env={
            **os.environ,
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "PREVIOUS": str(previous),
            "OPERATION": operation,
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
    payload = json.loads(log.read_text())
    args = payload["args"]
    assert args[:4] == ["run", "--rm", "--network", "host"]
    assert "compose" not in args and "--link" not in args
    assert str(previous / "data") + ":/app/data:ro" in args
    assert "/usr/local/bin/go-typesense-exporter" in args and image in args
    expected_keys = ["LOCAL_DATABASE_URL"] + (list(values)[1:] if operation == "sync" else [])
    assert payload["environment"] == {
        **{key: values[key] for key in expected_keys},
        "REDIS_URL": "redis://localhost:6379/0",
    }
    assert (
        args[-4:] == ["--redis-capacity", "repair-missing", "--limit", "50000"]
        if operation == "inspect"
        else args[-2:] == ["--sync-registry", "--require-native"]
    )
    assert "--apply" not in args
    assert "incoming-must-not-leak" not in result.stdout + result.stderr + log.read_text()
