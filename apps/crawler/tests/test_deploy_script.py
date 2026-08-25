from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path

import yaml

DEPLOY_SH = Path(__file__).resolve().parent.parent / "deploy.sh"
DEPLOY_HELPERS_SH = Path(__file__).resolve().parent.parent / "deploy_helpers.sh"
DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"
DOCKERIGNORE = Path(__file__).resolve().parent.parent / ".dockerignore"
XVFB_ENTRYPOINT = Path(__file__).resolve().parent.parent / "scripts" / "with-xvfb.sh"
COMPOSE_FILE = Path(__file__).resolve().parent.parent / "docker-compose.yml"
ROLLBACK_POOL_OVERRIDE = (
    Path(__file__).resolve().parent.parent / "rollback-pool-budget.override.yml"
)
DEPLOY_WORKFLOW = (
    Path(__file__).resolve().parents[3] / ".github/workflows/deploy-crawler-browser.yml"
)
MURMUR_DEPLOY_WORKFLOW = (
    Path(__file__).resolve().parents[3] / ".github/workflows/deploy-murmur-shim.yml"
)
SYNC_DATA_WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/sync-data.yml"
CSV_SYNC_HOST = Path(__file__).resolve().parents[3] / "scripts/crawler-csv-sync-host.sh"
BRIDGE_VERIFIER = Path(__file__).resolve().parents[3] / "scripts/verify-crawler-release-bridge.py"
POSTGRES_PREFLIGHT = (
    Path(__file__).resolve().parent.parent / "scripts/postgresql-operational-preflight.py"
)
REPO_ROOT = Path(__file__).resolve().parents[3]
AGENTS_MD = Path(__file__).resolve().parent.parent / "AGENTS.md"


def test_deploy_preflights_disk_before_pull_and_quiesce() -> None:
    script = DEPLOY_SH.read_text()

    preflight = script.index("\nensure_deploy_disk_headroom\n")
    pull = script.index("\npull_deploy_images\n")
    quiesce = script.index("docker compose stop --timeout 60")

    assert preflight < pull < quiesce


def test_deploy_refreshes_short_lived_ghcr_auth_before_release_mutation() -> None:
    script = DEPLOY_SH.read_text()
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    jobs = workflow["jobs"]
    deploy_step = next(
        step for step in jobs["deploy"]["steps"] if step.get("name") == "Deploy via SSH"
    )
    murmur_step = next(
        step
        for step in jobs["deploy"]["steps"]
        if step.get("name") == "Wait for same-revision murmur-shim deployment"
    )

    required = script.partition("required_vars=(")[2].partition(")")[0]
    assert "GHCR_PULL_USERNAME" in required
    assert "GHCR_PULL_TOKEN" in required
    credential_setup = script[
        script.index("initialize_ghcr_docker_config() {") : script.index(
            "stop_maintenance_window() {"
        )
    ]
    assert 'mktemp -d "${DEPLOY_DIR}/.ghcr-docker-config.XXXXXX"' in credential_setup
    assert 'chmod 0700 "$GHCR_DOCKER_CONFIG"' in credential_setup
    assert 'export DOCKER_CONFIG="$GHCR_DOCKER_CONFIG"' in credential_setup
    assert "docker login ghcr.io" in credential_setup
    assert "unset GHCR_PULL_TOKEN GHCR_PULL_USERNAME" in credential_setup
    assert "trap 'cleanup_ghcr_docker_config_on_exit $?' EXIT" in credential_setup

    initialize = script.index("\ninitialize_ghcr_docker_config\n")
    snapshot = script.index("\nsnapshot_active_deploy_specs\n")
    pull = script.index("\npull_deploy_images\n")
    assert initialize < snapshot < pull

    rollback_compose = script[
        script.index("rollback_compose() {") : script.index("rollback_compose_service_ready() {")
    ]
    rollback = script[script.index("rollback_deploy() {") : script.index("arm_deploy_rollback() {")]
    disarm_start = script.index("disarm_deploy_rollback() {")
    disarm = script[disarm_start : script.index("compose_service_ready() {", disarm_start)]
    assert 'clean_environment+=("DOCKER_CONFIG=$DOCKER_CONFIG")' in rollback_compose
    assert (
        'clean_environment+=("WEB_DATABASE_URL=$ROLLBACK_SYNC_WEB_DATABASE_URL")'
        in rollback_compose
    )
    assert "trap 'cleanup_ghcr_docker_config_on_exit $?' EXIT" in rollback
    assert disarm.index("cleanup_ghcr_docker_config") < disarm.index("trap - ERR EXIT")

    forwarded = deploy_step["with"]["envs"].split(",")
    assert "GHCR_PULL_USERNAME" in forwarded
    assert "GHCR_PULL_TOKEN" in forwarded
    assert deploy_step["env"]["GHCR_PULL_USERNAME"] == "${{ github.actor }}"
    assert deploy_step["env"]["GHCR_PULL_TOKEN"] == "${{ github.token }}"
    assert jobs["build"]["permissions"]["packages"] == "write"
    assert jobs["deploy"]["permissions"]["packages"] == "read"
    assert jobs["deploy"]["permissions"]["actions"] == "read"
    assert murmur_step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert jobs["promote"]["permissions"]["packages"] == "write"
    assert set(jobs["deploy"]["needs"]) == {"murmur", "build"}
    assert set(jobs["promote"]["needs"]) == {"build", "deploy"}

    env_start = script.index('cat > "$ENV_FILE"')
    persisted_env = script[env_start : script.index("EOF", env_start)]
    assert "GHCR_PULL_USERNAME" not in persisted_env
    assert "GHCR_PULL_TOKEN" not in persisted_env


def test_deploy_removes_transaction_scoped_ghcr_auth_on_success_and_failure(
    tmp_path: Path,
) -> None:
    script = DEPLOY_SH.read_text()
    credential_support = script[
        script.index("cleanup_ghcr_docker_config() {") : script.index("stop_maintenance_window() {")
    ]
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _write_executable(
        binary_dir / "docker",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "cat >/dev/null\n"
        'test "$1" = login\n'
        'test "$2" = ghcr.io\n'
        'test -d "$DOCKER_CONFIG"\n'
        'printf "{}\\n" >"$DOCKER_CONFIG/config.json"\n'
        'exit "${DOCKER_LOGIN_EXIT:-0}"\n',
    )
    harness = "\n".join(
        (
            "set -Eeuo pipefail",
            'DEPLOY_DIR="$TEST_DEPLOY_DIR"',
            'GHCR_DOCKER_CONFIG=""',
            'GHCR_PULL_USERNAME="github-actions[bot]"',
            'GHCR_PULL_TOKEN="short-lived-token"',
            credential_support,
            "initialize_ghcr_docker_config",
            'test -z "${GHCR_PULL_USERNAME+x}"',
            'test -z "${GHCR_PULL_TOKEN+x}"',
            'test -f "$DOCKER_CONFIG/config.json"',
        )
    )

    for login_exit in (0, 91):
        case_dir = tmp_path / f"case-{login_exit}"
        case_dir.mkdir()
        result = subprocess.run(
            ["bash", "-c", harness],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{binary_dir}:{os.environ['PATH']}",
                "TEST_DEPLOY_DIR": str(case_dir),
                "DOCKER_LOGIN_EXIT": str(login_exit),
            },
        )
        assert result.returncode == (0 if login_exit == 0 else 1), result.stderr
        assert list(case_dir.glob(".ghcr-docker-config.*")) == []


def test_deploy_quiesces_writers_before_migrations_and_schema_sync() -> None:
    script = DEPLOY_SH.read_text()

    quiesce = script.index("docker compose stop --timeout 60")
    migrate = script.index("alembic -c src/migrations/alembic.ini upgrade head")
    typesense_schema = script.index("uv run --no-sync crawler setup-typesense")
    sync = script.index("uv run --no-sync crawler sync", typesense_schema)
    nw_cutover = script.index("uv run --no-sync crawler repair-nw-provider-cutover")
    restart = script.index("docker compose up -d --remove-orphans", nw_cutover)

    assert quiesce < migrate < typesense_schema < sync < nw_cutover < restart


def test_operational_sync_entrypoints_are_local_and_typesense_only() -> None:
    script = DEPLOY_SH.read_text()
    sync_workflow = SYNC_DATA_WORKFLOW.read_text()
    sync_host = CSV_SYNC_HOST.read_text()

    assert "uv run --no-sync crawler sync\n" in script
    assert "uv run --no-sync crawler sync\n" in sync_host
    assert "--legacy-mirror" not in script
    assert "--legacy-mirror" not in sync_workflow
    assert "--legacy-mirror" not in sync_host


def test_production_env_omits_crawler_mirror_and_scopes_web_database() -> None:
    script = DEPLOY_SH.read_text()
    workflow = DEPLOY_WORKFLOW.read_text()
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    common_env = compose["x-common-env"]

    assert re.search(r"^DATABASE_URL=", script, re.MULTILINE) is None
    assert "DATABASE_URL_UNPOOLED" not in script
    assert "WEB_DATABASE_URL=${WEB_DATABASE_URL}" in script
    assert "JOBSEEK_DEPLOY_REVISION=${JOBSEEK_DEPLOY_REVISION}" in script
    assert "WEB_DATABASE_URL: ${{ secrets.DATABASE_URL_UNPOOLED }}" in workflow
    assert "DATABASE_URL" not in common_env
    assert "WEB_DATABASE_URL" not in common_env
    for service in ("worker-1", "worker-2", "worker-3", "browser-1", "exporter", "drain"):
        environment = compose["services"][service]["environment"]
        assert "DATABASE_URL" not in environment
        assert "WEB_DATABASE_URL" not in environment

    # Migration/schema one-offs receive no web-owned credential. Only the
    # explicit registry/watchlist sync invocation is allowlisted for it.
    forward_deploy = script[script.index("# ── Write env file") :]
    assert forward_deploy.count("-e WEB_DATABASE_URL") == 1
    assert "env -i \\\n" in script


def test_csv_sync_filters_the_host_environment_to_required_boundaries() -> None:
    workflow = SYNC_DATA_WORKFLOW.read_text()
    sync_host = CSV_SYNC_HOST.read_text()

    assert "RUNTIME_ENV_ROOT=${JOBSEEK_RUNTIME_ENV_ROOT:-/run/lock}" in sync_host
    assert 'mktemp "${RUNTIME_ENV_ROOT}/jobseek-csv-sync-env.XXXXXX"' in sync_host
    assert "chmod 0600" in sync_host
    assert '--env-file "$RUNTIME_ENV"' in sync_host
    assert "--env-file /home/deploy/.env" not in workflow
    assert "--env-file /home/deploy/.env" not in sync_host
    assert re.search(r"\bDATABASE_URL\b", sync_host) is None
    for key in (
        "LOCAL_DATABASE_URL",
        "WEB_DATABASE_URL",
        "TYPESENSE_HOST",
        "TYPESENSE_PORT",
        "TYPESENSE_PROTOCOL",
        "TYPESENSE_OPERATIONS_KEY",
    ):
        assert key in sync_host


def test_csv_sync_requires_the_committed_runtime_contract_before_publication() -> None:
    deploy = DEPLOY_SH.read_text()
    workflow = SYNC_DATA_WORKFLOW.read_text()
    sync_host = CSV_SYNC_HOST.read_text()

    assert "scripts/derive-crawler-runtime-contract.mjs" in workflow
    assert "scripts/verify-crawler-release-bridge.py" in workflow
    assert (
        "source: scripts/crawler-csv-sync-host.sh,"
        "scripts/verify-crawler-release-bridge.py" in workflow
    )
    assert "--kind data" in workflow
    assert "target_data_contract" in workflow
    assert "current_data_contract" in workflow
    assert "requested CSV snapshot is stale relative to current main" in workflow
    assert "current main CSV snapshot advanced before publication" in workflow
    assert "before_contract" in workflow
    assert "run_sync=false" in workflow
    assert "SYNC_RUNTIME_CONTRACT_SHA256" in workflow
    assert (
        "SYNC_RUNTIME_CONTRACT_SHA256: "
        "${{ steps.runtime_contract.outputs.runtime_contract_sha256 }}" in workflow
    )
    assert "current_runtime_contract" not in workflow
    assert "--check-runtime" in workflow
    assert 'if [[ "$status" -eq 75 ]]' in workflow
    assert workflow.index("--check-runtime") < workflow.index(
        "/usr/local/sbin/jobseek-maintenance window"
    )
    assert "JOBSEEK_RUNTIME_CONTRACT_SHA256" in deploy
    assert "JOBSEEK_RUNTIME_CONTRACT_SHA256" in sync_host
    assert ".crawler-active-release" in sync_host
    contract_gate = sync_host.index("verify_runtime_contract() {")
    credentials = sync_host.index("build_runtime_env() {")
    image = sync_host.index("CRAWLER_IMAGE_REF)")
    publication = sync_host.index("uv run --no-sync crawler sync")
    assert image < contract_gate < credentials < publication
    assert '"$ACTIVE_RELEASE/environment.env"' in sync_host
    assert '"$ACTIVE_RELEASE/success.env"' in sync_host
    assert "return 75" in sync_host
    main = sync_host[sync_host.index('USAGE="crawler-csv-sync-host.sh') :]
    actual_prepare = main.index('candidate_generation="$(')
    actual_noop = main.index(
        'if active_data_contract_matches "$DATA_CONTRACT_SHA256"', actual_prepare
    )
    runtime_gate = main.index('verify_runtime_contract "$RUNTIME_CONTRACT_SHA256"', actual_noop)
    assert actual_prepare < actual_noop < runtime_gate
    check_verification = main.index("verify_candidate_archive_contract")
    check_noop = main.index('if active_data_contract_matches "$DATA_CONTRACT_SHA256"')
    assert check_verification < check_noop
    assert "SYNC_DATA_CONTRACT_SHA256" in workflow
    assert "SYNC_ARCHIVE_SHA256" in workflow
    assert "/home/deploy/csv-candidates/" in workflow
    assert "/home/deploy/csv-overlay" not in workflow
    assert '"$ACTIVE_DATA_DIR:/app/data:ro"' in sync_host
    assert 'active_data_contract_matches "$DATA_CONTRACT_SHA256"' in sync_host


def test_csv_sync_runtime_contract_mismatch_is_retryable_but_corruption_is_fatal() -> None:
    sync_host = CSV_SYNC_HOST.read_text()
    verifier = sync_host[
        sync_host.index("load_committed_release() {") : sync_host.index(
            "\nactivate_release_generation() {"
        )
    ]
    assert "cmp -s \\\n    <(sed '/^COMPOSE_FILE=/d' \"$DEPLOY_ENV\")" in verifier
    assert '"$ACTIVE_RELEASE/environment.env" "$ACTIVE_RELEASE/success.env"' in verifier
    assert "committed crawler runtime contract is duplicated or invalid" in verifier
    assert "WAIT: CSV config requires a crawler runtime" in verifier
    assert "return 75" in verifier


def _create_v3_release(
    release_root: Path,
    name: str,
    runtime_contract: str,
    revision: str,
    board_value: str,
) -> tuple[Path, str]:
    release = release_root / name
    release.mkdir(parents=True)
    compose = release / "docker-compose.yml"
    environment = release / "environment.env"
    success = release / "success.env"
    compose.write_text("services: {}\n", encoding="utf-8")
    environment.write_text(
        "\n".join(
            (
                "CRAWLER_IMAGE_REF=ghcr.io/colophon-group/jobseek-crawler@sha256:" + "b" * 64,
                f"JOBSEEK_RUNTIME_CONTRACT_SHA256={runtime_contract}",
                "LOCAL_DATABASE_URL=postgresql://local",
                "WEB_DATABASE_URL=postgresql://web",
                "TYPESENSE_HOST=typesense",
                "TYPESENSE_PORT=8108",
                "TYPESENSE_PROTOCOL=http",
                "TYPESENSE_OPERATIONS_KEY=secret",
                "",
            )
        ),
        encoding="utf-8",
    )
    environment.chmod(0o600)
    success.write_text(f"JOBSEEK_RUNTIME_CONTRACT_SHA256={runtime_contract}\n", encoding="utf-8")
    data = release / "data"
    data.mkdir()
    board = data / "boards.csv"
    board.write_text(f"slug\n{board_value}\n", encoding="utf-8")
    board_digest = hashlib.sha256(board.read_bytes()).hexdigest()
    data_manifest = release / "data-files.sha256"
    data_manifest.write_text(f"{board_digest}  boards.csv\n", encoding="utf-8")
    data_contract = hashlib.sha256(data_manifest.read_bytes()).hexdigest()
    compose_digest = hashlib.sha256(compose.read_bytes()).hexdigest()
    environment_digest = hashlib.sha256(environment.read_bytes()).hexdigest()
    success_digest = hashlib.sha256(success.read_bytes()).hexdigest()
    (release / "docker-compose.sha256").write_text(f"{compose_digest}\n", encoding="utf-8")
    (release / "environment.sha256").write_text(f"{environment_digest}\n", encoding="utf-8")
    (release / "release.manifest").write_text(
        "\n".join(
            (
                "RELEASE_FORMAT_VERSION=3",
                f"COMPOSE_SHA256={compose_digest}",
                f"ENVIRONMENT_SHA256={environment_digest}",
                f"SUCCESS_SHA256={success_digest}",
                f"DATA_FILES_SHA256={hashlib.sha256(data_manifest.read_bytes()).hexdigest()}",
                f"DATA_CONTRACT_SHA256={data_contract}",
                f"DATA_REVISION={revision}",
                "HAS_IMAGE_OVERRIDE=0",
                "",
            )
        ),
        encoding="utf-8",
    )
    return release, data_contract


