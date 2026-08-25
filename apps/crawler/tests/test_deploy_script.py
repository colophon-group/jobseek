from __future__ import annotations

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

    assert "mktemp /run/lock/jobseek-csv-sync-env.XXXXXX" in sync_host
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
    assert "--kind data" in workflow
    assert "target_data_contract" in workflow
    assert "current_data_contract" in workflow
    assert "requested CSV snapshot is stale relative to current main" in workflow
    assert "current main CSV snapshot advanced before publication" in workflow
    assert "before_contract" in workflow
    assert "run_sync=false" in workflow
    assert "SYNC_RUNTIME_CONTRACT_SHA256" in workflow
    assert "--check-runtime" in workflow
    assert 'if [[ "$status" -eq 75 ]]' in workflow
    assert workflow.index("--check-runtime") < workflow.index(
        "/usr/local/sbin/jobseek-maintenance window"
    )
    assert "JOBSEEK_RUNTIME_CONTRACT_SHA256" in deploy
    assert "JOBSEEK_RUNTIME_CONTRACT_SHA256" in sync_host
    assert "/home/deploy/.crawler-active-release" in sync_host
    contract_gate = sync_host.index("contract_files=(")
    credentials = sync_host.index("required_env=(")
    image = sync_host.index("mapfile -t image_refs")
    publication = sync_host.index("uv run --no-sync crawler sync")
    assert contract_gate < credentials < image < publication
    assert sync_host.count('"$active_release/environment.env"') == 1
    assert sync_host.count('"$active_release/success.env"') == 1
    assert "return 75" in sync_host
    assert sync_host.index("verify_runtime_contract") < sync_host.index('RUNTIME_ENV="$(mktemp')


def test_csv_sync_runtime_contract_mismatch_is_retryable_but_corruption_is_fatal(
    tmp_path: Path,
) -> None:
    sync_host = CSV_SYNC_HOST.read_text()
    verifier = sync_host[
        sync_host.index("verify_runtime_contract() {") : sync_host.index(
            "\nverify_runtime_contract\n"
        )
    ]
    expected = "a" * 64
    different = "b" * 64
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
    harness = "\n".join(
        (
            "set -u",
            'DEPLOY_ENV="$TEST_DEPLOY_ENV"',
            'ACTIVE_RELEASE_POINTER="$TEST_ACTIVE_RELEASE_POINTER"',
            'ACTIVE_RELEASE_ROOT="$TEST_ACTIVE_RELEASE_ROOT"',
            f'RUNTIME_CONTRACT_SHA256="{expected}"',
            verifier,
            "verify_runtime_contract",
            "status=$?",
            "printf '%s\\n' \"$status\"",
        )
    )

    for name, values, expected_status in (
        ("match", (expected, expected, expected), 0),
        ("different", (expected, expected, different), 75),
        ("missing", (expected, expected, None), 75),
        ("invalid", (expected, expected, "not-a-digest"), 1),
    ):
        case_dir = tmp_path / name
        release_root = case_dir / "releases"
        release = release_root / "release-1"
        release.mkdir(parents=True)
        deploy_env = case_dir / ".env"
        pointer = case_dir / ".crawler-active-release"
        pointer.symlink_to(release)
        files = (deploy_env, release / "environment.env", release / "success.env")
        for path, value in zip(files, values, strict=True):
            path.write_text(
                "" if value is None else f"JOBSEEK_RUNTIME_CONTRACT_SHA256={value}\n",
                encoding="utf-8",
            )

        result = subprocess.run(
            [bash, "-c", harness],
            check=False,
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "TEST_ACTIVE_RELEASE_POINTER": str(pointer),
                "TEST_ACTIVE_RELEASE_ROOT": str(release_root),
                "TEST_DEPLOY_ENV": str(deploy_env),
            },
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(expected_status)
        assert ("WAIT:" in result.stderr) == (expected_status == 75)
        assert ("ERROR:" in result.stderr) == (expected_status == 1)


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
    env_restore = rollback.index('mv "$ROLLBACK_ENV_FILE" "$ENV_FILE"')
    spec_restore = rollback.index("restore_previous_deploy_specs")
    contract = rollback.index("configure_rollback_compose_contract")
    config_sync = rollback.index("rollback_sync_previous_config")
    old_stack_start = rollback.index("rollback_compose up -d --remove-orphans")
    health = rollback.index("wait_for_rollback_core_services")
    assert (
        quiesce
        < release_restore
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
            "verify_optional_runtime_contract_pair() {"
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
    bash = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"
    harness = "\n".join(
        (
            "set -u",
            'OWNER="colophon-group"',
            'ENV_FILE="$TEST_ENV_FILE"',
            'ROLLBACK_SYNC_WEB_DATABASE_URL=""',
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
            },
        )
        assert result.returncode == 0, result.stderr
        calls = log.read_text(encoding="utf-8").splitlines()
        assert calls[0] == (
            "run --rm --no-deps -e WEB_DATABASE_URL "
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
    publish = script.index('publish_active_deploy_release "$deploy_success_temporary"')
    staged_identity = script.index('verify_shim_deploy_contract "$deploy_success_temporary"')
    committed_identity = script.index('verify_shim_deploy_contract "$DEPLOY_SUCCESS_FILE"')

    assert health < prepare < staged_identity < publish < committed_identity < disarm
    assert '"CRAWLER_IMAGE_REF=$CRAWLER_IMAGE_REF"' in script
    assert '"BROWSER_IMAGE_REF=$BROWSER_IMAGE_REF"' in script
    assert '"SHIM_IMAGE_REF=$SHIM_IMAGE_REF"' in script
    assert '"JOBSEEK_RUNTIME_CONTRACT_SHA256=$JOBSEEK_RUNTIME_CONTRACT_SHA256"' in script
    assert "verify_optional_runtime_contract_pair" in script
    assert (
        "JOBSEEK_RUNTIME_CONTRACT_SHA256: ${{ needs.build.outputs.runtime_contract_sha256 }}"
        in (workflow)
    )
    assert "JOBSEEK_RUNTIME_CONTRACT_SHA256=//p" in murmur_workflow
    assert "${runtime_contracts[0]}" in murmur_workflow
    assert 'ACTIVE_RELEASE_POINTER="$DEPLOY_DIR/.crawler-active-release"' in script
    assert "RELEASE_FORMAT_VERSION=1" in script
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
