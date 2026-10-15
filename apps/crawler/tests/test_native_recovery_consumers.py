from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "apps/crawler/scripts/native-deploy-tools.sh"
IMAGE = "ghcr.io/colophon-group/jobseek-crawler@sha256:" + "a" * 64
BASH = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"


def function(path: str, name: str, following: str) -> str:
    source = (ROOT / path).read_text()
    return source[source.index(name + "() {") : source.index(following + "() {")]


@pytest.mark.parametrize(
    ("capability", "inspect_status", "image", "backend"),
    [
        ("v1", 0, IMAGE, "native"),
        ("", 0, IMAGE, "legacy"),
        ("<no value>", 0, IMAGE, "legacy"),
        ("v2", 0, IMAGE, None),
        ("v1", 19, IMAGE, None),
        ("v1", 0, "crawler:latest", None),
    ],
)
def test_maintenance_backend_uses_exact_immutable_image(
    tmp_path: Path,
    capability: str,
    inspect_status: int,
    image: str,
    backend: str | None,
) -> None:
    log = tmp_path / "calls"
    result = subprocess.run(
        [
            BASH,
            "-c",
            'source "$TEST_HELPER"; docker() { printf "%s\\n" "$*" >>"$TEST_LOG"; '
            'printf "%s\\n" "$TEST_CAPABILITY"; return "$TEST_INSPECT_STATUS"; }; '
            'crawler_image_maintenance_backend "$TEST_IMAGE"',
        ],
        env={
            **os.environ,
            "TEST_HELPER": str(HELPER),
            "TEST_LOG": str(log),
            "TEST_CAPABILITY": capability,
            "TEST_INSPECT_STATUS": str(inspect_status),
            "TEST_IMAGE": image,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0 if backend else result.returncode != 0
    assert result.stdout == backend + "\n" if backend else result.stdout == ""
    if image == IMAGE:
        calls = log.read_text().splitlines()
        assert len(calls) == 1 and calls[0].endswith(" " + IMAGE)
        assert calls[0].startswith("image inspect --format ")
    else:
        assert not log.exists()


@pytest.mark.parametrize("backend", ["native", "legacy"])
@pytest.mark.parametrize("status", [0, 23])
def test_parked_repair_uses_selected_cli_without_retry(
    tmp_path: Path,
    backend: str,
    status: int,
) -> None:
    log = tmp_path / "calls"
    repair = function(
        "apps/crawler/deploy.sh", "repair_umantis_identity_cutover", "repair_worker_route_cutover"
    )
    script = (
        'set -euo pipefail; MAINTENANCE_PROVENANCE_LABELS=(); CRAWLER_IMAGE_REF="$TEST_IMAGE"; '
        + repair
        + '\ndocker() { printf "%s\\n" "$*" >>"$TEST_LOG"; return "$TEST_STATUS"; }; '
        'repair_umantis_identity_cutover rollback-umantis-identity-cutover 1 "$TEST_BACKEND"'
    )
    result = subprocess.run(
        [BASH, "-c", script],
        env={
            **os.environ,
            "TEST_IMAGE": IMAGE,
            "TEST_LOG": str(log),
            "TEST_STATUS": str(status),
            "TEST_BACKEND": backend,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == status
    calls = log.read_text().splitlines()
    assert len(calls) == 1
    expected = (
        "go-typesense-exporter --repair-umantis-identity-cutover --park-monitors"
        if backend == "native"
        else "uv run --no-sync crawler repair-umantis-identity-cutover --park-monitors"
    )
    assert calls[0].endswith(IMAGE + " " + expected)


@pytest.mark.parametrize("role", ["csv-sync", "recovery-sync", "bootstrap-sync"])
@pytest.mark.parametrize("capability", ["v1", ""])
@pytest.mark.parametrize("status", [0, 29])
def test_csv_recovery_uses_retained_image_capability_and_never_retries(
    tmp_path: Path,
    role: str,
    capability: str,
    status: int,
) -> None:
    log = tmp_path / "calls"
    sync = function("scripts/crawler-csv-sync-host.sh", "sync_release_data", "write_journal")
    script = (
        'set -euo pipefail; source "$TEST_HELPER"; '
        + sync
        + "\n"
        + r"""
verify_release_generation() {
  ACTIVE_IMAGE_REF="$TEST_IMAGE"; ACTIVE_RELEASE_FORMAT=3; ACTIVE_DATA_DIR="$TEST_ROOT/data"
}
build_runtime_env() { RUNTIME_ENV="$TEST_ROOT/runtime.env"; : >"$RUNTIME_ENV"; }
docker() {
  if [[ "$1 $2" == "image inspect" ]]; then printf "%s\n" "$TEST_CAPABILITY"; return 0; fi
  printf "%s\n" "$*" >>"$TEST_LOG"; return "$TEST_STATUS"
}
sync_release_data "$TEST_ROOT/release" "$TEST_ROLE"
"""
    )
    result = subprocess.run(
        [BASH, "-c", script],
        env={
            **os.environ,
            "TEST_HELPER": str(HELPER),
            "TEST_IMAGE": IMAGE,
            "TEST_LOG": str(log),
            "TEST_ROOT": str(tmp_path),
            "TEST_CAPABILITY": capability,
            "TEST_ROLE": role,
            "TEST_STATUS": str(status),
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == status, result.stderr
    calls = log.read_text().splitlines()
    assert len(calls) == 1
    command = (
        "go-typesense-exporter --sync-registry"
        if role == "csv-sync" or capability == "v1"
        else "uv run --no-sync crawler sync"
    )
    assert calls[0].endswith(IMAGE + " " + command)
    assert f"-v {tmp_path}/data:/app/data:ro" in calls[0]
    assert f"-e CRAWLER_DB_ROLE={role}" in calls[0]


@pytest.mark.parametrize("backend", ["native", "legacy"])
def test_native_recovery_environment_omits_retained_web_credential(
    tmp_path: Path,
    backend: str,
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    values = {
        "LOCAL_DATABASE_URL": "local-fixture",
        "TYPESENSE_HOST": "search-fixture",
        "TYPESENSE_PORT": "8108",
        "TYPESENSE_PROTOCOL": "http",
        "TYPESENSE_OPERATIONS_KEY": "fixture-operations",
        "WEB_DATABASE_URL": "legacy-only-fixture",
    }
    (release / "environment.env").write_text(
        "".join(f"{key}={value}\n" for key, value in values.items())
    )
    build = function("scripts/crawler-csv-sync-host.sh", "build_runtime_env", "sync_release_data")
    script = (
        build + '\nRUNTIME_ENV_ROOT="$TEST_ROOT"; '
        'build_runtime_env "$TEST_RELEASE" "$TEST_BACKEND"; cat "$RUNTIME_ENV"'
    )
    result = subprocess.run(
        [BASH, "-c", script],
        env={
            **os.environ,
            "TEST_ROOT": str(tmp_path),
            "TEST_RELEASE": str(release),
            "TEST_BACKEND": backend,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert ("WEB_DATABASE_URL=legacy-only-fixture" in result.stdout) == (backend == "legacy")
    assert "LOCAL_DATABASE_URL=local-fixture" in result.stdout