def _replace_release_manifest_value(release: Path, key: str, value: str | None) -> None:
    manifest = release / "release.manifest"
    lines = [
        line
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if not line.startswith(f"{key}=")
    ]
    if value is not None:
        lines.append(f"{key}={value}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _refresh_release_snapshot_digests(release: Path) -> None:
    environment_digest = hashlib.sha256((release / "environment.env").read_bytes()).hexdigest()
    success_digest = hashlib.sha256((release / "success.env").read_bytes()).hexdigest()
    (release / "environment.sha256").write_text(f"{environment_digest}\n", encoding="utf-8")
    _replace_release_manifest_value(release, "ENVIRONMENT_SHA256", environment_digest)
    _replace_release_manifest_value(release, "SUCCESS_SHA256", success_digest)


def _create_full_deploy_v3_release(
    release_root: Path,
    name: str,
    marker: str,
) -> tuple[Path, dict[str, str]]:
    release = release_root / name
    release.mkdir(parents=True)
    revision = marker * 40
    runtime_contract = marker * 64
    crawler_ref = "ghcr.io/colophon-group/jobseek-crawler@sha256:" + marker * 64
    browser_ref = "ghcr.io/colophon-group/jobseek-crawler-browser@sha256:" + marker * 64
    shim_ref = "ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + marker * 64
    compose = release / "docker-compose.yml"
    compose.write_text(f"services:\n  worker-1:\n    image: {crawler_ref}\n", encoding="utf-8")
    identity_lines = (
        f"CRAWLER_IMAGE_TAG=v0.13.{int(marker, 16)}",
        f"CRAWLER_IMAGE_REF={crawler_ref}",
        f"BROWSER_IMAGE_REF={browser_ref}",
        f"SHIM_IMAGE_REF={shim_ref}",
        f"JOBSEEK_DEPLOY_REVISION={revision}",
        f"JOBSEEK_RUNTIME_CONTRACT_SHA256={runtime_contract}",
    )
    environment = release / "environment.env"
    environment.write_text(
        "\n".join((*identity_lines, f"WEB_DATABASE_URL=postgresql://web-{marker}", "")),
        encoding="utf-8",
    )
    environment.chmod(0o600)
    success = release / "success.env"
    success.write_text("\n".join((*identity_lines, "")), encoding="utf-8")
    data = release / "data"
    data.mkdir()
    board = data / "boards.csv"
    board.write_text(f"slug\n{marker.upper()}\n", encoding="utf-8")
    board_digest = hashlib.sha256(board.read_bytes()).hexdigest()
    data_manifest = release / "data-files.sha256"
    data_manifest.write_text(f"{board_digest}  boards.csv\n", encoding="utf-8")
    data_contract = hashlib.sha256(data_manifest.read_bytes()).hexdigest()
    compose_digest = hashlib.sha256(compose.read_bytes()).hexdigest()
    environment_digest = hashlib.sha256(environment.read_bytes()).hexdigest()
    success_digest = hashlib.sha256(success.read_bytes()).hexdigest()
    (release / "docker-compose.sha256").write_text(f"{compose_digest}\n", encoding="utf-8")
    (release / "environment.sha256").write_text(f"{environment_digest}\n", encoding="utf-8")
    (release / "release.manifest").write_text(
        "\n".join(
            (
                "RELEASE_FORMAT_VERSION=3",
                f"COMPOSE_SHA256={compose_digest}",
                f"ENVIRONMENT_SHA256={environment_digest}",
                f"SUCCESS_SHA256={success_digest}",
                f"DATA_FILES_SHA256={data_contract}",
                f"DATA_CONTRACT_SHA256={data_contract}",
                f"DATA_REVISION={revision}",
                "HAS_IMAGE_OVERRIDE=0",
                "",
            )
        ),
        encoding="utf-8",
    )
    return release, {
        "browser_ref": browser_ref,
        "crawler_ref": crawler_ref,
        "data_contract": data_contract,
        "revision": revision,
        "runtime_contract": runtime_contract,
        "shim_ref": shim_ref,
    }


def _create_legacy_format2_release(
    release_root: Path,
    name: str,
    marker: str,
    *,
    newline_terminated: bool = True,
) -> tuple[Path, dict[str, str]]:
    release = release_root / name
    release.mkdir(parents=True)
    source_revision = marker * 40
    runtime_contract = marker * 64
    crawler_ref = "ghcr.io/colophon-group/jobseek-crawler@sha256:" + marker * 64
    browser_ref = "ghcr.io/colophon-group/jobseek-crawler-browser@sha256:" + marker * 64
    shim_ref = "ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + marker * 64
    compose = release / "docker-compose.yml"
    compose.write_text(
        "\n".join(
            (
                "services:",
                f"  worker-1: {{image: {crawler_ref}}}",
                f"  browser-1: {{image: {browser_ref}}}",
                f"  murmur-shim: {{image: {shim_ref}}}",
                "",
            )
        ),
        encoding="utf-8",
    )
    identity_lines = (
        f"CRAWLER_IMAGE_TAG=v0.13.{int(marker, 16)}",
        f"CRAWLER_IMAGE_REF={crawler_ref}",
        f"BROWSER_IMAGE_REF={browser_ref}",
        f"SHIM_IMAGE_REF={shim_ref}",
        f"JOBSEEK_DEPLOY_REVISION={source_revision}",
    )
    ending = "\n" if newline_terminated else ""
    environment = release / "environment.env"
    environment.write_text(
        "\n".join(
            (
                *identity_lines,
                "LOCAL_DATABASE_URL=postgresql://local",
                "WEB_DATABASE_URL=postgresql://web",
                "TYPESENSE_HOST=typesense",
                "TYPESENSE_PORT=8108",
                "TYPESENSE_PROTOCOL=http",
                "TYPESENSE_OPERATIONS_KEY=secret",
            )
        )
        + ending,
        encoding="utf-8",
    )
    environment.chmod(0o600)
    success = release / "success.env"
    success.write_text("\n".join(identity_lines) + ending, encoding="utf-8")
    override = release / "rollback-images.override.yml"
    override.write_text(compose.read_text(encoding="utf-8"), encoding="utf-8")
    compose_digest = hashlib.sha256(compose.read_bytes()).hexdigest()
    environment_digest = hashlib.sha256(environment.read_bytes()).hexdigest()
    success_digest = hashlib.sha256(success.read_bytes()).hexdigest()
    override_digest = hashlib.sha256(override.read_bytes()).hexdigest()
    (release / "docker-compose.sha256").write_text(f"{compose_digest}\n", encoding="utf-8")
    (release / "environment.sha256").write_text(f"{environment_digest}\n", encoding="utf-8")
    (release / "release.manifest").write_text(
        "\n".join(
            (
                "RELEASE_FORMAT_VERSION=2",
                f"COMPOSE_SHA256={compose_digest}",
                f"ENVIRONMENT_SHA256={environment_digest}",
                f"SUCCESS_SHA256={success_digest}",
                f"IMAGE_OVERRIDE_SHA256={override_digest}",
                "BOOTSTRAP_LEGACY=1",
                "",
            )
        ),
        encoding="utf-8",
    )
    return release, {
        "browser_ref": browser_ref,
        "crawler_ref": crawler_ref,
        "runtime_contract": runtime_contract,
        "shim_ref": shim_ref,
        "source_revision": source_revision,
    }


def _create_legacy_format1_release(
    release_root: Path,
    name: str,
    marker: str,
) -> tuple[Path, dict[str, str]]:
    release, identity = _create_legacy_format2_release(release_root, name, marker)
    (release / "rollback-images.override.yml").unlink()
    manifest = release / "release.manifest"
    lines = [
        line
        for line in manifest.read_text(encoding="utf-8").splitlines()
        if not line.startswith(("IMAGE_OVERRIDE_SHA256=", "BOOTSTRAP_LEGACY="))
    ]
    lines[0] = "RELEASE_FORMAT_VERSION=1"
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return release, identity


def _create_murmur_carried_bridge(
    previous: Path,
    release_root: Path,
    name: str,
    shim_marker: str,
) -> Path:
    target = release_root / name
    target.mkdir()
    shutil.copyfile(previous / "docker-compose.yml", target / "docker-compose.yml")
    shutil.copytree(previous / "data", target / "data")
    shutil.copyfile(previous / "data-files.sha256", target / "data-files.sha256")
    old_shim = next(
        line.split("=", 1)[1]
        for line in (previous / "environment.env").read_text(encoding="utf-8").splitlines()
        if line.startswith("SHIM_IMAGE_REF=")
    )
    new_shim = "ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + shim_marker * 64
    for evidence_name, mode in (("environment.env", 0o600), ("success.env", 0o644)):
        content = (
            (previous / evidence_name)
            .read_text(encoding="utf-8")
            .replace(f"SHIM_IMAGE_REF={old_shim}", f"SHIM_IMAGE_REF={new_shim}")
        )
        (target / evidence_name).write_text(content, encoding="utf-8")
        (target / evidence_name).chmod(mode)
    override = target / "rollback-images.override.yml"
    override.write_text(f"services:\n  murmur-shim:\n    image: {new_shim}\n", encoding="utf-8")
    compose_digest = hashlib.sha256((target / "docker-compose.yml").read_bytes()).hexdigest()
    env_digest = hashlib.sha256((target / "environment.env").read_bytes()).hexdigest()
    success_digest = hashlib.sha256((target / "success.env").read_bytes()).hexdigest()
    data_digest = hashlib.sha256((target / "data-files.sha256").read_bytes()).hexdigest()
    override_digest = hashlib.sha256(override.read_bytes()).hexdigest()
    data_revision = next(
        line.split("=", 1)[1]
        for line in (previous / "release.manifest").read_text(encoding="utf-8").splitlines()
        if line.startswith("DATA_REVISION=")
    )
    (target / "docker-compose.sha256").write_text(f"{compose_digest}\n", encoding="utf-8")
    (target / "environment.sha256").write_text(f"{env_digest}\n", encoding="utf-8")
    (target / "release.manifest").write_text(
        "\n".join(
            (
                "RELEASE_FORMAT_VERSION=3",
                f"COMPOSE_SHA256={compose_digest}",
                f"ENVIRONMENT_SHA256={env_digest}",
                f"SUCCESS_SHA256={success_digest}",
                f"DATA_FILES_SHA256={data_digest}",
                f"DATA_CONTRACT_SHA256={data_digest}",
                f"DATA_REVISION={data_revision}",
                "HAS_IMAGE_OVERRIDE=1",
                f"IMAGE_OVERRIDE_SHA256={override_digest}",
                "BOOTSTRAP_LEGACY=0",
                "",
            )
        ),
        encoding="utf-8",
    )
    workflow = yaml.safe_load(MURMUR_DEPLOY_WORKFLOW.read_text())
    remote_script = next(
        step["with"]["script"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy via SSH"
    )
    start = remote_script.index("carry_bridge_provenance() {")
    carry = remote_script[start : remote_script.index("\n\nprevious_redis_ref=", start)]
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
    result = subprocess.run(
        [
            bash,
            "-c",
            "set -euo pipefail\n"
            + carry
            + '\ncarry_bridge_provenance "$1" "$2" "$2/release.manifest"',
            "carry-bridge",
            str(previous),
            str(target),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    verified = subprocess.run(
        [
            "python3",
            str(BRIDGE_VERIFIER),
            "--generation",
            str(target),
            "--owner",
            "colophon-group",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr
    assert verified.stdout.strip() == "bridge"
    return target


def _install_csv_host_docker(binary_dir: Path) -> None:
    _write_executable(
        binary_dir / "docker",
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "if args[:1] == ['compose'] and 'config' in args and '--images' in args:\n"
        "    env_file = Path(args[args.index('--env-file') + 1])\n"
        "    values = {}\n"
        "    for line in env_file.read_text().splitlines():\n"
        "        if '=' in line: values.setdefault(*line.split('=', 1))\n"
        "    for key in ('CRAWLER_IMAGE_REF', 'BROWSER_IMAGE_REF', 'SHIM_IMAGE_REF'):\n"
        "        if not (Path(__file__).parent / f'omit-{key}').exists(): print(values[key])\n"
        "elif args[:1] == ['run']:\n"
        "    log = os.environ.get('TEST_CSV_SYNC_LOG')\n"
        "    if log:\n"
        "        volume = next((item for item in args if item.endswith(':/app/data:ro')), '')\n"
        "        Path(log).write_text(volume.split(':', 1)[0] + '\\n')\n"
        "else:\n"
        "    raise AssertionError(args)\n",
    )


def _csv_host_test_environment(
    tmp_path: Path,
    release_root: Path,
    active: Path,
    live_env: Path,
    candidates: Path,
) -> dict[str, str]:
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    _write_executable(
        binaries / "stat",
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "print(oct(os.stat(sys.argv[-1]).st_mode & 0o777)[2:])\n",
    )
    _write_executable(
        binaries / "sha256sum",
        "#!/usr/bin/env python3\n"
        "import hashlib, pathlib, sys\n"
        "path = pathlib.Path(sys.argv[-1])\n"
        "print(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}')\n",
    )
    return {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "JOBSEEK_DEPLOY_DIR": str(tmp_path),
        "JOBSEEK_DEPLOY_ENV": str(live_env),
        "JOBSEEK_ACTIVE_RELEASE_POINTER": str(active),
        "JOBSEEK_ACTIVE_RELEASE_ROOT": str(release_root),
        "JOBSEEK_PUBLICATION_JOURNAL": str(tmp_path / "journal"),
        "JOBSEEK_CANDIDATE_ROOT": str(candidates),
        "JOBSEEK_RUNTIME_ENV_ROOT": str(tmp_path),
    }


def _create_csv_candidate(
    candidate_root: Path,
    revision: str,
    run_id: int,
    attempt: int,
    files: dict[str, bytes],
    runtime_contract: str | None = None,
    compatible_revisions: list[str] | None = None,
) -> tuple[str, str, str]:
    candidate_id = f"{revision}-{run_id}-{attempt}"
    candidate = candidate_root / candidate_id
    payload = candidate / "payload"
    data = payload / "data"
    data.mkdir(parents=True)
    rows = []
    for relative, content in sorted(files.items()):
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        rows.append((relative, hashlib.sha256(content).hexdigest()))
    manifest = payload / "data-files.sha256"
    manifest.write_text(
        "".join(f"{digest}  {relative}\n" for relative, digest in rows),
        encoding="utf-8",
    )
    attestation = None
    if runtime_contract is not None:
        revisions = compatible_revisions or [revision]
        attestation = payload / "runtime-attestation.env"
        attestation.write_text(
            "".join(
                (
                    "RUNTIME_ATTESTATION_FORMAT_VERSION=1\n",
                    f"PREVIOUS_REVISION={revision}\n",
                    f"RUNTIME_CONTRACT_SHA256={runtime_contract}\n",
                    *(f"COMPATIBLE_REVISION={item}\n" for item in revisions),
                )
            ),
            encoding="utf-8",
        )
    archive = candidate / "csv-snapshot.tar"
    with tarfile.open(archive, "w") as bundle:
        bundle.add(data, arcname="data")
        bundle.add(manifest, arcname="data-files.sha256")
        if attestation is not None:
            bundle.add(attestation, arcname="runtime-attestation.env")
    data_contract = hashlib.sha256(manifest.read_bytes()).hexdigest()
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    shutil.rmtree(payload)
    return candidate_id, data_contract, archive_sha


def test_legacy_format2_bootstrap_attests_old_runtime_and_is_idempotent(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    legacy, identity = _create_legacy_format2_release(release_root, "legacy.production", "b")
    previous_revision = "c" * 40
    active = tmp_path / ".crawler-active-release"
    active.symlink_to(legacy)
    live_env = tmp_path / ".env"
    shutil.copyfile(legacy / "environment.env", live_env)
    live_env.chmod(0o600)
    env = _csv_host_test_environment(tmp_path, release_root, active, live_env, candidates)
    _install_csv_host_docker(tmp_path / "bin")
    env["TEST_CSV_SYNC_LOG"] = str(tmp_path / "sync.log")
    candidate_id, data_contract, archive_sha = _create_csv_candidate(
        candidates,
        previous_revision,
        71,
        1,
        {"boards.csv": b"slug\nB\n"},
        identity["runtime_contract"],
        [previous_revision, identity["source_revision"]],
    )
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
    command = [
        bash,
        str(CSV_SYNC_HOST),
        "--bootstrap-current",
        previous_revision,
        identity["runtime_contract"],
        data_contract,
        candidate_id,
        archive_sha,
    ]
    first = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    assert first.returncode == 0, first.stderr
    bridged = active.resolve()
    assert bridged != legacy.resolve()
    assert (
        (bridged / "release.manifest")
        .read_text(encoding="utf-8")
        .startswith("RELEASE_FORMAT_VERSION=3\n")
    )
    for evidence in (bridged / "environment.env", bridged / "success.env"):
        assert (
            evidence.read_text(encoding="utf-8").count(
                f"JOBSEEK_RUNTIME_CONTRACT_SHA256={identity['runtime_contract']}\n"
            )
            == 1
        )
    bridge_manifest = (bridged / "release.manifest").read_text(encoding="utf-8")
    assert "LEGACY_BRIDGE_FORMAT_VERSION=1\n" in bridge_manifest
    assert f"LEGACY_SOURCE_REVISION={identity['source_revision']}\n" in bridge_manifest
    assert f"LEGACY_SOURCE_CRAWLER_IMAGE_REF={identity['crawler_ref']}\n" in bridge_manifest
    assert (bridged / "data" / "boards.csv").read_bytes() == b"slug\nB\n"
    assert Path(env["TEST_CSV_SYNC_LOG"]).read_text(encoding="utf-8").strip() == str(
        bridged / "data"
    )
    assert not (candidates / candidate_id).exists()

    # A workflow retry re-copies the same immutable archive. Once the bridge
    # is active, bootstrap is an exact no-op and consumes the duplicate.
    repeated_id, repeated_contract, repeated_archive = _create_csv_candidate(
        candidates,
        previous_revision,
        71,
        1,
        {"boards.csv": b"slug\nB\n"},
        identity["runtime_contract"],
        [previous_revision, identity["source_revision"]],
    )
    assert (repeated_id, repeated_contract) == (candidate_id, data_contract)
    command[7] = repeated_archive
    retry = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    assert retry.returncode == 0, retry.stderr
    assert active.resolve() == bridged
    assert not (candidates / candidate_id).exists()


def test_legacy_format1_bootstrap_rejects_any_unattested_override_without_mutation(
    tmp_path: Path,
) -> None:
    for kind in ("regular", "dangling"):
        root = tmp_path / kind
        release_root = root / "releases"
        candidates = root / "candidates"
        candidates.mkdir(parents=True)
        legacy, identity = _create_legacy_format1_release(release_root, "legacy.production", "b")
        override = legacy / "rollback-images.override.yml"
        if kind == "regular":
            wrong_ref = "ghcr.io/colophon-group/jobseek-crawler@sha256:" + "f" * 64
            override.write_text(
                f"services:\n  worker-1:\n    image: {wrong_ref}\n", encoding="utf-8"
            )
        else:
            override.symlink_to(root / "missing-override.yml")
        active = root / ".crawler-active-release"
        active.symlink_to(legacy)
        live_env = root / ".env"
        shutil.copyfile(legacy / "environment.env", live_env)
        live_env.chmod(0o600)
        env = _csv_host_test_environment(root, release_root, active, live_env, candidates)
        _install_csv_host_docker(root / "bin")
        sync_log = root / "sync.log"
        env["TEST_CSV_SYNC_LOG"] = str(sync_log)
        previous_revision = "c" * 40
        candidate_id, data_contract, archive_sha = _create_csv_candidate(
            candidates,
            previous_revision,
            74,
            1,
            {"boards.csv": b"slug\nB\n"},
            identity["runtime_contract"],
            [previous_revision, identity["source_revision"]],
        )
        bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
        result = subprocess.run(
            [
                bash,
                str(CSV_SYNC_HOST),
                "--bootstrap-current",
                previous_revision,
                identity["runtime_contract"],
                data_contract,
                candidate_id,
                archive_sha,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode != 0
        assert (
            "format-1 crawler release contains unattested image-override residue" in result.stderr
        )
        assert active.resolve() == legacy.resolve()
        assert not (candidates / candidate_id).exists()
        assert list(release_root.glob("data-*")) == []
        assert not sync_log.exists()
        assert not (root / "journal").exists()


def test_legacy_runtime_bridge_rejects_unbound_evidence_and_cleans_failed_generation(
    tmp_path: Path,
) -> None:
    def setup_case(name: str, *, newline_terminated: bool = True):
        root = tmp_path / name
        release_root = root / "releases"
        candidates = root / "candidates"
        candidates.mkdir(parents=True)
        legacy, identity = _create_legacy_format2_release(
            release_root,
            "legacy.production",
            "b",
            newline_terminated=newline_terminated,
        )
        active = root / ".crawler-active-release"
        active.symlink_to(legacy)
        live_env = root / ".env"
        shutil.copyfile(legacy / "environment.env", live_env)
        live_env.chmod(0o600)
        env = _csv_host_test_environment(root, release_root, active, live_env, candidates)
        _install_csv_host_docker(root / "bin")
        previous_revision = "c" * 40
        candidate_id, data_contract, archive_sha = _create_csv_candidate(
            candidates,
            previous_revision,
            72,
            1,
            {"boards.csv": b"slug\nB\n"},
            identity["runtime_contract"],
            [previous_revision, identity["source_revision"]],
        )
        bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
        command = [
            bash,
            str(CSV_SYNC_HOST),
            "--bootstrap-current",
            previous_revision,
            identity["runtime_contract"],
            data_contract,
            candidate_id,
            archive_sha,
        ]
        return release_root, candidates, legacy, active, live_env, env, identity, command

    release_root, _, legacy, active, _, env, identity, command = setup_case("contract")
    wrong_contract = command.copy()
    wrong_contract[4] = "f" * 64
    result = subprocess.run(wrong_contract, check=False, capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert "attestation identity is mismatched" in result.stderr
    assert active.resolve() == legacy.resolve()
    assert list(release_root.glob("data-*")) == []

    release_root, _, legacy, active, _, env, _, command = setup_case("revision")
    # The archive remains bound to the exact before revision, but no longer
    # attests the older active deploy revision.
    candidate = Path(env["JOBSEEK_CANDIDATE_ROOT"]) / command[6] / "csv-snapshot.tar"
    extract = tmp_path / "revision-payload"
    with tarfile.open(candidate) as bundle:
        bundle.extractall(extract, filter="data")
    (extract / "runtime-attestation.env").write_text(
        "\n".join(
            (
                "RUNTIME_ATTESTATION_FORMAT_VERSION=1",
                f"PREVIOUS_REVISION={command[3]}",
                f"RUNTIME_CONTRACT_SHA256={command[4]}",
                f"COMPATIBLE_REVISION={command[3]}",
                "",
            )
        ),
        encoding="utf-8",
    )
    with tarfile.open(candidate, "w") as bundle:
        bundle.add(extract / "data", arcname="data")
        bundle.add(extract / "data-files.sha256", arcname="data-files.sha256")
        bundle.add(extract / "runtime-attestation.env", arcname="runtime-attestation.env")
    command[7] = hashlib.sha256(candidate.read_bytes()).hexdigest()
    result = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert "outside the attested runtime epoch" in result.stderr
    assert active.resolve() == legacy.resolve()
    assert list(release_root.glob("data-*")) == []

    release_root, _, legacy, active, _, env, _, command = setup_case("image")
    (Path(env["PATH"].split(":", 1)[0]) / "omit-CRAWLER_IMAGE_REF").touch()
    result = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert "does not bind CRAWLER_IMAGE_REF" in result.stderr
    assert active.resolve() == legacy.resolve()
    assert list(release_root.glob("data-*")) == []

    release_root, _, legacy, active, _, env, _, command = setup_case("missing")
    (legacy / "rollback-images.override.yml").unlink()
    result = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert "image override failed verification" in result.stderr
    assert active.resolve() == legacy.resolve()
    assert list(release_root.glob("data-*")) == []

    (
        release_root,
        candidates,
        legacy,
        active,
        live_env,
        env,
        identity,
        command,
    ) = setup_case("cleanup", newline_terminated=False)
    result = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    assert result.returncode != 0
    assert "not canonically newline-terminated" in result.stderr
    assert active.resolve() == legacy.resolve()
    assert list(release_root.glob("data-*")) == []
    for path in (legacy / "environment.env", legacy / "success.env"):
        path.write_bytes(path.read_bytes() + b"\n")
    _refresh_release_snapshot_digests(legacy)
    shutil.copyfile(legacy / "environment.env", live_env)
    live_env.chmod(0o600)
    repeated_id, repeated_contract, repeated_archive = _create_csv_candidate(
        candidates,
        command[3],
        72,
        1,
        {"boards.csv": b"slug\nB\n"},
        identity["runtime_contract"],
        [command[3], identity["source_revision"]],
    )
    assert (repeated_id, repeated_contract) == (command[6], command[5])
    command[7] = repeated_archive
    retry = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    assert retry.returncode == 0, retry.stderr
    assert active.resolve() != legacy.resolve()


def test_csv_publication_timeline_does_not_deadlock_across_later_runtime_deploy(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    runtime_r0 = "1" * 64
    runtime_r1 = "2" * 64
    revision_a = "a" * 40
    revision_b = "b" * 40
    revision_c = "c" * 40
    release_a, data_a = _create_v3_release(
        release_root, "release-a.timeline", runtime_r0, revision_a, "a"
    )
    active = tmp_path / ".crawler-active-release"
    active.symlink_to(release_a)
    live_env = tmp_path / ".env"
    shutil.copyfile(release_a / "environment.env", live_env)
    live_env.chmod(0o600)
    env = _csv_host_test_environment(tmp_path, release_root, active, live_env, candidates)
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"

    # R0/A is live. A delayed D(B) exists when a later runtime-only R1 deploy
    # starts. Bootstrap must retain A as the real rollback state, even though
    # pre-push main now contains B.
    candidate_b, data_b, archive_b = _create_csv_candidate(
        candidates, revision_b, 10, 1, {"boards.csv": b"slug\nb\n"}
    )
    bootstrap_b = subprocess.run(
        [
            bash,
            str(CSV_SYNC_HOST),
            "--bootstrap-current",
            revision_b,
            runtime_r0,
            data_b,
            candidate_b,
            archive_b,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert bootstrap_b.returncode == 0, bootstrap_b.stderr
    assert active.resolve() == release_a.resolve()
    assert not (candidates / candidate_b).exists()

    # Model the successful R1 full deploy: its immutable image contains B.
    # The delayed D(B) job remains bound to R0, but can now finish as an exact
    # data-contract no-op instead of waiting forever for runtime R0.
    release_b, release_b_contract = _create_v3_release(
        release_root, "release-b.timeline", runtime_r1, revision_b, "b"
    )
    assert release_b_contract == data_b
    active.unlink()
    active.symlink_to(release_b)
    shutil.copyfile(release_b / "environment.env", live_env)
    live_env.chmod(0o600)
    candidate_b, repeated_data_b, archive_b = _create_csv_candidate(
        candidates, revision_b, 10, 1, {"boards.csv": b"slug\nb\n"}
    )
    assert repeated_data_b == data_b
    delayed_command = [
        bash,
        str(CSV_SYNC_HOST),
        revision_b,
        runtime_r0,
        data_b,
        candidate_b,
        archive_b,
    ]
    delayed_check = subprocess.run(
        [*delayed_command, "--check-runtime"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert delayed_check.returncode == 0, delayed_check.stderr
    delayed_publish = subprocess.run(
        delayed_command, check=False, capture_output=True, text=True, env=env
    )
    assert delayed_publish.returncode == 0, delayed_publish.stderr
    assert active.resolve() == release_b.resolve()
    assert not (candidates / candidate_b).exists()

    # A failed later C candidate correctly leaves B active. A subsequent full
    # deploy with yet another pre-push snapshot must still retain B as rollback
    # evidence instead of demanding equality with mutable main.
    candidate_c, data_c, archive_c = _create_csv_candidate(
        candidates, revision_c, 11, 1, {"boards.csv": b"slug\nc\n"}
    )
    bootstrap_c = subprocess.run(
        [
            bash,
            str(CSV_SYNC_HOST),
            "--bootstrap-current",
            revision_c,
            runtime_r1,
            data_c,
            candidate_c,
            archive_c,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert bootstrap_c.returncode == 0, bootstrap_c.stderr
    assert active.resolve() == release_b.resolve()
    assert not (candidates / candidate_c).exists()


def test_csv_publication_rejects_tree_a_claiming_active_contract_b_before_noop(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    runtime = "1" * 64
    revision_b = "b" * 40
    release_b, data_b = _create_v3_release(
        release_root, "release-b.contract", runtime, revision_b, "b"
    )
    active = tmp_path / ".crawler-active-release"
    active.symlink_to(release_b)
    live_env = tmp_path / ".env"
    shutil.copyfile(release_b / "environment.env", live_env)
    live_env.chmod(0o600)
    revision_a = "a" * 40
    candidate_a, data_a, archive_a = _create_csv_candidate(
        candidates, revision_a, 20, 1, {"boards.csv": b"slug\na\n"}
    )
    assert data_a != data_b
    env = _csv_host_test_environment(tmp_path, release_root, active, live_env, candidates)
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
    command = [
        bash,
        str(CSV_SYNC_HOST),
        revision_a,
        runtime,
        data_b,
        candidate_a,
        archive_a,
    ]
    for args in ([*command, "--check-runtime"], command):
        result = subprocess.run(args, check=False, capture_output=True, text=True, env=env)
        assert result.returncode != 0
        assert "data contract does not match its exact tree" in result.stderr
        assert "already committed" not in result.stderr
    assert active.resolve() == release_b.resolve()
    assert (candidates / candidate_a).exists()
    assert list(release_root.glob(f"data-{revision_a}.*")) == []


def test_full_deploy_rejects_image_tree_a_claiming_git_contract_b(tmp_path: Path) -> None:
    script = DEPLOY_SH.read_text()
    csv_helpers = script[
        script.index("verify_exact_csv_tree() {") : script.index("\nread_exact_release_value() {")
    ]
    forward_helpers = script[
        script.index("prepare_forward_data_snapshot() {") : script.index(
            "\nsnapshot_active_deploy_specs() {"
        )
    ]
    image_data = tmp_path / "image-data"
    image_data.mkdir()
    (image_data / "boards.csv").write_bytes(b"slug\na\n")
    b_digest = hashlib.sha256(b"slug\nb\n").hexdigest()
    b_manifest = f"{b_digest}  boards.csv\n".encode()
    expected_contract_b = hashlib.sha256(b_manifest).hexdigest()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    _write_executable(
        binaries / "docker",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'case "$1" in\n'
        "  create) printf 'candidate-container\\n' ;;\n"
        '  cp) cp -R "$TEST_IMAGE_DATA/." "$3" ;;\n'
        "  rm) : ;;\n"
        "  *) exit 91 ;;\n"
        "esac\n",
    )
    _write_executable(
        binaries / "sha256sum",
        "#!/usr/bin/env python3\n"
        "import hashlib, pathlib, sys\n"
        "path = pathlib.Path(sys.argv[-1])\n"
        "print(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}')\n",
    )
    harness = "\n".join(
        (
            "set -euo pipefail",
            'DEPLOY_DIR="$TEST_DEPLOY_DIR"',
            'JOBSEEK_DEPLOY_REVISION="' + "a" * 40 + '"',
            'CRAWLER_IMAGE_REF="ghcr.io/example/crawler@sha256:' + "1" * 64 + '"',
            'JOBSEEK_DATA_CONTRACT_SHA256="$TEST_EXPECTED_CONTRACT"',
            'FORWARD_DATA_STAGING_ROOT=""',
            'FORWARD_DATA_SNAPSHOT=""',
            'FORWARD_DATA_FILES_MANIFEST=""',
            csv_helpers,
            forward_helpers,
            "set +e",
            "prepare_forward_data_snapshot",
            "prepare_status=$?",
            "set -e",
            'test "$prepare_status" -ne 0',
            'test -n "$FORWARD_DATA_STAGING_ROOT"',
            'test -d "$FORWARD_DATA_STAGING_ROOT"',
            "cleanup_forward_data_snapshot",
            'test ! -e "$FORWARD_DATA_STAGING_ROOT"',
        )
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "TEST_DEPLOY_DIR": str(tmp_path),
            "TEST_IMAGE_DATA": str(image_data),
            "TEST_EXPECTED_CONTRACT": expected_contract_b,
        },
    )
    assert result.returncode == 0, result.stderr
    assert "does not match the expected data contract" in result.stderr
    assert list(tmp_path.glob(".crawler-forward-data-*")) == []


def test_publication_retention_preserves_live_journal_rollback_and_inflight_state(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    candidate_root = tmp_path / "candidates"
    candidate_root.mkdir()
    old = 1_700_000_000
    runtime = "1" * 64
    active_release, _ = _create_v3_release(
        release_root, f"release-{'a' * 40}.active", runtime, "a" * 40, "active"
    )
    valid_rollback, _ = _create_v3_release(
        release_root, f"release-{'b' * 40}.rollback", runtime, "b" * 40, "rollback"
    )
    valid_newest, _ = _create_v3_release(
        release_root, f"release-{'c' * 40}.newest", runtime, "c" * 40, "newest"
    )
    journal_previous = release_root / f"data-{'d' * 40}.journal-previous"
    journal_target = release_root / f"data-{'e' * 40}.journal-target"
    explicit_rollback = release_root / f"data-{'f' * 40}.explicit"
    for generation in (journal_previous, journal_target, explicit_rollback):
        generation.mkdir()
        (generation / "partial-residue.bin").touch()
        os.truncate(generation / "partial-residue.bin", 7_400_000)
    corrupt_newest = []
    for index in range(5):
        generation, _ = _create_v3_release(
            release_root,
            f"release-{'1' * 40}.corrupt-{index}",
            runtime,
            f"{index + 1:040x}",
            f"corrupt-{index}",
        )
        # These look complete but fail the same digest/tree verifier used for
        # active generations, so they must not consume the safe keep window.
        (generation / "data" / "boards.csv").write_text(
            f"slug\ntampered-{index}\n", encoding="utf-8"
        )
        corrupt_newest.append(generation)
    ordered = [
        active_release,
        journal_previous,
        journal_target,
        explicit_rollback,
        valid_rollback,
        *corrupt_newest,
        valid_newest,
    ]
    for index, generation in enumerate(ordered):
        os.utime(generation, (old + index, old + index))
    active = tmp_path / ".crawler-active-release"
    active.symlink_to(active_release)
    live_env = tmp_path / ".env"
    shutil.copyfile(active_release / "environment.env", live_env)
    live_env.chmod(0o600)
    journal = tmp_path / "journal"
    journal.write_text(
        "\n".join(
            (
                "PUBLICATION_FORMAT_VERSION=1",
                "SYNC_SUCCEEDED=0",
                "RECOVERY_ACTION=restore-previous",
                f"PREVIOUS_RELEASE={journal_previous}",
                f"TARGET_RELEASE={journal_target}",
                "",
            )
        ),
        encoding="utf-8",
    )
    journal.chmod(0o600)

    revision = "d" * 40
    protected_candidate = f"{revision}-1-1"
    stale_candidate = f"{revision}-2-1"
    young_candidate = f"{revision}-3-1"
    for candidate in (protected_candidate, stale_candidate, young_candidate):
        path = candidate_root / candidate
        path.mkdir()
        (path / "csv-snapshot.tar").touch()
        os.truncate(path / "csv-snapshot.tar", 7_400_000)
    os.utime(candidate_root / protected_candidate, (old, old))
    os.utime(candidate_root / stale_candidate, (old, old))
    external = tmp_path / "must-not-delete"
    external.mkdir()
    (external / "sentinel").write_text("safe", encoding="utf-8")
    (candidate_root / f"{'e' * 40}-4-1").symlink_to(external)
    (release_root / "legacy.unsafe-link").symlink_to(external)
    protected_forward = tmp_path / f".crawler-forward-data-{'a' * 40}.inflight"
    stale_forward = tmp_path / f".crawler-forward-data-{'b' * 40}.stale"
    young_forward = tmp_path / f".crawler-forward-data-{'c' * 40}.young"
    for staging in (protected_forward, stale_forward, young_forward):
        staging.mkdir()
        (staging / "data-residue.bin").touch()
        os.truncate(staging / "data-residue.bin", 7_400_000)
    os.utime(protected_forward, (old, old))
    os.utime(stale_forward, (old, old))
    (tmp_path / f".crawler-forward-data-{'d' * 40}.unsafe").symlink_to(external)

    env = _csv_host_test_environment(tmp_path, release_root, active, live_env, candidate_root)
    env.update(
        {
            "JOBSEEK_PUBLICATION_KEEP_GENERATIONS": "2",
            "JOBSEEK_PUBLICATION_GRACE_SECONDS": "3600",
        }
    )
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"

    result = subprocess.run(
        [
            bash,
            str(CSV_SYNC_HOST),
            "--prune-only",
            str(explicit_rollback),
            protected_candidate,
            str(protected_forward),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    for preserved in (
        active_release,
        journal_previous,
        journal_target,
        explicit_rollback,
        valid_rollback,
        valid_newest,
    ):
        assert preserved.exists()
    assert all(not generation.exists() for generation in corrupt_newest)
    assert (candidate_root / protected_candidate).exists()
    assert not (candidate_root / stale_candidate).exists()
    assert (candidate_root / young_candidate).exists()
    assert protected_forward.exists()
    assert not stale_forward.exists()
    assert young_forward.exists()
    assert (external / "sentinel").read_text(encoding="utf-8") == "safe"


def test_publication_retention_fails_closed_on_malformed_journal(tmp_path: Path) -> None:
    release_root = tmp_path / "releases"
    candidate_root = tmp_path / "candidates"
    release = release_root / f"release-{'a' * 40}.active"
    stale = release_root / f"data-{'b' * 40}.stale"
    release.mkdir(parents=True)
    stale.mkdir()
    candidate_root.mkdir()
    active = tmp_path / ".crawler-active-release"
    active.symlink_to(release)
    journal = tmp_path / "journal"
    journal.write_text(
        "PUBLICATION_FORMAT_VERSION=1\nPUBLICATION_FORMAT_VERSION=1\n",
        encoding="utf-8",
    )
    journal.chmod(0o600)
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
    result = subprocess.run(
        [bash, str(CSV_SYNC_HOST), "--prune-only"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "JOBSEEK_DEPLOY_DIR": str(tmp_path),
            "JOBSEEK_ACTIVE_RELEASE_POINTER": str(active),
            "JOBSEEK_ACTIVE_RELEASE_ROOT": str(release_root),
            "JOBSEEK_PUBLICATION_JOURNAL": str(journal),
            "JOBSEEK_CANDIDATE_ROOT": str(candidate_root),
            "JOBSEEK_PUBLICATION_KEEP_GENERATIONS": "1",
            "JOBSEEK_PUBLICATION_GRACE_SECONDS": "0",
        },
    )
    assert result.returncode != 0
    assert stale.exists()


def test_csv_snapshot_verifier_rejects_tamper_residue_and_deleted_files(
    tmp_path: Path,
) -> None:
    sync_host = CSV_SYNC_HOST.read_text()
    verifier = sync_host[
        sync_host.index("verify_exact_csv_tree() {") : sync_host.index(
            "\nresolve_active_release() {"
        )
    ]
    data = tmp_path / "data"
    data.mkdir()
    boards = data / "boards.csv"
    companies = data / "companies.csv"
    boards.write_text("slug\nb\n", encoding="utf-8")
    companies.write_text("slug\nc\n", encoding="utf-8")
    manifest = tmp_path / "data-files.sha256"

    def rewrite_manifest() -> None:
        rows = sorted(
            (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in data.iterdir()
            if path.suffix == ".csv"
        )
        manifest.write_text(
            "".join(f"{digest}  {name}\n" for name, digest in rows),
            encoding="utf-8",
        )

    def verify() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                f'{verifier}\nverify_exact_csv_tree "$1" "$2"',
                "_",
                str(data),
                str(manifest),
            ],
            check=False,
            capture_output=True,
            text=True,
        )

    rewrite_manifest()
    assert verify().returncode == 0
    boards.write_text("slug\ntampered\n", encoding="utf-8")
    assert verify().returncode != 0
    boards.write_text("slug\nb\n", encoding="utf-8")
    (data / "failed-candidate.csv").write_text("slug\nresidue\n", encoding="utf-8")
    assert verify().returncode != 0
    (data / "failed-candidate.csv").unlink()
    companies.unlink()
    assert verify().returncode != 0


def test_csv_host_rejects_archive_digest_mismatch_and_live_env_drift(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    release = release_root / "release-a"
    release.mkdir(parents=True)
    compose = release / "docker-compose.yml"
    environment = release / "environment.env"
    success = release / "success.env"
    compose.write_text("services: {}\n", encoding="utf-8")
    runtime_contract = "a" * 64
    environment.write_text(
        "\n".join(
            (
                "CRAWLER_IMAGE_REF=ghcr.io/colophon-group/jobseek-crawler@sha256:" + "b" * 64,
                f"JOBSEEK_RUNTIME_CONTRACT_SHA256={runtime_contract}",
                "LOCAL_DATABASE_URL=postgresql://local",
                "WEB_DATABASE_URL=postgresql://web",
                "TYPESENSE_HOST=typesense",
                "TYPESENSE_PORT=8108",
                "TYPESENSE_PROTOCOL=http",
                "TYPESENSE_OPERATIONS_KEY=secret",
                "",
            )
        ),
        encoding="utf-8",
    )
    environment.chmod(0o600)
    success.write_text(f"JOBSEEK_RUNTIME_CONTRACT_SHA256={runtime_contract}\n", encoding="utf-8")
    data = release / "data"
    data.mkdir()
    (data / "boards.csv").write_text("slug\ncommitted\n", encoding="utf-8")
    data_file_digest = hashlib.sha256((data / "boards.csv").read_bytes()).hexdigest()
    data_manifest = release / "data-files.sha256"
    data_manifest.write_text(f"{data_file_digest}  boards.csv\n", encoding="utf-8")
    compose_digest = hashlib.sha256(compose.read_bytes()).hexdigest()
    env_digest = hashlib.sha256(environment.read_bytes()).hexdigest()
    success_digest = hashlib.sha256(success.read_bytes()).hexdigest()
    (release / "docker-compose.sha256").write_text(f"{compose_digest}\n", encoding="utf-8")
    (release / "environment.sha256").write_text(f"{env_digest}\n", encoding="utf-8")
    (release / "release.manifest").write_text(
        "\n".join(
            (
                "RELEASE_FORMAT_VERSION=3",
                f"COMPOSE_SHA256={compose_digest}",
                f"ENVIRONMENT_SHA256={env_digest}",
                f"SUCCESS_SHA256={success_digest}",
                f"DATA_FILES_SHA256={hashlib.sha256(data_manifest.read_bytes()).hexdigest()}",
                f"DATA_CONTRACT_SHA256={hashlib.sha256(data_manifest.read_bytes()).hexdigest()}",
                f"DATA_REVISION={'e' * 40}",
                "HAS_IMAGE_OVERRIDE=0",
                "",
            )
        ),
        encoding="utf-8",
    )
    active = tmp_path / ".crawler-active-release"
    active.symlink_to(release)
    live_env = tmp_path / ".env"
    shutil.copyfile(environment, live_env)
    live_env.chmod(0o600)
    candidates = tmp_path / "candidates"
    revision = "c" * 40
    candidate_id = f"{revision}-1-1"
    candidate = candidates / candidate_id
    candidate.mkdir(parents=True)
    (candidate / "csv-snapshot.tar").write_bytes(b"tampered archive")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    _write_executable(
        binaries / "stat",
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "print(oct(os.stat(sys.argv[-1]).st_mode & 0o777)[2:])\n",
    )
    _write_executable(
        binaries / "sha256sum",
        "#!/usr/bin/env python3\n"
        "import hashlib, pathlib, sys\n"
        "path = pathlib.Path(sys.argv[-1])\n"
        "print(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}')\n",
    )
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
    command = [
        bash,
        str(CSV_SYNC_HOST),
        revision,
        runtime_contract,
        "f" * 64,
        candidate_id,
        "0" * 64,
    ]
    env = {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "JOBSEEK_DEPLOY_DIR": str(tmp_path),
        "JOBSEEK_DEPLOY_ENV": str(live_env),
        "JOBSEEK_ACTIVE_RELEASE_POINTER": str(active),
        "JOBSEEK_ACTIVE_RELEASE_ROOT": str(release_root),
        "JOBSEEK_PUBLICATION_JOURNAL": str(tmp_path / "journal"),
        "JOBSEEK_CANDIDATE_ROOT": str(candidates),
    }
    mismatch = subprocess.run(command, check=False, capture_output=True, text=True, env=env)
    assert mismatch.returncode != 0
    assert "CSV candidate archive digest mismatch" in mismatch.stderr
    assert active.resolve() == release.resolve()

    shutil.rmtree(candidate)
    candidate_id, valid_contract, valid_archive = _create_csv_candidate(
        candidates, revision, 1, 1, {"boards.csv": b"slug\ncommitted\n"}
    )
    drift_command = [
        bash,
        str(CSV_SYNC_HOST),
        revision,
        runtime_contract,
        valid_contract,
        candidate_id,
        valid_archive,
    ]
    live_env.write_text(environment.read_text() + "UNCOMMITTED=value\n", encoding="utf-8")
    drift = subprocess.run(
        [*drift_command, "--check-runtime"],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert drift.returncode != 0
    assert "live crawler environment drifted from committed release" in drift.stderr


def test_data_publication_journals_sync_before_atomic_promotion() -> None:
    sync_host = CSV_SYNC_HOST.read_text()
    publication = sync_host[sync_host.rindex("previous_release=") :]
    prepared = publication.index(
        'write_journal 0 "$previous_release" "$candidate_generation" restore-previous'
    )
    sync = publication.index('sync_release_data "$candidate_generation" csv-sync')
    succeeded = publication.index(
        'write_journal 1 "$previous_release" "$candidate_generation" restore-previous'
    )
    promote = publication.index('activate_release_generation "$candidate_generation"')
    assert prepared < sync < succeeded < promote
    assert "recover_publication" in sync_host.partition("cleanup() {")[2].partition("}")[0]
    recovery = sync_host[
        sync_host.index("recover_publication() (") : sync_host.index(
            "\nprepare_candidate_generation() {"
        )
    ]
    assert 'sync_release_data "$previous" recovery-sync' in recovery
    assert 'sync_release_data "$target" recovery-sync' in recovery


def test_full_deploy_bootstraps_exact_prior_data_before_arming_rollback() -> None:
    deploy = DEPLOY_SH.read_text()
    workflow = DEPLOY_WORKFLOW.read_text()
    bootstrap = deploy.index("--bootstrap-current")
    verify = deploy.index("\nverify_active_deploy_snapshot\n", bootstrap)
    snapshot = deploy.index("\nsnapshot_active_deploy_specs\n", verify)
    arm = deploy.index("\narm_deploy_rollback\n", snapshot)
    assert bootstrap < verify < snapshot < arm
    assert "Build exact pre-deploy CSV rollback candidate" in workflow
    assert "${{ github.event.before }}" in workflow
    assert "JOBSEEK_PREVIOUS_RUNTIME_CONTRACT_SHA256" in workflow
    assert "JOBSEEK_PREVIOUS_DATA_CONTRACT_SHA256" in workflow
    assert "JOBSEEK_PREVIOUS_DATA_ARCHIVE_SHA256" in workflow
    assert "--runtime-attestation-out" in workflow
    assert "data data-files.sha256 runtime-attestation.env" in workflow
    assert "scripts/verify-crawler-release-bridge.py" in workflow
    assert "scripts/verify-crawler-release-bridge.py" in deploy
    assert "LEGACY_BRIDGE_FORMAT_VERSION=1" in CSV_SYNC_HOST.read_text()
    assert "promote-target" in CSV_SYNC_HOST.read_text()


def test_deploy_brackets_service_pause_with_validated_maintenance_provenance() -> None:
    script = DEPLOY_SH.read_text()
    workflow = DEPLOY_WORKFLOW.read_text()

    marker_start = script.index("\nstart_maintenance_window\n")
    quiesce = script.index("docker compose stop --timeout 60")
    ready = script.index("\nwait_for_core_services\n")
    marker_stop = script.index("\nstop_maintenance_window\n", ready)

    assert marker_start < quiesce < ready < marker_stop
    assert "JOBSEEK_DEPLOY_REVISION" in workflow
    assert "JOBSEEK_DEPLOY_REVISION: ${{ github.sha }}" in workflow
    for label in (
        "com.docker.compose.project=${COMPOSE_PROJECT_NAME}",
        "com.docker.compose.oneoff=True",
        "jobseek.maintenance.operation=${MAINTENANCE_OPERATION}",
        "jobseek.maintenance.issue=${MAINTENANCE_ISSUE}",
        "jobseek.maintenance.revision=${JOBSEEK_DEPLOY_REVISION}",
        "jobseek.maintenance.budget-seconds=${MAINTENANCE_BUDGET_SECONDS}",
    ):
        assert label in script
    for service in (
        "maintenance-window",
        "deploy-alloy-state",
        "deploy-migrate",
        "deploy-setup-typesense",
        "deploy-sync",
        "deploy-nw-provider-cutover",
    ):
        assert f"com.docker.compose.service={service}" in script


def test_deploy_blocks_compose_oneoffs_before_touching_services() -> None:
    script = DEPLOY_SH.read_text()

    oneoff_guard = script.index("\nensure_no_running_compose_oneoffs\n")
    typesense_guard = script.index("\nensure_no_running_typesense_maintenance\n")
    reconciliation_guard = script.index("\nensure_reconciliation_wrapper_compatible\n")
    postgres_guard = script.index(
        'python3 "$INCOMING_DIR/scripts/postgresql-operational-preflight.py"'
    )
    activation = script.index("\nactivate_staged_deploy_specs\n")
    legacy_stop = script.index('docker stop --time=60 "${legacy_containers[@]}"')
    env_write = script.index('cat > "$ENV_FILE"')
    pull = script.index("\npull_deploy_images\n")
    quiesce = script.index("docker compose stop --timeout 60")

    assert (
        oneoff_guard
        < typesense_guard
        < reconciliation_guard
        < postgres_guard
        < activation
        < legacy_stop
        < env_write
        < pull
        < quiesce
    )


def test_deploy_rolls_back_env_and_compose_as_one_contract() -> None:
    script = DEPLOY_SH.read_text()
    workflow = DEPLOY_WORKFLOW.read_text()
    rollback = script[script.index("rollback_deploy() {") : script.index("arm_deploy_rollback() {")]

    assert "docker-compose.yml" in script.partition("DEPLOY_SPEC_FILES=(")[2].partition(")")[0]
    assert 'tar -C "$snapshot_dir" -cpf' in script
    assert 'tar -C "$DEPLOY_DIR" -xpf "$ROLLBACK_SPEC_ARCHIVE"' in script
    assert 'install -m 0644 "$ACTIVE_COMPOSE_SNAPSHOT"' in script
    assert "ACTIVE_RELEASE_POINTER" in script
    assert "ACTIVE_RELEASE_MANIFEST" in script
    assert 'install -m 0600 "$ACTIVE_ENV_SNAPSHOT" "$ROLLBACK_ENV_FILE"' in script
    quiesce = rollback.index(
        "stop --timeout 60 worker-1 worker-2 worker-3 browser-1 exporter drain"
    )
    assert 'activate_release_generation "$ROLLBACK_ACTIVE_RELEASE_TARGET"' in script
    release_restore = rollback.index(
        'activate_release_generation "$ROLLBACK_ACTIVE_RELEASE_TARGET"'
    )
    release_rehydrate = rollback.index("verify_active_deploy_snapshot", release_restore)
    env_restore = rollback.index('mv "$ROLLBACK_ENV_FILE" "$ENV_FILE"')
    spec_restore = rollback.index("restore_previous_deploy_specs")
    contract = rollback.index("configure_rollback_compose_contract")
    config_sync = rollback.index("rollback_sync_previous_config")
    old_stack_start = rollback.index("rollback_compose up -d --remove-orphans")
    health = rollback.index("wait_for_rollback_core_services")
    assert (
        quiesce
        < release_restore
        < release_rehydrate
        < env_restore
        < spec_restore
        < contract
        < config_sync
        < old_stack_start
        < health
    )
    assert "|| true" not in rollback
    assert "rollback failed with status" in rollback
    assert "if ((env_restore_complete && spec_restore_complete)); then" in rollback
    assert (
        "if ((quiesce_complete && release_restore_complete && env_restore_complete && "
        "spec_restore_complete && bounded_contract_persisted)); then" in rollback
    )
    assert (
        "spec_restore_complete && bounded_contract_persisted && "
        "config_restore_complete)); then" in rollback
    )
    rollback_sync = script[
        script.index("rollback_sync_previous_config() {") : script.index(
            "rollback_compose_service_ready() {"
        )
    ]
    assert 'read_exact_release_value "$ENV_FILE" CRAWLER_IMAGE_REF' in rollback_sync
    assert "uv run --no-sync crawler sync" in rollback_sync
    assert "-e CRAWLER_DB_ROLE=rollback-sync" in rollback_sync
    assert script.index("FORWARD_SYNC_STARTED=1") < script.index(
        "uv run --no-sync crawler sync", script.index("FORWARD_SYNC_STARTED=1")
    )
    assert "local services=(redis worker-1 worker-2 worker-3 browser-1 exporter drain alloy)" in (
        script
    )
    assert 'compose_args=(-f "$DEPLOY_DIR/docker-compose.yml")' in script
    assert 'compose_args+=(-f "$ROLLBACK_ACTIVE_IMAGE_OVERRIDE")' in script
    assert 'compose_args+=(-f "$ROLLBACK_POOL_OVERRIDE")' in script
    assert ROLLBACK_POOL_OVERRIDE.exists()
    assert "apps/crawler/rollback-pool-budget.override.yml" in workflow
    assert "target: /home/deploy/incoming/" in workflow
    assert "script: bash /home/deploy/incoming/deploy.sh" in workflow
    assert "target: /home/deploy/\n" not in workflow


def test_previous_config_restore_uses_the_restored_image_and_scopes_web_secret(
    tmp_path: Path,
) -> None:
    script = DEPLOY_SH.read_text()
    read_exact = script[
        script.index("read_exact_release_value() {") : script.index(
            "verify_runtime_contract_pair() {"
        )
    ]
    rollback_sync = script[
        script.index("rollback_sync_previous_config() {") : script.index(
            "rollback_compose_service_ready() {"
        )
    ]
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CRAWLER_IMAGE_REF="
        f"ghcr.io/colophon-group/jobseek-crawler@sha256:{'a' * 64}\n"
        "WEB_DATABASE_URL=postgresql://rollback-only\n",
        encoding="utf-8",
    )
    log = tmp_path / "calls.log"
    data_snapshot = tmp_path / "committed-b"
    data_snapshot.mkdir()
    (data_snapshot / "boards.csv").write_text("slug\nB\n", encoding="utf-8")
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
    harness = "\n".join(
        (
            "set -u",
            'OWNER="colophon-group"',
            'ENV_FILE="$TEST_ENV_FILE"',
            'ROLLBACK_SYNC_WEB_DATABASE_URL=""',
            'ACTIVE_RELEASE_FORMAT="3"',
            'ACTIVE_DATA_SNAPSHOT="$TEST_DATA_SNAPSHOT"',
            'ACTIVE_DATA_FILES_MANIFEST="$TEST_DATA_MANIFEST"',
            "verify_exact_csv_tree() { return 0; }",
            read_exact,
            rollback_sync,
            "rollback_compose() {",
            '  test "$ROLLBACK_SYNC_WEB_DATABASE_URL" = "postgresql://rollback-only"',
            '  printf \'%s\\n\' "$*" >>"$TEST_LOG"',
            '  return "$COMPOSE_STATUS"',
            "}",
            "rollback_sync_previous_config",
            "status=$?",
            'test -z "$ROLLBACK_SYNC_WEB_DATABASE_URL"',
            'printf \'status=%s\\n\' "$status" >>"$TEST_LOG"',
            "exit 0",
        )
    )

    for compose_status in (0, 9):
        log.write_text("", encoding="utf-8")
        result = subprocess.run(
            [bash, "-c", harness],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "COMPOSE_STATUS": str(compose_status),
                "TEST_ENV_FILE": str(env_file),
                "TEST_LOG": str(log),
                "TEST_DATA_SNAPSHOT": str(data_snapshot),
                "TEST_DATA_MANIFEST": str(tmp_path / "data-files.sha256"),
            },
        )
        assert result.returncode == 0, result.stderr
        calls = log.read_text(encoding="utf-8").splitlines()
        assert calls[0] == (
            f"run --rm --no-deps -v {data_snapshot}:/app/data:ro -e WEB_DATABASE_URL "
            "-e CRAWLER_DB_ROLE=rollback-sync -e CRAWLER_DB_POOL_MIN=0 "
            "-e CRAWLER_DB_POOL_MAX=4 worker-1 uv run --no-sync crawler sync"
        )
        assert calls[1] == f"status={compose_status}"
        assert "postgresql://rollback-only" not in result.stdout
        assert "postgresql://rollback-only" not in result.stderr


def test_deploy_publishes_exact_success_marker_only_after_commit() -> None:
    script = DEPLOY_SH.read_text()
    workflow = DEPLOY_WORKFLOW.read_text()
    murmur_workflow = MURMUR_DEPLOY_WORKFLOW.read_text()
    prepare = script.index('"CRAWLER_IMAGE_TAG=$IMAGE_TAG"')
    health = script.index("\nwait_for_core_services\n")
    disarm = script.index("\ndisarm_deploy_rollback\n")
    publish = script.index('publish_active_deploy_release \\\n  "$deploy_success_temporary"')
    staged_identity = script.index('verify_shim_deploy_contract "$deploy_success_temporary"')
    committed_identity = script.index('verify_shim_deploy_contract "$DEPLOY_SUCCESS_FILE"')

    assert health < prepare < staged_identity < publish < committed_identity < disarm
    assert '"CRAWLER_IMAGE_REF=$CRAWLER_IMAGE_REF"' in script
    assert '"BROWSER_IMAGE_REF=$BROWSER_IMAGE_REF"' in script
    assert '"SHIM_IMAGE_REF=$SHIM_IMAGE_REF"' in script
    assert '"JOBSEEK_RUNTIME_CONTRACT_SHA256=$JOBSEEK_RUNTIME_CONTRACT_SHA256"' in script
    assert "verify_runtime_contract_pair" in script
    assert (
        "JOBSEEK_RUNTIME_CONTRACT_SHA256: ${{ needs.build.outputs.runtime_contract_sha256 }}"
        in (workflow)
    )
    assert "JOBSEEK_RUNTIME_CONTRACT_SHA256=//p" in murmur_workflow
    assert "${runtime_contracts[0]}" in murmur_workflow
    assert 'ACTIVE_RELEASE_POINTER="$DEPLOY_DIR/.crawler-active-release"' in script
    assert "RELEASE_FORMAT_VERSION=3" in script
    assert '"DATA_FILES_SHA256=$data_files_digest"' in script
    assert '"DATA_CONTRACT_SHA256=$data_files_digest"' in script
    assert '"$data_files_digest" == "$JOBSEEK_DATA_CONTRACT_SHA256"' in script
    assert '"DATA_REVISION=$JOBSEEK_DEPLOY_REVISION"' in script
    assert '-v "$FORWARD_DATA_SNAPSHOT:/app/data:ro"' in script
    assert (
        "JOBSEEK_DATA_CONTRACT_SHA256: ${{ needs.build.outputs.data_contract_sha256 }}" in workflow
    )
    assert 'cp -a "$previous_active_generation/data" "$murmur_generation/data"' in (murmur_workflow)
    assert "DATA_CONTRACT_SHA256=$data_contract" in murmur_workflow
    assert '[[ -d "$ACTIVE_RELEASE_ROOT" && ! -L "$ACTIVE_RELEASE_ROOT" ]]' in script
    assert "os.replace(temporary, active)" in script


def test_release_generation_root_is_durable_before_pointer_publication() -> None:
    crawler = DEPLOY_SH.read_text()
    publish = crawler[
        crawler.index("publish_active_deploy_release() {") : crawler.index(
            "snapshot_active_deploy_specs() {"
        )
    ]
    generation_sync = publish.index("os.fsync(directory_fd)")
    root_sync = publish.index("os.fsync(release_root_fd)")
    pointer_publish = publish.index('activate_release_generation "$generation"')
    assert generation_sync < root_sync < pointer_publish

    murmur = MURMUR_DEPLOY_WORKFLOW.read_text()
    durable_publish = murmur[
        murmur.index("fsync_release_generation() {") : murmur.index("verify_release_generation() {")
    ]
    generation_sync = durable_publish.index("os.fsync(generation_fd)")
    root_sync = durable_publish.index("os.fsync(release_root_fd)")
    pointer_publish = durable_publish.index("os.symlink(generation, candidate)")
    assert generation_sync < root_sync < pointer_publish


def test_deploy_signal_and_error_restore_previous_contract_once(tmp_path: Path) -> None:
    script = DEPLOY_SH.read_text()
    restore = script[
        script.index("restore_previous_deploy_specs() {") : script.index(
            "reconciliation_wrapper_is_compatible() {"
        )
    ]
    rollback_support = script[
        script.index("configure_rollback_compose_contract() {") : script.index(
            "rollback_deploy() {"
        )
    ]
    rollback = script[script.index("rollback_deploy() {") : script.index("arm_deploy_rollback() {")]
    arm = script[
        script.index("arm_deploy_rollback() {") : script.index("disarm_deploy_rollback() {")
    ]
    harness = "\n".join(
        (
            "set -Eeuo pipefail",
            'DEPLOY_DIR="$TEST_DEPLOY_DIR"',
            'ENV_FILE="$DEPLOY_DIR/.env"',
            'ROLLBACK_ENV_FILE="$DEPLOY_DIR/.env.rollback"',
            'ROLLBACK_SPEC_ARCHIVE="$DEPLOY_DIR/.deploy-spec.rollback.tar"',
            'ROLLBACK_POOL_OVERRIDE="$DEPLOY_DIR/.crawler-rollback-pool-budget.override.yml"',
            'ACTIVE_RELEASE_POINTER="$DEPLOY_DIR/.crawler-active-release"',
            'ROLLBACK_ACTIVE_RELEASE_TARGET="$DEPLOY_DIR/old-release"',
            'ROLLBACK_ACTIVE_IMAGE_OVERRIDE=""',
            "ENV_FILE_WAS_PRESENT=1",
            "ROLLBACK_ARMED=0",
            "ROLLBACK_RUNNING=0",
            'COMPOSE_PROJECT_NAME="deploy"',
            'MAINTENANCE_MARKER_NAME="test-marker"',
            "stop_maintenance_window() {",
            "  printf 'maintenance-stop\\n' >>\"$TEST_LOG\"",
            "}",
            "wait_for_core_services() {",
            "  printf 'rollback-ready\\n' >>\"$TEST_LOG\"",
            "}",
            "activate_release_generation() {",
            "  printf 'release-restore\\n' >>\"$TEST_LOG\"",
            "}",
            "verify_active_deploy_snapshot() {",
            "  printf 'release-verify\\n' >>\"$TEST_LOG\"",
            "}",
            "publish_legacy_success_marker() {",
            "  printf 'legacy-publish\\n' >>\"$TEST_LOG\"",
            "}",
            restore,
            rollback_support,
            rollback,
            arm,
            "wait_for_rollback_core_services() {",
            "  printf 'health-gate\\n' >>\"$TEST_LOG\"",
            "}",
            "arm_deploy_rollback",
            "printf 'CRAWLER_IMAGE_TAG=failed-tag\\n' >\"$ENV_FILE\"",
            "printf 'new-compose\\n' >\"$DEPLOY_DIR/docker-compose.yml\"",
            'if [[ "$1" == signal ]]; then',
            '  kill -HUP "$$"',
            "else",
            "  false",
            "fi",
            "exit 99",
        )
    )

    for mode, expected_status in (
        ("signal", 129),
        ("error", 1),
        ("restart-failure", 1),
    ):
        deploy_dir = tmp_path / mode
        deploy_dir.mkdir()
        env_file = deploy_dir / ".env"
        rollback_env = deploy_dir / ".env.rollback"
        compose = deploy_dir / "docker-compose.yml"
        rollback_override = deploy_dir / ".crawler-rollback-pool-budget.override.yml"
        archive = deploy_dir / ".deploy-spec.rollback.tar"
        log = deploy_dir / "rollback.log"
        env_file.write_text("CRAWLER_IMAGE_TAG=failed-tag\n", encoding="utf-8")
        rollback_env.write_text("CRAWLER_IMAGE_TAG=old-tag\n", encoding="utf-8")
        compose.write_text("new-compose\n", encoding="utf-8")
        rollback_override.write_text("bounded-override\n", encoding="utf-8")
        previous_compose = deploy_dir / "previous-compose.yml"
        previous_compose.write_text("old-compose\n", encoding="utf-8")
        with tarfile.open(archive, "w") as rollback_archive:
            rollback_archive.add(previous_compose, arcname="docker-compose.yml")
        binary_dir = deploy_dir / "bin"
        binary_dir.mkdir()
        _write_executable(
            binary_dir / "docker",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'action=""\n'
            'for arg in "$@"; do\n'
            '  if [[ "$arg" == stop || "$arg" == up ]]; then action="$arg"; fi\n'
            "done\n"
            'env_file="$3"\n'
            'deploy_dir="$(dirname "$env_file")"\n'
            'log="$deploy_dir/rollback.log"\n'
            'if [[ "$action" == stop ]]; then\n'
            "  printf 'compose-stop\\n' >>\"$log\"\n"
            "  exit 0\n"
            "fi\n"
            '[[ "$action" == up ]]\n'
            "printf 'compose-up\\n' >>\"$log\"\n"
            'printf \'process-tag=%s\\n\' "${CRAWLER_IMAGE_TAG:-unset}" >>"$log"\n'
            'tag="$(sed -n \'s/^CRAWLER_IMAGE_TAG=//p\' "$env_file")"\n'
            'printf \'env-tag=%s\\n\' "$tag" >>"$log"\n'
            'printf \'compose=%s\\n\' "$(cat "$deploy_dir/docker-compose.yml")" >>"$log"\n'
            '[[ ! -e "$deploy_dir/fail-restart" ]]\n',
        )
        if mode == "restart-failure":
            (deploy_dir / "fail-restart").touch()

        result = subprocess.run(
            ["bash", "-c", harness, "deploy-rollback-test", mode],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "CRAWLER_IMAGE_TAG": "failed-tag",
                "PATH": f"{binary_dir}:{os.environ['PATH']}",
                "TEST_DEPLOY_DIR": str(deploy_dir),
                "TEST_LOG": str(log),
            },
        )

        assert result.returncode == expected_status, result.stderr
        assert env_file.read_text(encoding="utf-8") == (
            f"CRAWLER_IMAGE_TAG=old-tag\n\nCOMPOSE_FILE={compose}:{rollback_override}\n"
        )
        assert compose.read_text(encoding="utf-8") == "old-compose\n"
        events = log.read_text(encoding="utf-8").splitlines()
        expected_events = [
            "compose-stop",
            "release-restore",
            "release-verify",
            "compose-up",
            "process-tag=unset",
            "env-tag=old-tag",
            "compose=old-compose",
        ]
        if mode != "restart-failure":
            expected_events.extend(("health-gate", "release-verify", "legacy-publish"))
        expected_events.append("maintenance-stop")
        assert events == expected_events
        if mode == "restart-failure":
            assert "retaining the last verified crawler snapshot" in result.stderr


def test_rollback_propagates_quiesce_start_and_health_failures(tmp_path: Path) -> None:
    script = DEPLOY_SH.read_text()
    configure = script[
        script.index("configure_rollback_compose_contract() {") : script.index(
            "rollback_compose() {"
        )
    ]
    rollback = script[script.index("rollback_deploy() {") : script.index("arm_deploy_rollback() {")]
    harness = "\n".join(
        (
            "set -u",
            'DEPLOY_DIR="$TEST_DEPLOY_DIR"',
            'ENV_FILE="$DEPLOY_DIR/.env"',
            'ROLLBACK_ENV_FILE="$DEPLOY_DIR/.env.rollback"',
            'ROLLBACK_POOL_OVERRIDE="$DEPLOY_DIR/.crawler-rollback-pool-budget.override.yml"',
            'ROLLBACK_ACTIVE_RELEASE_TARGET="$DEPLOY_DIR/old-release"',
            'ROLLBACK_ACTIVE_IMAGE_OVERRIDE=""',
            "ENV_FILE_WAS_PRESENT=1",
            "ROLLBACK_ARMED=1",
            "ROLLBACK_RUNNING=0",
            'COMPOSE_PROJECT_NAME="deploy"',
            "docker() {",
            "  printf 'quiesce\\n' >>\"$TEST_LOG\"",
            '  return "$STOP_STATUS"',
            "}",
            "restore_previous_deploy_specs() {",
            "  printf 'restore-specs\\n' >>\"$TEST_LOG\"",
            "}",
            "activate_release_generation() {",
            "  printf 'release-restore\\n' >>\"$TEST_LOG\"",
            "}",
            "verify_active_deploy_snapshot() {",
            "  printf 'release-verify\\n' >>\"$TEST_LOG\"",
            "}",
            "publish_legacy_success_marker() {",
            "  printf 'legacy-publish\\n' >>\"$TEST_LOG\"",
            "}",
            configure,
            "rollback_compose() {",
            "  printf 'compose-start\\n' >>\"$TEST_LOG\"",
            '  return "$START_STATUS"',
            "}",
            "wait_for_rollback_core_services() {",
            "  printf 'health-gate\\n' >>\"$TEST_LOG\"",
            '  return "$HEALTH_STATUS"',
            "}",
            "stop_maintenance_window() {",
            "  printf 'maintenance-stop\\n' >>\"$TEST_LOG\"",
            "}",
            rollback,
            "rollback_deploy 23",
        )
    )

    cases = (
        (
            5,
            0,
            0,
            [
                "quiesce",
                "release-restore",
                "release-verify",
                "restore-specs",
                "maintenance-stop",
            ],
        ),
        (
            0,
            6,
            0,
            [
                "quiesce",
                "release-restore",
                "release-verify",
                "restore-specs",
                "compose-start",
                "maintenance-stop",
            ],
        ),
        (
            0,
            0,
            7,
            [
                "quiesce",
                "release-restore",
                "release-verify",
                "restore-specs",
                "compose-start",
                "health-gate",
                "maintenance-stop",
            ],
        ),
    )
    for index, (stop_status, start_status, health_status, expected_events) in enumerate(cases):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        (case_dir / ".env").write_text("failed\n", encoding="utf-8")
        (case_dir / ".env.rollback").write_text("restored\n", encoding="utf-8")
        compose = case_dir / "docker-compose.yml"
        compose.write_text("old-compose\n", encoding="utf-8")
        rollback_override = case_dir / ".crawler-rollback-pool-budget.override.yml"
        rollback_override.write_text("bounded-override\n", encoding="utf-8")
        log = case_dir / "events.log"
        result = subprocess.run(
            ["bash", "-c", harness],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "TEST_DEPLOY_DIR": str(case_dir),
                "TEST_LOG": str(log),
                "STOP_STATUS": str(stop_status),
                "START_STATUS": str(start_status),
                "HEALTH_STATUS": str(health_status),
            },
        )

        expected_status = stop_status or start_status or health_status
        assert result.returncode == expected_status, result.stderr
        assert f"rollback failed with status {expected_status}" in result.stderr
        assert ("bounded old-stack contract persisted but restart skipped" in result.stderr) == (
            stop_status != 0
        )
        assert (case_dir / ".env").read_text(encoding="utf-8") == (
            f"restored\n\nCOMPOSE_FILE={compose}:{rollback_override}\n"
        )
        assert log.read_text(encoding="utf-8").splitlines() == expected_events


def test_rollback_restores_previous_csv_config_before_restarting_workers(
    tmp_path: Path,
) -> None:
    script = DEPLOY_SH.read_text()
    configure = script[
        script.index("configure_rollback_compose_contract() {") : script.index(
            "rollback_compose() {"
        )
    ]
    rollback = script[script.index("rollback_deploy() {") : script.index("arm_deploy_rollback() {")]
    harness = "\n".join(
        (
            "set -u",
            'DEPLOY_DIR="$TEST_DEPLOY_DIR"',
            'ENV_FILE="$DEPLOY_DIR/.env"',
            'ROLLBACK_ENV_FILE="$DEPLOY_DIR/.env.rollback"',
            'ROLLBACK_SPEC_ARCHIVE="$DEPLOY_DIR/.deploy-spec.rollback.tar"',
            'ROLLBACK_POOL_OVERRIDE="$DEPLOY_DIR/.crawler-rollback-pool-budget.override.yml"',
            'ROLLBACK_ACTIVE_RELEASE_TARGET="$DEPLOY_DIR/old-release"',
            'ROLLBACK_ACTIVE_IMAGE_OVERRIDE=""',
            "ENV_FILE_WAS_PRESENT=1",
            "ROLLBACK_ARMED=1",
            "ROLLBACK_RUNNING=0",
            "FORWARD_SYNC_STARTED=1",
            'COMPOSE_PROJECT_NAME="deploy"',
            "docker() {",
            "  printf 'quiesce\\n' >>\"$TEST_LOG\"",
            "}",
            "restore_previous_deploy_specs() {",
            "  printf 'restore-specs\\n' >>\"$TEST_LOG\"",
            "}",
            "activate_release_generation() {",
            "  printf 'release-restore\\n' >>\"$TEST_LOG\"",
            "}",
            "verify_active_deploy_snapshot() {",
            "  printf 'release-verify\\n' >>\"$TEST_LOG\"",
            "}",
            "publish_legacy_success_marker() {",
            "  printf 'legacy-publish\\n' >>\"$TEST_LOG\"",
            "}",
            configure,
            "rollback_compose() {",
            "  printf 'compose-start\\n' >>\"$TEST_LOG\"",
            "}",
            "wait_for_rollback_core_services() {",
            "  printf 'health-gate\\n' >>\"$TEST_LOG\"",
            "}",
            "stop_maintenance_window() {",
            "  printf 'maintenance-stop\\n' >>\"$TEST_LOG\"",
            "}",
            rollback,
            "rollback_sync_previous_config() {",
            "  printf 'config-sync\\n' >>\"$TEST_LOG\"",
            '  return "$SYNC_STATUS"',
            "}",
            "rollback_deploy 23",
        )
    )

    for sync_status in (0, 8):
        case_dir = tmp_path / str(sync_status)
        case_dir.mkdir()
        (case_dir / ".env").write_text("failed\n", encoding="utf-8")
        (case_dir / ".env.rollback").write_text("restored\n", encoding="utf-8")
        compose = case_dir / "docker-compose.yml"
        compose.write_text("old-compose\n", encoding="utf-8")
        rollback_override = case_dir / ".crawler-rollback-pool-budget.override.yml"
        rollback_override.write_text("bounded-override\n", encoding="utf-8")
        (case_dir / ".deploy-spec.rollback.tar").touch()
        log = case_dir / "events.log"

        result = subprocess.run(
            ["bash", "-c", harness],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "TEST_DEPLOY_DIR": str(case_dir),
                "TEST_LOG": str(log),
                "SYNC_STATUS": str(sync_status),
            },
        )

        expected_status = 23 if sync_status == 0 else sync_status
        assert result.returncode == expected_status, result.stderr
        events = log.read_text(encoding="utf-8").splitlines()
        assert events[:5] == [
            "quiesce",
            "release-restore",
            "release-verify",
            "restore-specs",
            "config-sync",
        ]
        if sync_status == 0:
            assert events[5:] == [
                "compose-start",
                "health-gate",
                "release-verify",
                "legacy-publish",
                "maintenance-stop",
            ]
        else:
            assert events[5:] == ["maintenance-stop"]
            assert "old stack restart skipped" in result.stderr


def test_post_pointer_failure_rehydrates_old_release_before_config_rollback(
    tmp_path: Path,
) -> None:
    script = DEPLOY_SH.read_text()
    verification_helpers = script[
        script.index("verify_active_snapshot_file() {") : script.index(
            "\nwrite_exact_csv_manifest() {"
        )
    ]
    release_helpers = script[
        script.index("read_exact_release_value() {") : script.index(
            "\npublish_legacy_success_marker() {"
        )
    ]
    restore = script[
        script.index("restore_previous_deploy_specs() {") : script.index(
            "\nreconciliation_wrapper_is_compatible() {"
        )
    ]
    rollback_support = script[
        script.index("configure_rollback_compose_contract() {") : script.index(
            "rollback_deploy() {"
        )
    ]
    rollback = script[script.index("rollback_deploy() {") : script.index("arm_deploy_rollback() {")]

    deploy_dir = tmp_path / "deploy"
    release_root = deploy_dir / "releases"
    release_root.mkdir(parents=True)
    binary_dir = deploy_dir / "bin"
    binary_dir.mkdir()
    _install_release_verifier_tools(binary_dir)
    _install_csv_host_docker(binary_dir)
    legacy_release, legacy_identity = _create_legacy_format2_release(
        release_root, "legacy.production", "b"
    )
    active_pointer = deploy_dir / ".crawler-active-release"
    active_pointer.symlink_to(legacy_release)
    live_env = deploy_dir / ".env"
    shutil.copyfile(legacy_release / "environment.env", live_env)
    live_env.chmod(0o600)
    candidates = deploy_dir / "candidates"
    candidates.mkdir()
    previous_data_revision = "c" * 40
    candidate_id, old_data_contract, archive_sha = _create_csv_candidate(
        candidates,
        previous_data_revision,
        73,
        1,
        {"boards.csv": b"slug\nB\n"},
        legacy_identity["runtime_contract"],
        [previous_data_revision, legacy_identity["source_revision"]],
    )
    bootstrap_env = _csv_host_test_environment(
        deploy_dir, release_root, active_pointer, live_env, candidates
    )
    bootstrap_env["PATH"] = f"{binary_dir}:{os.environ['PATH']}"
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
    bootstrap = subprocess.run(
        [
            bash,
            str(CSV_SYNC_HOST),
            "--bootstrap-current",
            previous_data_revision,
            legacy_identity["runtime_contract"],
            old_data_contract,
            candidate_id,
            archive_sha,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=bootstrap_env,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr
    old_release = active_pointer.resolve()
    assert old_release != legacy_release.resolve()
    old_release = _create_murmur_carried_bridge(old_release, release_root, "murmur-carried", "e")
    active_pointer.unlink()
    active_pointer.symlink_to(old_release)
    old_identity = {
        **legacy_identity,
        "data_contract": old_data_contract,
        "revision": previous_data_revision,
    }
    old_manifest = (old_release / "release.manifest").read_text(encoding="utf-8")
    assert "LEGACY_BRIDGE_FORMAT_VERSION=1\n" in old_manifest
    assert "LEGACY_BRIDGE_TRANSITIVE=1\n" in old_manifest

    # Model the new full release committing its pointer before a later deploy
    # gate fails. Rollback must select and rehydrate the bridged old release.
    new_release, new_identity = _create_full_deploy_v3_release(release_root, "release-new", "d")
    active_pointer.unlink()
    active_pointer.symlink_to(new_release)
    shutil.copyfile(new_release / "environment.env", live_env)
    live_env.chmod(0o600)
    rollback_env = deploy_dir / ".env.rollback"
    shutil.copyfile(old_release / "environment.env", rollback_env)
    rollback_env.chmod(0o600)
    live_compose = deploy_dir / "docker-compose.yml"
    shutil.copyfile(new_release / "docker-compose.yml", live_compose)
    rollback_archive = deploy_dir / ".deploy-spec.rollback.tar"
    with tarfile.open(rollback_archive, "w") as archive:
        archive.add(old_release / "docker-compose.yml", arcname="docker-compose.yml")
    pool_override = deploy_dir / ".crawler-rollback-pool-budget.override.yml"
    pool_override.write_text("services: {}\n", encoding="utf-8")
    log = deploy_dir / "rollback.log"

    _install_release_verifier_tools(binary_dir)
    _write_executable(
        binary_dir / "docker",
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import sys\n"
        f"log = Path({str(log)!r})\n"
        f"old_data = Path({str(old_release / 'data')!r})\n"
        f"new_data = Path({str(new_release / 'data')!r})\n"
        "args = sys.argv[1:]\n"
        "def env_value(key):\n"
        "    env_file = Path(args[args.index('--env-file') + 1])\n"
        "    values = [line.split('=', 1)[1] for line in env_file.read_text().splitlines() "
        "if line.startswith(f'{key}=')]\n"
        "    assert len(values) == 1, (key, values, args)\n"
        "    return values[0]\n"
        "revision = env_value('JOBSEEK_DEPLOY_REVISION')\n"
        "crawler_ref = env_value('CRAWLER_IMAGE_REF')\n"
        "runtime = env_value('JOBSEEK_RUNTIME_CONTRACT_SHA256')\n"
        "marker = revision[0]\n"
        "assert crawler_ref.endswith(marker * 64), (crawler_ref, revision)\n"
        "assert runtime == marker * 64, (runtime, revision)\n"
        "if 'config' in args and '--images' in args:\n"
        "    with log.open('a') as output: output.write(f'verify:{marker}\\n')\n"
        "    print(crawler_ref)\n"
        "elif 'stop' in args:\n"
        "    with log.open('a') as output: output.write('stop\\n')\n"
        "elif 'run' in args:\n"
        "    volume = args[args.index('-v') + 1].split(':', 1)[0]\n"
        "    assert Path(volume) == old_data, (volume, old_data, new_data)\n"
        "    assert Path(volume) != new_data\n"
        "    board = (Path(volume) / 'boards.csv').read_text().splitlines()[-1]\n"
        "    assert board == 'B', board\n"
        "    with log.open('a') as output: output.write(f'sync:{marker}:{board}\\n')\n"
        "elif 'up' in args:\n"
        "    with log.open('a') as output: output.write(f'up:{marker}\\n')\n"
        "else:\n"
        "    raise AssertionError(args)\n",
    )

    harness = "\n".join(
        (
            "set -Eeuo pipefail",
            'OWNER="colophon-group"',
            f'BRIDGE_VERIFIER="{BRIDGE_VERIFIER}"',
            f'DEPLOY_DIR="{deploy_dir}"',
            f'ENV_FILE="{live_env}"',
            f'ACTIVE_RELEASE_ROOT="{release_root}"',
            f'ACTIVE_RELEASE_POINTER="{active_pointer}"',
            'ACTIVE_RELEASE_DIR=""',
            'ACTIVE_COMPOSE_SNAPSHOT=""',
            'ACTIVE_COMPOSE_SNAPSHOT_SHA256=""',
            'ACTIVE_ENV_SNAPSHOT=""',
            'ACTIVE_ENV_SNAPSHOT_SHA256=""',
            'ACTIVE_RELEASE_MANIFEST=""',
            'ACTIVE_RELEASE_FORMAT=""',
            'ACTIVE_IMAGE_OVERRIDE=""',
            'ACTIVE_DATA_SNAPSHOT=""',
            'ACTIVE_DATA_FILES_MANIFEST=""',
            'DEPLOY_SUCCESS_FILE=""',
            f'ROLLBACK_ENV_FILE="{rollback_env}"',
            f'ROLLBACK_SPEC_ARCHIVE="{rollback_archive}"',
            f'ROLLBACK_POOL_OVERRIDE="{pool_override}"',
            f'ROLLBACK_ACTIVE_RELEASE_TARGET="{old_release}"',
            'ROLLBACK_ACTIVE_IMAGE_OVERRIDE=""',
            'ROLLBACK_SYNC_WEB_DATABASE_URL=""',
            "ENV_FILE_WAS_PRESENT=1",
            "ROLLBACK_ARMED=1",
            "ROLLBACK_RUNNING=0",
            "FORWARD_SYNC_STARTED=1",
            'COMPOSE_PROJECT_NAME="deploy"',
            verification_helpers,
            release_helpers,
            restore,
            rollback_support,
            rollback,
            "wait_for_rollback_core_services() { :; }",
            "publish_legacy_success_marker() { :; }",
            "stop_maintenance_window() { :; }",
            "verify_active_deploy_snapshot",
            f'test "$ACTIVE_RELEASE_DIR" = "{new_release}"',
            f'test "$ACTIVE_DATA_SNAPSHOT" = "{new_release / "data"}"',
            "rollback_deploy 23",
        )
    )
    result = subprocess.run(
        [bash, "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{binary_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 23, result.stderr
    assert active_pointer.resolve() == old_release.resolve()
    assert old_identity["data_contract"] != new_identity["data_contract"]
    active_manifest = (active_pointer.resolve() / "release.manifest").read_text(encoding="utf-8")
    assert f"DATA_CONTRACT_SHA256={old_identity['data_contract']}\n" in active_manifest
    assert f"DATA_REVISION={old_identity['revision']}\n" in active_manifest
    assert (
        (active_pointer.resolve() / "data" / "boards.csv")
        .read_text(encoding="utf-8")
        .endswith("B\n")
    )
    assert old_identity["crawler_ref"] in live_compose.read_text(encoding="utf-8")
    restored_env = live_env.read_text(encoding="utf-8")
    assert f"CRAWLER_IMAGE_REF={old_identity['crawler_ref']}\n" in restored_env
    assert f"JOBSEEK_RUNTIME_CONTRACT_SHA256={old_identity['runtime_contract']}\n" in (restored_env)
    assert new_identity["crawler_ref"] not in restored_env
    assert log.read_text(encoding="utf-8").splitlines() == [
        "verify:d",
        "stop",
        "verify:b",
        "sync:b:B",
        "up:b",
        "verify:b",
    ]


def test_first_rollout_fails_closed_without_digest_pinned_compose_preseed() -> None:
    murmur = MURMUR_DEPLOY_WORKFLOW.read_text()
    first_rollout = murmur[murmur.index('if [[ ! -e "$active_release" ]]') :]
    assert "$legacy_active_compose" in first_rollout
    assert "rollback-images.override.yml" in first_rollout
    assert "IMAGE_OVERRIDE_SHA256=$image_override_digest" in first_rollout
    assert "config --images" in murmur
    assert '[[ "$configured_image" =~ @sha256:[0-9a-f]{64}$ ]]' in murmur
    image_writer = murmur[
        murmur.index("write_exact_image_override() {") : murmur.index(
            "fsync_release_generation() {"
        )
    ]
    for service in (
        "redis",
        "worker-1",
        "worker-2",
        "worker-3",
        "exporter",
        "drain",
        "browser-1",
        "murmur-shim-runtime-init",
        "murmur-shim",
        "alloy",
    ):
        assert f"  {service}:" in image_writer
    assert "install -m 0644 /home/deploy/.crawler-deploy-success.env" in first_rollout
    assert "RELEASE_FORMAT_VERSION=2" in first_rollout
    assert "BOOTSTRAP_LEGACY=1" in first_rollout
    assert "BOOTSTRAP_LEGACY=0" in first_rollout
    assert "RELEASE_FORMAT_VERSION=0" not in first_rollout
    assert 'activate_release_generation "$legacy_generation"' in first_rollout


def test_murmur_v3_verifier_rejects_unsafe_or_unattested_evidence(
    tmp_path: Path,
) -> None:
    workflow = yaml.safe_load(MURMUR_DEPLOY_WORKFLOW.read_text())
    remote_script = next(
        step["with"]["script"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy via SSH"
    )
    assert "source: scripts/verify-crawler-release-bridge.py" in MURMUR_DEPLOY_WORKFLOW.read_text()
    verifier = remote_script[
        remote_script.index("verify_snapshot() {") : remote_script.index("\nprevious_redis_ref=")
    ]
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _install_release_verifier_tools(binary_dir)
    docker_log = tmp_path / "docker.log"
    _write_executable(
        binary_dir / "docker",
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import sys\n"
        f"log = Path({str(docker_log)!r})\n"
        "args = sys.argv[1:]\n"
        "assert 'config' in args and '--images' in args, args\n"
        "with log.open('a') as output: output.write(' '.join(args) + '\\n')\n"
        "print('ghcr.io/colophon-group/jobseek-crawler@sha256:' + 'a' * 64)\n",
    )
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"

    def verify(release: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                bash,
                "-c",
                "set -euo pipefail\n"
                + f'bridge_verifier="{BRIDGE_VERIFIER}"\nOWNER="colophon-group"\n'
                + verifier
                + '\nverify_release_generation "$1"',
                "verify",
                str(release),
            ],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{binary_dir}:{os.environ['PATH']}"},
        )

    valid_root = tmp_path / "valid"
    valid_root.mkdir()
    valid_release, _ = _create_full_deploy_v3_release(valid_root, "release-valid", "a")
    valid = verify(valid_release)
    assert valid.returncode == 0, valid.stderr

    override_root = tmp_path / "valid-override"
    override_root.mkdir()
    override_release, _ = _create_full_deploy_v3_release(
        override_root, "release-valid-override", "a"
    )
    override = override_release / "rollback-images.override.yml"
    override.write_text("services: {}\n", encoding="utf-8")
    _replace_release_manifest_value(override_release, "HAS_IMAGE_OVERRIDE", "1")
    _replace_release_manifest_value(
        override_release,
        "IMAGE_OVERRIDE_SHA256",
        hashlib.sha256(override.read_bytes()).hexdigest(),
    )
    valid_override = verify(override_release)
    assert valid_override.returncode == 0, valid_override.stderr
    assert f"-f {override}" in docker_log.read_text(encoding="utf-8").splitlines()[-1]

    cases = (
        "data-symlink",
        "manifest-symlink",
        "stray-override",
        "dangling-override",
        "missing-runtime",
        "flag-one-missing-digest",
        "flag-one-wrong-digest",
        "flag-zero-with-digest",
        "flag-one-symlink",
    )
    for case in cases:
        case_root = tmp_path / case
        case_root.mkdir()
        release, _ = _create_full_deploy_v3_release(case_root, f"release-{case}", "a")
        override = release / "rollback-images.override.yml"
        if case == "data-symlink":
            external_data = case_root / "external-data"
            shutil.move(release / "data", external_data)
            (release / "data").symlink_to(external_data, target_is_directory=True)
        elif case == "manifest-symlink":
            external_manifest = case_root / "external-manifest.sha256"
            shutil.move(release / "data-files.sha256", external_manifest)
            (release / "data-files.sha256").symlink_to(external_manifest)
        elif case == "stray-override":
            override.write_text("services: {}\n", encoding="utf-8")
        elif case == "dangling-override":
            override.symlink_to(case_root / "missing-override.yml")
        elif case == "missing-runtime":
            for evidence_name in ("environment.env", "success.env"):
                evidence = release / evidence_name
                lines = [
                    line
                    for line in evidence.read_text(encoding="utf-8").splitlines()
                    if not line.startswith("JOBSEEK_RUNTIME_CONTRACT_SHA256=")
                ]
                evidence.write_text("\n".join(lines) + "\n", encoding="utf-8")
            _refresh_release_snapshot_digests(release)
        elif case == "flag-one-missing-digest":
            override.write_text("services: {}\n", encoding="utf-8")
            _replace_release_manifest_value(release, "HAS_IMAGE_OVERRIDE", "1")
        elif case == "flag-one-wrong-digest":
            override.write_text("services: {}\n", encoding="utf-8")
            _replace_release_manifest_value(release, "HAS_IMAGE_OVERRIDE", "1")
            _replace_release_manifest_value(release, "IMAGE_OVERRIDE_SHA256", "0" * 64)
        elif case == "flag-zero-with-digest":
            _replace_release_manifest_value(release, "IMAGE_OVERRIDE_SHA256", "0" * 64)
        elif case == "flag-one-symlink":
            external_override = case_root / "external-override.yml"
            external_override.write_text("services: {}\n", encoding="utf-8")
            override.symlink_to(external_override)
            _replace_release_manifest_value(release, "HAS_IMAGE_OVERRIDE", "1")
            _replace_release_manifest_value(
                release,
                "IMAGE_OVERRIDE_SHA256",
                hashlib.sha256(external_override.read_bytes()).hexdigest(),
            )
        result = verify(release)
        assert result.returncode != 0, f"{case} unexpectedly verified"

    verification = remote_script[
        remote_script.index("verify_release_generation() {") : remote_script.index(
            "\nprevious_redis_ref="
        )
    ]
    assert 'active_image_override="$image_override"' in remote_script
    assert 'if [[ -f "$active_generation/rollback-images.override.yml" ]]' not in remote_script
    assert 'test ! -L "$generation/data-files.sha256"' in verification
    assert 'test "${#runtime_environment_values[@]}" -eq 1' in verification


def test_bridge_corruption_is_rejected_by_host_deploy_and_murmur(
    tmp_path: Path,
) -> None:
    release_root = tmp_path / "releases"
    candidates = tmp_path / "candidates"
    candidates.mkdir()
    legacy, identity = _create_legacy_format2_release(release_root, "legacy.production", "b")
    active = tmp_path / ".crawler-active-release"
    active.symlink_to(legacy)
    live_env = tmp_path / ".env"
    shutil.copyfile(legacy / "environment.env", live_env)
    live_env.chmod(0o600)
    host_env = _csv_host_test_environment(tmp_path, release_root, active, live_env, candidates)
    binary_dir = tmp_path / "bin"
    _install_csv_host_docker(binary_dir)
    previous_revision = "c" * 40
    candidate_id, data_contract, archive_sha = _create_csv_candidate(
        candidates,
        previous_revision,
        81,
        1,
        {"boards.csv": b"slug\nB\n"},
        identity["runtime_contract"],
        [previous_revision, identity["source_revision"]],
    )
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
    bootstrap = subprocess.run(
        [
            bash,
            str(CSV_SYNC_HOST),
            "--bootstrap-current",
            previous_revision,
            identity["runtime_contract"],
            data_contract,
            candidate_id,
            archive_sha,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=host_env,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr
    valid_bridge = active.resolve()

    deploy_script = DEPLOY_SH.read_text()
    deploy_verification_helpers = deploy_script[
        deploy_script.index("verify_active_snapshot_file() {") : deploy_script.index(
            "\nwrite_exact_csv_manifest() {"
        )
    ]
    deploy_release_helpers = deploy_script[
        deploy_script.index("read_exact_release_value() {") : deploy_script.index(
            "\npublish_legacy_success_marker() {"
        )
    ]
    murmur_workflow = yaml.safe_load(MURMUR_DEPLOY_WORKFLOW.read_text())
    murmur_remote = next(
        step["with"]["script"]
        for step in murmur_workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy via SSH"
    )
    murmur_verifier = murmur_remote[
        murmur_remote.index("verify_snapshot() {") : murmur_remote.index("\nprevious_redis_ref=")
    ]

    def verify_with_deploy(release: Path) -> subprocess.CompletedProcess[str]:
        harness = "\n".join(
            (
                "set -euo pipefail",
                'OWNER="colophon-group"',
                f'BRIDGE_VERIFIER="{BRIDGE_VERIFIER}"',
                f'DEPLOY_DIR="{tmp_path}"',
                f'ACTIVE_RELEASE_ROOT="{release_root}"',
                f'ACTIVE_RELEASE_POINTER="{active}"',
                'ACTIVE_RELEASE_DIR=""',
                'ACTIVE_COMPOSE_SNAPSHOT=""',
                'ACTIVE_COMPOSE_SNAPSHOT_SHA256=""',
                'ACTIVE_ENV_SNAPSHOT=""',
                'ACTIVE_ENV_SNAPSHOT_SHA256=""',
                'DEPLOY_SUCCESS_FILE=""',
                'ACTIVE_RELEASE_MANIFEST=""',
                'ACTIVE_RELEASE_FORMAT=""',
                'ACTIVE_IMAGE_OVERRIDE=""',
                'ACTIVE_DATA_SNAPSHOT=""',
                'ACTIVE_DATA_FILES_MANIFEST=""',
                'COMPOSE_PROJECT_NAME="deploy"',
                deploy_verification_helpers,
                deploy_release_helpers,
                "verify_active_deploy_snapshot",
            )
        )
        return subprocess.run(
            [bash, "-c", harness],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{binary_dir}:{os.environ['PATH']}"},
        )

    def verify_with_murmur(release: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                bash,
                "-c",
                "set -euo pipefail\n"
                + f'bridge_verifier="{BRIDGE_VERIFIER}"\nOWNER="colophon-group"\n'
                + murmur_verifier
                + '\nverify_release_generation "$1"',
                "verify",
                str(release),
            ],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{binary_dir}:{os.environ['PATH']}"},
        )

    for index, corruption in enumerate(
        ("attestation-epoch", "legacy-field", "source-evidence"), start=1
    ):
        corrupted = release_root / f"corrupted-{corruption}"
        shutil.copytree(valid_bridge, corrupted)
        if corruption == "attestation-epoch":
            attestation = corrupted / "runtime-attestation.env"
            attestation.write_text(
                "\n".join(
                    line
                    for line in attestation.read_text(encoding="utf-8").splitlines()
                    if line != f"COMPATIBLE_REVISION={identity['source_revision']}"
                )
                + "\n",
                encoding="utf-8",
            )
            _replace_release_manifest_value(
                corrupted,
                "LEGACY_RUNTIME_ATTESTATION_SHA256",
                hashlib.sha256(attestation.read_bytes()).hexdigest(),
            )
        elif corruption == "legacy-field":
            _replace_release_manifest_value(
                corrupted,
                "LEGACY_SOURCE_CRAWLER_IMAGE_REF",
                "ghcr.io/colophon-group/jobseek-crawler@sha256:" + "f" * 64,
            )
        else:
            source_environment = corrupted / "legacy-source-environment.env"
            source_content = source_environment.read_text(encoding="utf-8")
            changed_source = source_content.replace(
                f"JOBSEEK_DEPLOY_REVISION={identity['source_revision']}",
                "JOBSEEK_DEPLOY_REVISION=" + "f" * 40,
            )
            assert changed_source != source_content
            source_environment.write_text(changed_source, encoding="utf-8")
            _replace_release_manifest_value(
                corrupted,
                "LEGACY_SOURCE_ENVIRONMENT_SHA256",
                hashlib.sha256(source_environment.read_bytes()).hexdigest(),
            )

        active.unlink()
        active.symlink_to(corrupted)
        shutil.copyfile(corrupted / "environment.env", live_env)
        live_env.chmod(0o600)
        next_revision = chr(ord("d") + index - 1) * 40
        next_candidate, next_contract, next_archive = _create_csv_candidate(
            candidates,
            next_revision,
            81 + index,
            1,
            {"boards.csv": f"slug\n{corruption}\n".encode()},
        )
        host = subprocess.run(
            [
                bash,
                str(CSV_SYNC_HOST),
                "--bootstrap-current",
                next_revision,
                identity["runtime_contract"],
                next_contract,
                next_candidate,
                next_archive,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=host_env,
        )
        deploy = verify_with_deploy(corrupted)
        murmur = verify_with_murmur(corrupted)
        for consumer, result in (("host", host), ("deploy", deploy), ("murmur", murmur)):
            assert result.returncode != 0, f"{consumer} accepted {corruption}"
            assert "legacy bridge verification failed" in result.stderr
        assert active.resolve() == corrupted.resolve()
        assert not (candidates / next_candidate).exists()


def test_murmur_bootstrap_digest_resolver_fails_without_exact_identity(
    tmp_path: Path,
) -> None:
    workflow = yaml.safe_load(MURMUR_DEPLOY_WORKFLOW.read_text())
    remote_script = next(
        step["with"]["script"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy via SSH"
    )
    resolver = remote_script[
        remote_script.index("resolve_running_digest() {") : remote_script.index(
            "fsync_release_generation() {"
        )
    ]
    repository = "ghcr.io/colophon-group/jobseek-crawler"
    exact_ref = repository + "@sha256:" + "a" * 64
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _write_executable(
        binary_dir / "docker",
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args[0] == 'inspect' and '{{.Config.Image}}' in args:\n"
        "    print(os.environ['CONFIGURED_REF'])\n"
        "elif args[0] == 'inspect' and '{{.Image}}' in args:\n"
        "    print('sha256:' + 'f' * 64)\n"
        "elif args[:2] == ['image', 'inspect']:\n"
        "    print(os.environ.get('REPO_DIGESTS', ''))\n"
        "else:\n"
        "    raise SystemExit(90)\n",
    )
    harness = "\n".join(
        (
            "set -uo pipefail",
            resolver,
            f'resolve_running_digest deploy-worker-1-1 "{repository}"',
        )
    )
    bash_binary = (
        str(Path("/opt/homebrew/bin/bash")) if Path("/opt/homebrew/bin/bash").exists() else "bash"
    )

    direct = subprocess.run(
        [bash_binary, "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
            "CONFIGURED_REF": exact_ref,
            "REPO_DIGESTS": "",
        },
    )
    fallback = subprocess.run(
        [bash_binary, "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
            "CONFIGURED_REF": repository + ":v1.2.3",
            "REPO_DIGESTS": exact_ref,
        },
    )
    unresolved = subprocess.run(
        [bash_binary, "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
            "CONFIGURED_REF": repository + ":latest",
            "REPO_DIGESTS": "",
        },
    )

    assert direct.returncode == 0
    assert direct.stdout.strip() == exact_ref
    assert fallback.returncode == 0
    assert fallback.stdout.strip() == exact_ref
    assert unresolved.returncode != 0
    assert unresolved.stdout == ""


def test_coupled_failure_after_murmur_promotion_restores_digest_generation(
    tmp_path: Path,
) -> None:
    script = DEPLOY_SH.read_text()
    restore = script[
        script.index("restore_previous_deploy_specs() {") : script.index(
            "reconciliation_wrapper_is_compatible() {"
        )
    ]
    rollback_support = script[
        script.index("configure_rollback_compose_contract() {") : script.index(
            "rollback_deploy() {"
        )
    ]
    rollback = script[script.index("rollback_deploy() {") : script.index("arm_deploy_rollback() {")]

    deploy_dir = tmp_path / "deploy"
    deploy_dir.mkdir()
    live_env = deploy_dir / ".env"
    rollback_env = deploy_dir / ".env.rollback"
    live_compose = deploy_dir / "docker-compose.yml"
    rollback_archive = deploy_dir / ".deploy-spec.rollback.tar"
    image_override = deploy_dir / "release-previous" / "rollback-images.override.yml"
    image_override.parent.mkdir()
    pool_override = deploy_dir / ".crawler-rollback-pool-budget.override.yml"
    log = deploy_dir / "rollback.log"

    crawler_ref = "ghcr.io/colophon-group/jobseek-crawler@sha256:" + "a" * 64
    browser_ref = "ghcr.io/colophon-group/jobseek-crawler-browser@sha256:" + "b" * 64
    promoted_shim_ref = "ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + "c" * 64
    redis_ref = "redis@sha256:" + "d" * 64
    alloy_ref = "grafana/alloy@sha256:" + "e" * 64
    rollback_env.write_text(
        "\n".join(
            (
                "OWNER=colophon-group",
                "CRAWLER_IMAGE_TAG=v1.2.3",
                f"CRAWLER_IMAGE_REF={crawler_ref}",
                f"BROWSER_IMAGE_REF={browser_ref}",
                f"SHIM_IMAGE_REF={promoted_shim_ref}",
                "MURMUR_TOKEN=rotated-token",
                "LOCAL_DATABASE_URL=postgresql://rotated-dsn",
                "",
            )
        ),
        encoding="utf-8",
    )
    live_env.write_text("failed-release\n", encoding="utf-8")
    live_compose.write_text("services:\n  worker-1:\n    image: failed:latest\n", encoding="utf-8")
    legacy_compose = deploy_dir / "legacy-compose.yml"
    legacy_compose.write_text(
        "services:\n"
        "  worker-1:\n"
        "    image: ghcr.io/colophon-group/jobseek-crawler:latest\n"
        "  murmur-shim:\n"
        "    image: ghcr.io/colophon-group/jobseek-murmur-shim:latest\n",
        encoding="utf-8",
    )
    with tarfile.open(rollback_archive, "w") as archive:
        archive.add(legacy_compose, arcname="docker-compose.yml")
    image_override.write_text(
        "services:\n"
        f"  redis:\n    image: {redis_ref}\n"
        f"  worker-1:\n    image: {crawler_ref}\n"
        f"  worker-2:\n    image: {crawler_ref}\n"
        f"  worker-3:\n    image: {crawler_ref}\n"
        f"  exporter:\n    image: {crawler_ref}\n"
        f"  drain:\n    image: {crawler_ref}\n"
        f"  browser-1:\n    image: {browser_ref}\n"
        f"  murmur-shim-runtime-init:\n    image: {crawler_ref}\n"
        f"  murmur-shim:\n    image: {promoted_shim_ref}\n"
        f"  alloy:\n    image: {alloy_ref}\n",
        encoding="utf-8",
    )
    pool_override.write_text("services: {}\n", encoding="utf-8")

    binary_dir = deploy_dir / "bin"
    binary_dir.mkdir()
    _write_executable(
        binary_dir / "docker",
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import re\n"
        "import sys\n"
        f"log = Path({str(log)!r})\n"
        f"expected_compose = {str(live_compose)!r}\n"
        f"expected_images = {str(image_override)!r}\n"
        f"expected_pool = {str(pool_override)!r}\n"
        f"expected_shim = {promoted_shim_ref!r}\n"
        "args = sys.argv[1:]\n"
        "if 'stop' in args:\n"
        "    raise SystemExit(0)\n"
        "assert 'up' in args and '--remove-orphans' in args, args\n"
        "compose_files = [args[index + 1] for index, value in enumerate(args) if value == '-f']\n"
        "assert compose_files == [\n"
        "    expected_compose, expected_images, expected_pool\n"
        "], compose_files\n"
        "env_file = args[args.index('--env-file') + 1]\n"
        "images = [\n"
        "    line.strip().split('image: ', 1)[1]\n"
        "    for line in Path(expected_images).read_text().splitlines()\n"
        "    if line.strip().startswith('image: ')\n"
        "]\n"
        "assert len(images) == 10\n"
        "assert all(re.search(r'@sha256:[0-9a-f]{64}$', image) for image in images)\n"
        "assert all('latest' not in image for image in images)\n"
        "assert expected_shim in images\n"
        "assert 'MURMUR_TOKEN=rotated-token\\n' in Path(env_file).read_text()\n"
        "log.write_text(f'digest-override={expected_images}\\n')\n",
    )

    harness = "\n".join(
        (
            "set -Eeuo pipefail",
            f'DEPLOY_DIR="{deploy_dir}"',
            f'ENV_FILE="{live_env}"',
            f'ROLLBACK_ENV_FILE="{rollback_env}"',
            f'ROLLBACK_SPEC_ARCHIVE="{rollback_archive}"',
            f'ROLLBACK_POOL_OVERRIDE="{pool_override}"',
            f'ROLLBACK_ACTIVE_RELEASE_TARGET="{image_override.parent}"',
            f'ROLLBACK_ACTIVE_IMAGE_OVERRIDE="{image_override}"',
            "ENV_FILE_WAS_PRESENT=1",
            "ROLLBACK_ARMED=1",
            "ROLLBACK_RUNNING=0",
            'COMPOSE_PROJECT_NAME="deploy"',
            'MAINTENANCE_MARKER_NAME=""',
            "stop_maintenance_window() { :; }",
            "activate_release_generation() { :; }",
            "verify_active_deploy_snapshot() { :; }",
            "publish_legacy_success_marker() { :; }",
            restore,
            rollback_support,
            rollback,
            "wait_for_rollback_core_services() { :; }",
            "rollback_deploy 23",
        )
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{binary_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 23, result.stderr
    restored_env = live_env.read_text(encoding="utf-8")
    assert "MURMUR_TOKEN=rotated-token\n" in restored_env
    assert f"SHIM_IMAGE_REF={promoted_shim_ref}\n" in restored_env
    assert f"COMPOSE_FILE={live_compose}:{image_override}:{pool_override}\n" in restored_env
    assert log.read_text(encoding="utf-8").strip() == f"digest-override={image_override}"


def test_murmur_rotation_is_persisted_before_active_generation_commit() -> None:
    workflow = yaml.safe_load(MURMUR_DEPLOY_WORKFLOW.read_text())
    remote_script = next(
        step["with"]["script"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy via SSH"
    )
    transaction = remote_script[
        remote_script.index('env_candidate="$(mktemp') : remote_script.index(
            "rollback_armed=0",
            remote_script.index('test "$(readlink "$active_release")"'),
        )
    ]
    assert 'install -m 0644 "$previous_active_generation/docker-compose.yml"' in transaction
    assert '"$previous_active_generation/environment.env"' in transaction
    token_filter = transaction.index("MURMUR_TOKEN|LOCAL_DATABASE_URL|COMPOSE_FILE")
    token_write = transaction.index("printf 'MURMUR_TOKEN=%s")
    dsn_write = transaction.index("printf 'LOCAL_DATABASE_URL=%s")
    env_publish = transaction.index('mv "$env_candidate" "$live_env"')
    health = transaction.index("curl -sf http://localhost:8080/health")
    generation_token = transaction.index("printf 'MURMUR_TOKEN=%s", health)
    generation_dsn = transaction.index("printf 'LOCAL_DATABASE_URL=%s", health)
    release_intent = transaction.index("release_activated=1")
    release_publish = transaction.index('activate_release_generation "$murmur_generation"')
    release_verify = transaction.index('verify_release_generation "$murmur_generation"')
    assert (
        token_filter
        < token_write
        < dsn_write
        < env_publish
        < health
        < generation_token
        < generation_dsn
        < release_intent
        < release_publish
        < release_verify
    )


def test_deploy_requires_exact_reconciliation_wrapper_before_activation() -> None:
    script = DEPLOY_SH.read_text()
    workflow = DEPLOY_WORKFLOW.read_text()

    guard = script.index("\nensure_reconciliation_wrapper_compatible\n")
    compose_preseed = script.index("\nverify_active_deploy_snapshot\n")
    rollback_cleanup = script.index(
        'rm -f "$ROLLBACK_ENV_FILE" "$ROLLBACK_SPEC_ARCHIVE"', compose_preseed
    )
    snapshot = script.index("\nsnapshot_active_deploy_specs\n")
    activation = script.index("\nactivate_staged_deploy_specs\n")
    env_write = script.index('cat > "$ENV_FILE"')
    assert guard < compose_preseed < rollback_cleanup < snapshot < activation < env_write
    assert "sha256sum /usr/local/sbin/jobseek-crawler-reconciliation" in script
    assert '--expected-wrapper-sha256 "$JOBSEEK_RECONCILIATION_WRAPPER_SHA256"' in script
    assert "systemctl is-enabled --quiet jobseek-crawler-reconciliation.timer" in script
    assert "systemctl is-active --quiet jobseek-crawler-reconciliation.timer" in script
    assert "RECONCILIATION_COMPAT_WAIT_SECONDS:-1200" in script
    assert "Derive reconciliation wrapper contract" in workflow
    assert "sha256sum deploy/reconciliation/run.sh" in workflow
    assert "JOBSEEK_RECONCILIATION_WRAPPER_SHA256" in workflow


def test_crawler_host_mutation_waits_for_same_revision_murmur_workflow() -> None:
    workflow = DEPLOY_WORKFLOW.read_text()
    parsed = yaml.safe_load(workflow)

    wait = workflow.index("- name: Wait for same-revision murmur-shim deployment")
    host_copy = workflow.index("- name: Copy deploy files")
    assert wait < host_copy
    assert parsed["jobs"]["murmur"]["timeout-minutes"] == 360
    assert set(parsed["jobs"]["deploy"]["needs"]) == {"murmur", "build"}
    assert "actions: read" in workflow
    assert "actions/workflows/deploy-murmur-shim.yml/runs" in workflow
    assert '-f head_sha="$GITHUB_SHA"' in workflow
    assert "deadline=$((SECONDS + 21000))" in workflow
    assert "behind one predecessor" in workflow
    assert "same-revision murmur-shim workflow concluded" in workflow
    assert "timed out waiting for same-revision murmur-shim deployment" in workflow
    assert "malformed or mismatched same-revision Murmur workflow attestation" in workflow
    assert 'gh run download "$murmur_run_id"' in workflow
    assert 'revision_ref="${repository}@${release_digest}"' in workflow
    assert 'docker buildx imagetools inspect "$revision_ref"' in workflow
    assert 'printf \'image_ref=%s\\n\' "$shim_image_ref" >>"$GITHUB_OUTPUT"' in workflow
    assert "SHIM_IMAGE_REF: ${{ steps.murmur.outputs.image_ref }}" in workflow


def test_murmur_revision_resolver_fails_closed_on_invalid_attestations(
    tmp_path: Path,
) -> None:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    wait_script = next(
        step["run"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Wait for same-revision murmur-shim deployment"
    )
    resolver = wait_script[wait_script.index("# Validate the exact recorded manifest") :]
    revision = "1" * 40
    manifest_digest = "sha256:" + "2" * 64
    runnable_digest = "sha256:" + "3" * 64
    provenance_digest = "sha256:" + "4" * 64
    valid_manifest = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "digest": manifest_digest,
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": runnable_digest,
                "platform": {"architecture": "amd64", "os": "linux"},
            },
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": provenance_digest,
                "platform": {"architecture": "unknown", "os": "unknown"},
                "annotations": {
                    "vnd.docker.reference.digest": runnable_digest,
                    "vnd.docker.reference.type": "attestation-manifest",
                },
            },
        ],
    }
    valid_provenance = {
        "SLSA": {
            "invocation": {
                "configSource": {
                    "uri": f"https://github.com/colophon-group/jobseek/commit/{revision}"
                }
            }
        }
    }
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    jq_binary = Path("/opt/homebrew/bin/jq")
    if not jq_binary.exists():
        discovered_jq = shutil.which("jq")
        assert discovered_jq is not None
        jq_binary = Path(discovered_jq)
    _write_executable(
        binary_dir / "jq",
        f'#!/usr/bin/env bash\nexec "{jq_binary}" "$@"\n',
    )
    _write_executable(
        binary_dir / "docker",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$*" == *".Manifest"* ]]; then\n'
        "  printf '%s\\n' \"$TEST_MANIFEST_JSON\"\n"
        'elif [[ "$*" == *".Provenance"* ]]; then\n'
        "  printf '%s\\n' \"$TEST_PROVENANCE_JSON\"\n"
        "else\n"
        "  exit 90\n"
        "fi\n",
    )
    bash_binary = (
        Path("/opt/homebrew/bin/bash") if Path("/opt/homebrew/bin/bash").exists() else Path("bash")
    )

    def run_resolver(manifest: dict[str, object], provenance: dict[str, object]):
        output = tmp_path / "github-output"
        output.write_text("", encoding="utf-8")
        result = subprocess.run(
            [str(bash_binary), "-c", f"set -euo pipefail\n{resolver}"],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{binary_dir}:{os.environ['PATH']}",
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REPOSITORY_OWNER": "colophon-group",
                "GITHUB_SHA": revision,
                "release_digest": manifest_digest,
                "TEST_MANIFEST_JSON": json.dumps(manifest, separators=(",", ":")),
                "TEST_PROVENANCE_JSON": json.dumps(provenance, separators=(",", ":")),
            },
        )
        return result, output.read_text(encoding="utf-8")

    accepted, output = run_resolver(valid_manifest, valid_provenance)
    assert accepted.returncode == 0, accepted.stderr
    assert output == (f"image_ref=ghcr.io/colophon-group/jobseek-murmur-shim@{manifest_digest}\n")

    invalid_cases: list[tuple[dict[str, object], dict[str, object], str]] = []
    missing = json.loads(json.dumps(valid_manifest))
    missing["manifests"] = missing["manifests"][:1]
    invalid_cases.append((missing, valid_provenance, "zero or multiple SLSA"))
    multiple = json.loads(json.dumps(valid_manifest))
    duplicate = json.loads(json.dumps(multiple["manifests"][1]))
    duplicate["digest"] = "sha256:" + "5" * 64
    multiple["manifests"].append(duplicate)
    invalid_cases.append((multiple, valid_provenance, "zero or multiple SLSA"))
    malformed_attestation = json.loads(json.dumps(valid_manifest))
    malformed_attestation["manifests"][1]["digest"] = "sha256:not-a-digest"
    invalid_cases.append((malformed_attestation, valid_provenance, "zero or multiple SLSA"))
    malformed = json.loads(json.dumps(valid_manifest))
    malformed["digest"] = "sha256:not-a-digest"
    invalid_cases.append((malformed, valid_provenance, "zero or multiple manifest"))
    mismatched_provenance = json.loads(json.dumps(valid_provenance))
    mismatched_provenance["SLSA"]["invocation"]["configSource"]["uri"] = "0" * 40
    invalid_cases.append(
        (valid_manifest, mismatched_provenance, "does not attest the exact source revision")
    )

    for manifest, provenance, expected_error in invalid_cases:
        rejected, output = run_resolver(manifest, provenance)
        assert rejected.returncode != 0
        assert output == ""
        assert expected_error in rejected.stderr


def test_murmur_release_record_is_bound_to_exact_push_run() -> None:
    crawler_workflow = DEPLOY_WORKFLOW.read_text()
    murmur_workflow = MURMUR_DEPLOY_WORKFLOW.read_text()

    assert "murmur-release-${{ github.run_id }}-${{ github.run_attempt }}" in murmur_workflow
    assert "run_id: $run_id" in murmur_workflow
    assert "run_attempt: $run_attempt" in murmur_workflow
    assert "head_sha: $head_sha" in murmur_workflow
    assert "event: $event" in murmur_workflow
    assert "image_digest: $image_digest" in murmur_workflow
    assert 'gh run download "$murmur_run_id"' in crawler_workflow
    assert '--name "murmur-release-${murmur_run_id}-${murmur_run_attempt}"' in crawler_workflow
    assert ".run_id == $run_id" in crawler_workflow
    assert ".run_attempt == $run_attempt" in crawler_workflow
    assert ".head_sha == $head_sha" in crawler_workflow
    assert '.event == "push"' in crawler_workflow
    resolver = crawler_workflow[crawler_workflow.index("# Download the immutable release record") :]
    assert "${repository}:${GITHUB_SHA}" not in resolver


def test_murmur_release_record_rejects_cross_run_and_cross_event_digest(
    tmp_path: Path,
) -> None:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    wait_script = next(
        step["run"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Wait for same-revision murmur-shim deployment"
    )
    record_gate = wait_script[
        wait_script.index("# Download the immutable release record") : wait_script.index(
            "# Validate the exact recorded manifest"
        )
    ]
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    jq_binary = Path("/opt/homebrew/bin/jq")
    if not jq_binary.exists():
        discovered_jq = shutil.which("jq")
        assert discovered_jq is not None
        jq_binary = Path(discovered_jq)
    _write_executable(binary_dir / "jq", f'#!/usr/bin/env bash\nexec "{jq_binary}" "$@"\n')
    _write_executable(
        binary_dir / "gh",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'destination=""\n'
        "while [[ $# -gt 0 ]]; do\n"
        '  if [[ "$1" == --dir ]]; then destination="$2"; shift 2; else shift; fi\n'
        "done\n"
        '[[ -n "$destination" ]]\n'
        'printf \'%s\\n\' "$TEST_RELEASE_RECORD" >"$destination/murmur-release.json"\n',
    )
    revision = "1" * 40
    digest = "sha256:" + "2" * 64
    bash_binary = (
        Path("/opt/homebrew/bin/bash") if Path("/opt/homebrew/bin/bash").exists() else Path("bash")
    )

    def validate(record: dict[str, object]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(bash_binary), "-c", f"set -euo pipefail\n{record_gate}"],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "PATH": f"{binary_dir}:{os.environ['PATH']}",
                "GITHUB_REPOSITORY": "colophon-group/jobseek",
                "GITHUB_SHA": revision,
                "murmur_run_id": "12345",
                "murmur_run_attempt": "3",
                "TEST_RELEASE_RECORD": json.dumps(record, separators=(",", ":")),
            },
        )

    valid = {
        "run_id": 12345,
        "run_attempt": 3,
        "head_sha": revision,
        "event": "push",
        "image_digest": digest,
    }
    assert validate(valid).returncode == 0
    for key, value in (
        ("run_id", 54321),
        ("run_attempt", 2),
        ("head_sha", "3" * 40),
        ("event", "workflow_dispatch"),
        ("image_digest", "sha256:not-a-digest"),
    ):
        mismatched = dict(valid)
        mismatched[key] = value
        rejected = validate(mismatched)
        assert rejected.returncode != 0, (key, rejected.stdout, rejected.stderr)


def test_release_pointer_never_exposes_a_partially_prepared_generation(tmp_path: Path) -> None:
    script = DEPLOY_SH.read_text()
    activation = script[
        script.index("activate_release_generation() {") : script.index(
            "publish_legacy_success_marker() {"
        )
    ]
    release_root = tmp_path / "releases"
    release_root.mkdir()
    old = release_root / "old"
    new = release_root / "new"
    old.mkdir()
    new.mkdir()
    (old / "success.env").write_text("JOBSEEK_DEPLOY_REVISION=old\n", encoding="utf-8")
    # Model a process dying after writing only part of the candidate bundle.
    (new / "success.env").write_text("JOBSEEK_DEPLOY_REVISION=new\n", encoding="utf-8")
    active = tmp_path / ".crawler-active-release"
    active.symlink_to(old)
    assert (active / "success.env").read_text(encoding="utf-8").endswith("old\n")
    # Finishing the unreferenced directory cannot change what readers see;
    # only the final pointer replacement publishes the complete generation.
    for name in (
        "docker-compose.yml",
        "docker-compose.sha256",
        "environment.env",
        "environment.sha256",
        "release.manifest",
    ):
        (new / name).write_text(f"{name}\n", encoding="utf-8")

    harness = "\n".join(
        (
            "set -euo pipefail",
            'DEPLOY_DIR="$TEST_DEPLOY_DIR"',
            'ACTIVE_RELEASE_ROOT="$TEST_RELEASE_ROOT"',
            'ACTIVE_RELEASE_POINTER="$TEST_ACTIVE"',
            activation,
            'activate_release_generation "$TEST_NEW"',
        )
    )
    completed = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "TEST_DEPLOY_DIR": str(tmp_path),
            "TEST_RELEASE_ROOT": str(release_root),
            "TEST_ACTIVE": str(active),
            "TEST_NEW": str(new),
        },
    )
    assert completed.returncode == 0, completed.stderr
    assert active.is_symlink()
    assert active.resolve() == new
    assert (active / "success.env").read_text(encoding="utf-8").endswith("new\n")


def test_murmur_failure_restores_old_contract_before_clean_environment_restart(
    tmp_path: Path,
) -> None:
    workflow = yaml.safe_load(MURMUR_DEPLOY_WORKFLOW.read_text())
    remote_script = next(
        step["with"]["script"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy via SSH"
    )
    rollback = remote_script[
        remote_script.index("rollback_shim() {") : remote_script.index("trap rollback_shim EXIT")
    ]
    live_compose = tmp_path / "docker-compose.yml"
    live_env = tmp_path / ".env"
    previous_compose = tmp_path / "previous-compose.yml"
    previous_env = tmp_path / "previous.env"
    staged_compose = tmp_path / "staged-compose.yml"
    log = tmp_path / "rollback.log"
    live_compose.write_text("new-compose\n", encoding="utf-8")
    live_env.write_text("MURMUR_TOKEN=new-secret\n", encoding="utf-8")
    previous_compose.write_text("old-compose\n", encoding="utf-8")
    previous_env.write_text("MURMUR_TOKEN=old-secret\n", encoding="utf-8")
    staged_compose.write_text("staged-compose\n", encoding="utf-8")
    previous_ref = "ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + "a" * 64
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _write_executable(
        binary_dir / "docker",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$1" == compose ]]; then\n'
        f'  printf "compose=%s\\n" "$(cat "{live_compose}")" >>"{log}"\n'
        f'  printf "env=%s\\n" "$(cat "{live_env}")" >>"{log}"\n'
        f'  printf "process-token=%s\\n" "${{MURMUR_TOKEN:-unset}}" >>"{log}"\n'
        'elif [[ "$1" == inspect ]]; then\n'
        f'  printf "%s\\n" "{previous_ref}"\n'
        "else\n"
        "  exit 91\n"
        "fi\n",
    )
    _write_executable(binary_dir / "curl", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(binary_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        binary_dir / "sha256sum",
        '#!/usr/bin/env bash\nexec shasum -a 256 "$1"\n',
    )
    harness = "\n".join(
        (
            "set -uo pipefail",
            f'live_compose="{live_compose}"',
            f'live_env="{live_env}"',
            f'previous_compose="{previous_compose}"',
            f'previous_env="{previous_env}"',
            f'staged_compose="{staged_compose}"',
            f'live_dir="{tmp_path}"',
            'env_candidate=""',
            'compose_candidate=""',
            "rollback_armed=1",
            "compose_activated=1",
            "env_activated=1",
            f'previous_compose_sha256="$(sha256sum "{previous_compose}" | awk \'{{print $1}}\')"',
            f'previous_env_sha256="$(sha256sum "{previous_env}" | awk \'{{print $1}}\')"',
            f'previous_shim_ref="{previous_ref}"',
            'active_image_override=""',
            "release_activated=0",
            'murmur_success_temporary=""',
            'MURMUR_TOKEN="new-secret"',
            "export MURMUR_TOKEN",
            rollback,
            "false",
            "rollback_shim",
        )
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{binary_dir}:{os.environ['PATH']}",
        },
    )
    assert result.returncode == 1, result.stderr
    assert live_compose.read_text(encoding="utf-8") == "old-compose\n"
    assert live_env.read_text(encoding="utf-8") == "MURMUR_TOKEN=old-secret\n"
    assert log.exists(), result.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "compose=old-compose",
        "env=MURMUR_TOKEN=old-secret",
        "process-token=unset",
    ]


def test_murmur_publication_login_and_pull_failures_restore_old_contract(
    tmp_path: Path,
) -> None:
    workflow = yaml.safe_load(MURMUR_DEPLOY_WORKFLOW.read_text())
    remote_script = next(
        step["with"]["script"]
        for step in workflow["jobs"]["deploy"]["steps"]
        if step.get("name") == "Deploy via SSH"
    )
    transaction_start = remote_script.index("rollback_shim() {")
    identity_gate = remote_script.index(
        'test "$(docker inspect deploy-murmur-shim-1', transaction_start
    )
    transaction_end = remote_script.index("rollback_armed=0", identity_gate)
    transaction = remote_script[transaction_start:transaction_end]
    old_ref = "ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + "a" * 64
    new_ref = "ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + "b" * 64
    crawler_ref = "ghcr.io/colophon-group/jobseek-crawler@sha256:" + "c" * 64
    browser_ref = "ghcr.io/colophon-group/jobseek-crawler-browser@sha256:" + "d" * 64

    for failure_mode in ("compose-publication", "login", "pull"):
        case_dir = tmp_path / failure_mode
        case_dir.mkdir()
        live_compose = case_dir / "docker-compose.yml"
        live_env = case_dir / ".env"
        previous_compose = case_dir / "previous-compose.yml"
        previous_env = case_dir / "previous.env"
        staged_compose = case_dir / "staged-compose.yml"
        injected = case_dir / "injected"
        log = case_dir / "rollback.log"
        live_compose.write_text("old-compose\n", encoding="utf-8")
        live_env.write_text(
            f"MURMUR_TOKEN=old-secret\nSHIM_IMAGE_REF={old_ref}\n", encoding="utf-8"
        )
        previous_compose.write_text("old-compose\n", encoding="utf-8")
        previous_env.write_text(
            f"MURMUR_TOKEN=old-secret\nSHIM_IMAGE_REF={old_ref}\n", encoding="utf-8"
        )
        staged_compose.write_text("new-compose\n", encoding="utf-8")
        binary_dir = case_dir / "bin"
        binary_dir.mkdir()
        _write_executable(
            binary_dir / "mv",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'destination="${!#}"\n'
            f'if [[ "{failure_mode}" == compose-publication && '
            f'"$destination" == "{live_compose}" && ! -e "{injected}" ]]; then\n'
            f'  : >"{injected}"\n'
            "  exit 88\n"
            "fi\n"
            'exec /bin/mv "$@"\n',
        )
        _write_executable(
            binary_dir / "docker",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'if [[ "$1" == login ]]; then\n'
            "  cat >/dev/null\n"
            f'  [[ "{failure_mode}" != login ]] || exit 89\n'
            'elif [[ "$1 $2" == "compose pull" ]]; then\n'
            f'  [[ "{failure_mode}" != pull ]] || exit 90\n'
            'elif [[ "$1" == compose && "$*" == *" up -d murmur-shim"* ]]; then\n'
            f'  printf "compose=%s\\n" "$(cat "{live_compose}")" >>"{log}"\n'
            f'  printf "env=%s\\n" "$(tr \'\\n\' \';\' <"{live_env}")" >>"{log}"\n'
            f'  printf "process-token=%s\\n" "${{MURMUR_TOKEN:-unset}}" >>"{log}"\n'
            'elif [[ "$1" == inspect ]]; then\n'
            f'  printf "%s\\n" "{old_ref}"\n'
            "else\n"
            "  exit 91\n"
            "fi\n",
        )
        _write_executable(binary_dir / "curl", "#!/usr/bin/env bash\nexit 0\n")
        _write_executable(binary_dir / "sleep", "#!/usr/bin/env bash\nexit 0\n")
        _write_executable(
            binary_dir / "sha256sum",
            '#!/usr/bin/env bash\nexec shasum -a 256 "$1"\n',
        )
        harness = "\n".join(
            (
                "set -Eeuo pipefail",
                f'live_dir="{case_dir}"',
                f'live_compose="{live_compose}"',
                f'live_env="{live_env}"',
                f'previous_compose="{previous_compose}"',
                f'previous_env="{previous_env}"',
                f'staged_compose="{staged_compose}"',
                f'previous_compose_sha256="$(sha256sum "{previous_compose}" | awk '
                "'{print $1}')\"",
                f'previous_env_sha256="$(sha256sum "{previous_env}" | awk \'{{print $1}}\')"',
                f'previous_shim_ref="{old_ref}"',
                f'CRAWLER_IMAGE_REF="{crawler_ref}"',
                f'BROWSER_IMAGE_REF="{browser_ref}"',
                f'SHIM_IMAGE_REF="{new_ref}"',
                'GHCR_PULL_TOKEN="pull-token"',
                'MURMUR_TOKEN="new-secret"',
                'LOCAL_DATABASE_URL="postgresql://new-dsn"',
                "export MURMUR_TOKEN LOCAL_DATABASE_URL",
                "rollback_armed=0",
                "compose_activated=0",
                "env_activated=0",
                "release_activated=0",
                'active_image_override=""',
                'murmur_success_temporary=""',
                'compose_candidate=""',
                'env_candidate=""',
                transaction,
            )
        )
        result = subprocess.run(
            ["bash", "-c", harness],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "PATH": f"{binary_dir}:{os.environ['PATH']}"},
        )

        assert result.returncode != 0, (failure_mode, result.stdout, result.stderr)
        assert live_compose.read_text(encoding="utf-8") == "old-compose\n"
        assert live_env.read_text(encoding="utf-8") == (
            f"MURMUR_TOKEN=old-secret\nSHIM_IMAGE_REF={old_ref}\n"
        )
        assert log.read_text(encoding="utf-8").splitlines() == [
            "compose=old-compose",
            f"env=MURMUR_TOKEN=old-secret;SHIM_IMAGE_REF={old_ref};",
            "process-token=unset",
        ]


def test_same_revision_murmur_digest_survives_crawler_rollback_and_retry(
    tmp_path: Path,
) -> None:
    script = DEPLOY_SH.read_text()
    resolver = script[
        script.index("resolve_shim_image_ref() {") : script.index("read_exact_shim_ref() {")
    ]
    old_ref = "ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + "a" * 64
    same_revision_ref = "ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + "b" * 64
    env_file = tmp_path / ".env"
    env_file.write_text(f"SHIM_IMAGE_REF={old_ref}\n", encoding="utf-8")
    harness = "\n".join(
        (
            "set -euo pipefail",
            'OWNER="colophon-group"',
            'COMPOSE_PROJECT_NAME="deploy"',
            'ENV_FILE="$TEST_ENV_FILE"',
            resolver,
            # Attempt one receives the digest attested by the successful
            # same-head Murmur workflow even though the active crawler
            # snapshot still names the previous digest.
            'SHIM_IMAGE_REF="$TEST_SAME_REVISION_REF"',
            "resolve_shim_image_ref",
            "printf 'first=%s\\n' \"$SHIM_IMAGE_REF\"",
            # Model the failed crawler attempt restoring the old .env, then
            # rerun with the same workflow output. The resolver must not read
            # the old digest back from live state.
            'printf \'SHIM_IMAGE_REF=%s\\n\' "$TEST_OLD_REF" >"$ENV_FILE"',
            'SHIM_IMAGE_REF="$TEST_SAME_REVISION_REF"',
            "resolve_shim_image_ref",
            "printf 'retry=%s\\n' \"$SHIM_IMAGE_REF\"",
        )
    )

    result = subprocess.run(
        [
            str(Path("/opt/homebrew/bin/bash"))
            if Path("/opt/homebrew/bin/bash").exists()
            else "bash",
            "-c",
            harness,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "TEST_ENV_FILE": str(env_file),
            "TEST_OLD_REF": old_ref,
            "TEST_SAME_REVISION_REF": same_revision_ref,
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"first={same_revision_ref}",
        f"retry={same_revision_ref}",
    ]


def test_shim_release_gate_requires_live_container_and_success_marker_equality(
    tmp_path: Path,
) -> None:
    script = DEPLOY_SH.read_text()
    verifier = script[
        script.index("read_exact_shim_ref() {") : script.index("verify_compose_service_image() {")
    ]
    image_ref = "ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + "c" * 64
    env_file = tmp_path / ".env"
    marker = tmp_path / ".crawler-deploy-success.env"
    env_file.write_text(f"SHIM_IMAGE_REF={image_ref}\n", encoding="utf-8")
    marker.write_text(f"SHIM_IMAGE_REF={image_ref}\n", encoding="utf-8")
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    _write_executable(
        binary_dir / "docker",
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "$1 $2 $3" == "compose ps -aq" ]]; then\n'
        "  printf 'shim-container\\n'\n"
        'elif [[ "$1 $2" == "inspect shim-container" ]]; then\n'
        "  printf '%s\\n' \"$TEST_CONTAINER_REF\"\n"
        "else\n"
        "  exit 91\n"
        "fi\n",
    )
    harness = "\n".join(
        (
            "set -euo pipefail",
            'OWNER="colophon-group"',
            'ENV_FILE="$TEST_ENV_FILE"',
            'SHIM_IMAGE_REF="$TEST_EXPECTED_REF"',
            verifier,
            'verify_shim_deploy_contract "$TEST_MARKER"',
        )
    )
    base_env = {
        **os.environ,
        "PATH": f"{binary_dir}:{os.environ['PATH']}",
        "TEST_ENV_FILE": str(env_file),
        "TEST_MARKER": str(marker),
        "TEST_EXPECTED_REF": image_ref,
    }

    accepted = subprocess.run(
        [
            str(Path("/opt/homebrew/bin/bash"))
            if Path("/opt/homebrew/bin/bash").exists()
            else "bash",
            "-c",
            harness,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**base_env, "TEST_CONTAINER_REF": image_ref},
    )
    assert accepted.returncode == 0, accepted.stderr

    mismatched = subprocess.run(
        [
            str(Path("/opt/homebrew/bin/bash"))
            if Path("/opt/homebrew/bin/bash").exists()
            else "bash",
            "-c",
            harness,
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **base_env,
            "TEST_CONTAINER_REF": ("ghcr.io/colophon-group/jobseek-murmur-shim@sha256:" + "d" * 64),
        },
    )
    assert mismatched.returncode != 0
    assert "live environment, container, and success marker disagree" in mismatched.stderr


def test_operator_worker_restart_uses_compose_credential_allowlist() -> None:
    agents = AGENTS_MD.read_text()
    container_management = agents[
        agents.index("### Container Management") : agents.index("### Disk and Docker GC")
    ]

    assert "docker compose up -d --force-recreate <service>" in container_management
    assert "--env-file /home/deploy/.env" not in container_management
    assert "docker run -d --name <name>" not in container_management


def test_deploy_copies_postgresql_operational_preflight() -> None:
    workflow = DEPLOY_WORKFLOW.read_text()

    assert POSTGRES_PREFLIGHT.exists()
    assert "apps/crawler/scripts/postgresql-operational-preflight.py" in workflow


def test_postgresql_archive_push_uses_shared_repository_lock() -> None:
    expected = "flock -s /var/spool/pgbackrest/repository.lock pgbackrest"

    assert expected in (REPO_ROOT / "deploy/backups/postgresql/migrate-container.sh").read_text()
    assert expected in (REPO_ROOT / "deploy/networking/harden-postgresql.sh").read_text()


def test_deploy_sources_pull_helpers_and_workflow_copies_them() -> None:
    script = DEPLOY_SH.read_text()
    workflow = DEPLOY_WORKFLOW.read_text()

    source = script.index('source "$INCOMING_DIR/deploy_helpers.sh"')
    pull = script.index("\npull_deploy_images\n")

    assert source < pull
    assert "apps/crawler/deploy_helpers.sh" in workflow
    assert DEPLOY_HELPERS_SH.exists()


def test_deploy_oneoff_guard_uses_compose_labels_and_reports_context() -> None:
    script = DEPLOY_SH.read_text()

    assert 'COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-$(basename "$DEPLOY_DIR")}"' in script
    assert "export COMPOSE_PROJECT_NAME" in script
    assert "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME}" in script
    assert "label=com.docker.compose.oneoff=True" in script
    assert "Container ID\\tName\\tImage\\tStatus\\tCompose service\\tCommand" in script
    assert "Wait for the one-off job to finish" in script


def test_deploy_blocks_named_typesense_maintenance_containers() -> None:
    script = DEPLOY_SH.read_text()

    assert "name=^/crawler-(backfill|refresh)-typesense-" in script
    assert "inline crawler sync also refreshes Typesense" in script
    assert "Wait for the maintenance job to finish" in script


def test_deploy_disk_preflight_prunes_only_safe_reclaimable_docker_state() -> None:
    script = DEPLOY_SH.read_text()

    assert "docker builder prune -af" in script
    assert "docker image prune -af" in script
    assert "DEPLOY_MIN_FREE_KB" in script
    assert "df -Pk" in script
    assert "docker system prune" not in script
    assert "docker volume prune" not in script

    preflight = script[
        script.index("ensure_deploy_disk_headroom() {") : script.index("resolve_shim_image_ref() {")
    ]
    assert preflight.index("docker builder prune -af") < preflight.index("docker image prune -af")
    assert script.rstrip().splitlines()[-2] == "docker image prune -f || true"


def test_crawler_image_stays_on_python_313_for_fasttext_wheels() -> None:
    dockerfile = DOCKERFILE.read_text()
    dockerignore = DOCKERIGNORE.read_text().splitlines()

    assert "FROM python:3.13.15-slim-trixie@sha256:" in dockerfile
    assert "python:3.14" not in dockerfile
    assert "ghcr.io/astral-sh/uv:0.12.3@sha256:" in dockerfile
    assert "ghcr.io/astral-sh/uv:latest" not in dockerfile
    assert "COPY scripts/with-xvfb.sh /usr/local/bin/with-xvfb" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/with-xvfb"]' in dockerfile
    assert "scripts/*" in dockerignore
    assert "!scripts/with-xvfb.sh" in dockerignore
    assert "scripts/" not in dockerignore
    assert XVFB_ENTRYPOINT.is_file()


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _install_release_verifier_tools(binary_dir: Path) -> None:
    _write_executable(
        binary_dir / "sha256sum",
        "#!/usr/bin/env python3\n"
        "from pathlib import Path\n"
        "import hashlib\n"
        "import sys\n"
        "path = Path(sys.argv[-1])\n"
        "print(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}')\n",
    )
    _write_executable(
        binary_dir / "stat",
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import stat\n"
        "import sys\n"
        "assert sys.argv[1:3] == ['-c', '%a'], sys.argv\n"
        "print(f'{stat.S_IMODE(os.lstat(sys.argv[3]).st_mode):o}')\n",
    )


def test_xvfb_entrypoint_cleans_stale_display_artifacts_on_restart(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    socket_dir = runtime / ".X11-unix"
    socket_dir.mkdir(parents=True)
    # Docker gives the restarted container a fresh PID namespace, so a stale
    # Xvfb PID can be reused by an unrelated live process. That must not make
    # the stale lock permanent.
    (runtime / ".X99-lock").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (socket_dir / "X99").write_text("stale", encoding="utf-8")

    binaries = tmp_path / "bin"
    binaries.mkdir()
    _write_executable(
        binaries / "xdpyinfo",
        '#!/bin/sh\ntest -f "$XVFB_RUNTIME_DIR/.display-ready"\n',
    )
    _write_executable(
        binaries / "Xvfb",
        '#!/bin/sh\ntouch "$XVFB_RUNTIME_DIR/.display-ready"\nsleep 0.5\n',
    )
    target = tmp_path / "target"
    _write_executable(
        target,
        "#!/bin/sh\n"
        'test ! -e "$XVFB_RUNTIME_DIR/.X99-lock"\n'
        'test ! -e "$XVFB_RUNTIME_DIR/.X11-unix/X99"\n'
        'test "$DISPLAY" = :99\n'
        "echo target-started\n",
    )

    result = subprocess.run(
        ["/bin/sh", str(XVFB_ENTRYPOINT), str(target)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "XVFB_RUNTIME_DIR": str(runtime),
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "target-started"


def test_xvfb_entrypoint_keeps_artifacts_when_display_is_live(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    socket_dir = runtime / ".X11-unix"
    socket_dir.mkdir(parents=True)
    lock = runtime / ".X99-lock"
    socket = socket_dir / "X99"
    lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
    socket.write_text("live", encoding="utf-8")

    binaries = tmp_path / "bin"
    binaries.mkdir()
    _write_executable(binaries / "xdpyinfo", "#!/bin/sh\nexit 0\n")

    result = subprocess.run(
        ["/bin/sh", str(XVFB_ENTRYPOINT), "/bin/true"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
        env={
            **os.environ,
            "PATH": f"{binaries}:{os.environ['PATH']}",
            "XVFB_RUNTIME_DIR": str(runtime),
        },
    )

    assert result.returncode != 0
    assert "display 99 is already active" in result.stderr
    assert lock.exists()
    assert socket.exists()


def test_alloy_uses_explicit_persistent_storage_path() -> None:
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    alloy = compose["services"]["alloy"]

    assert "alloy-data:/data-alloy" in alloy["volumes"]
    assert "--storage.path=/data-alloy" in alloy["command"]
    assert compose["volumes"]["alloy-data"]["external"] is True
    assert compose["volumes"]["alloy-data"]["name"] == "${COMPOSE_PROJECT_NAME}_alloy-data"


def test_alloy_state_migrates_before_compose_can_recreate_it() -> None:
    script = DEPLOY_SH.read_text()

    migration = script.index("\nprepare_alloy_state_volume\n")
    first_activation = script.index("docker compose up -d --force-recreate alloy", migration)
    stack_start = script.index("docker compose up -d --remove-orphans", first_activation)
    forced_recreate = script.index("docker compose up -d --force-recreate alloy", stack_start)

    assert migration < first_activation < stack_start < forced_recreate
    assert 'docker stop --time=30 "$alloy_container"' in script
    assert 'docker cp "${alloy_container}:/data-alloy/." "$staging/"' in script
    assert ".jobseek-persistent-state" in script
    assert 'normalize_alloy_state_volume "$volume_name"' in script
    assert "chown -R 0:0 /data-alloy && chmod 0700 /data-alloy" in script
    assert "grafana/alloy:latest" not in script
    assert "http://127.0.0.1:12346/-/ready" in script
