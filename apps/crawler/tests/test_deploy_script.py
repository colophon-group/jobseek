from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import yaml

DEPLOY_SH = Path(__file__).resolve().parent.parent / "deploy.sh"
DEPLOY_HELPERS_SH = Path(__file__).resolve().parent.parent / "deploy_helpers.sh"
DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"
DOCKERIGNORE = Path(__file__).resolve().parent.parent / ".dockerignore"
XVFB_ENTRYPOINT = Path(__file__).resolve().parent.parent / "scripts" / "with-xvfb.sh"
COMPOSE_FILE = Path(__file__).resolve().parent.parent / "docker-compose.yml"
DEPLOY_WORKFLOW = (
    Path(__file__).resolve().parents[3] / ".github/workflows/deploy-crawler-browser.yml"
)
SYNC_DATA_WORKFLOW = Path(__file__).resolve().parents[3] / ".github/workflows/sync-data.yml"
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


def test_deploy_quiesces_writers_before_migrations_and_schema_sync() -> None:
    script = DEPLOY_SH.read_text()

    quiesce = script.index("docker compose stop --timeout 60")
    migrate = script.index("alembic -c src/migrations/alembic.ini upgrade head")
    typesense_schema = script.index("uv run --no-sync crawler setup-typesense")
    sync = script.index("uv run --no-sync crawler sync")

    assert quiesce < migrate < typesense_schema < sync


def test_operational_sync_entrypoints_are_local_and_typesense_only() -> None:
    script = DEPLOY_SH.read_text()
    sync_workflow = SYNC_DATA_WORKFLOW.read_text()

    assert "uv run --no-sync crawler sync\n" in script
    assert "uv run --no-sync crawler sync\n" in sync_workflow
    assert "--legacy-mirror" not in script
    assert "--legacy-mirror" not in sync_workflow


def test_production_env_omits_crawler_mirror_and_scopes_web_database() -> None:
    script = DEPLOY_SH.read_text()
    workflow = DEPLOY_WORKFLOW.read_text()
    compose = yaml.safe_load(COMPOSE_FILE.read_text())
    common_env = compose["x-common-env"]

    assert re.search(r"^DATABASE_URL=", script, re.MULTILINE) is None
    assert "DATABASE_URL_UNPOOLED" not in script
    assert "WEB_DATABASE_URL=${WEB_DATABASE_URL}" in script
    assert "WEB_DATABASE_URL: ${{ secrets.DATABASE_URL_UNPOOLED }}" in workflow
    assert "DATABASE_URL" not in common_env
    assert "WEB_DATABASE_URL" not in common_env
    for service in ("worker-1", "worker-2", "worker-3", "browser-1", "exporter", "drain"):
        environment = compose["services"][service]["environment"]
        assert "DATABASE_URL" not in environment
        assert "WEB_DATABASE_URL" not in environment

    # Migration/schema one-offs receive no web-owned credential. Only the
    # explicit registry/watchlist sync invocation is allowlisted for it.
    assert '--env-file "$ENV_FILE"' not in script
    assert script.count("-e WEB_DATABASE_URL") == 1


def test_csv_sync_filters_the_host_environment_to_required_boundaries() -> None:
    workflow = SYNC_DATA_WORKFLOW.read_text()

    assert "mktemp /run/lock/jobseek-csv-sync-env.XXXXXX" in workflow
    assert "chmod 0600" in workflow
    assert '--env-file "$RUNTIME_ENV"' in workflow
    assert "--env-file /home/deploy/.env" not in workflow
    assert re.search(r"\bDATABASE_URL\b", workflow) is None
    for key in (
        "LOCAL_DATABASE_URL",
        "WEB_DATABASE_URL",
        "TYPESENSE_HOST",
        "TYPESENSE_PORT",
        "TYPESENSE_PROTOCOL",
        "TYPESENSE_OPERATIONS_KEY",
    ):
        assert key in workflow


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
    rollback = script[
        script.index("rollback_deploy() {") : script.index("compose_service_ready() {")
    ]

    assert "docker-compose.yml" in script.partition("DEPLOY_SPEC_FILES=(")[2].partition(")")[0]
    assert 'tar -C "$snapshot_dir" -cpf' in script
    assert 'tar -C "$DEPLOY_DIR" -xpf "$ROLLBACK_SPEC_ARCHIVE"' in script
    assert 'compose_source="$ACTIVE_COMPOSE_SNAPSHOT"' in script
    assert 'compose_source="$BOOTSTRAP_ROLLBACK_COMPOSE"' in script
    assert 'git show "${BEFORE_SHA}:apps/crawler/docker-compose.yml"' in workflow
    assert "apps/crawler/docker-compose.rollback.yml" in workflow
    assert 'mv "$active_compose_temporary" "$ACTIVE_COMPOSE_SNAPSHOT"' in script
    env_restore = rollback.index('mv "$ROLLBACK_ENV_FILE" "$ENV_FILE"')
    spec_restore = rollback.index("restore_previous_deploy_specs")
    old_stack_start = rollback.index("docker compose up -d --remove-orphans")
    assert env_restore < spec_restore < old_stack_start
    assert "target: /home/deploy/incoming/" in workflow
    assert "script: bash /home/deploy/incoming/deploy.sh" in workflow
    assert "target: /home/deploy/\n" not in workflow


def test_deploy_requires_exact_reconciliation_wrapper_before_activation() -> None:
    script = DEPLOY_SH.read_text()
    workflow = DEPLOY_WORKFLOW.read_text()

    guard = script.index("\nensure_reconciliation_wrapper_compatible\n")
    snapshot = script.index("\nsnapshot_active_deploy_specs\n")
    activation = script.index("\nactivate_staged_deploy_specs\n")
    env_write = script.index('cat > "$ENV_FILE"')
    assert guard < snapshot < activation < env_write
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

    wait = workflow.index("- name: Wait for same-revision murmur-shim deployment")
    host_copy = workflow.index("- name: Copy deploy files")
    assert wait < host_copy
    assert "actions: read" in workflow
    assert "actions/workflows/deploy-murmur-shim.yml/runs" in workflow
    assert '-f head_sha="$GITHUB_SHA"' in workflow
    assert "deadline=$((SECONDS + 2700))" in workflow
    assert "same-revision murmur-shim workflow concluded" in workflow
    assert "timed out waiting for same-revision murmur-shim deployment" in workflow


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


def test_deploy_disk_preflight_only_prunes_builder_cache() -> None:
    script = DEPLOY_SH.read_text()

    assert "docker builder prune -af" in script
    assert "DEPLOY_MIN_FREE_KB" in script
    assert "df -Pk" in script
    assert "docker system prune" not in script
    assert "docker volume prune" not in script


def test_crawler_image_stays_on_python_313_for_fasttext_wheels() -> None:
    dockerfile = DOCKERFILE.read_text()
    dockerignore = DOCKERIGNORE.read_text().splitlines()

    assert "FROM python:3.13-slim AS base" in dockerfile
    assert "python:3.14" not in dockerfile
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
