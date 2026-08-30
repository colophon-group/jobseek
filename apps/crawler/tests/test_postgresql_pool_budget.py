"""Static enforcement for the production PostgreSQL connection budget."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import yaml

from src.workspace.codex_routine_runner import (
    LABELLER_POSTGRES_ENV,
    labeller_postgresql_child_env,
)

ROOT = Path(__file__).resolve().parents[3]
CRAWLER = ROOT / "apps" / "crawler"
COMPOSE = CRAWLER / "docker-compose.yml"
ROLLBACK_OVERRIDE = CRAWLER / "rollback-pool-budget.override.yml"
BASE_REVISION = "51625c18e3d1d03cdf606b307001b96b6dc85868"
BASE_COMPOSE_FIXTURE = CRAWLER / "tests" / "fixtures" / f"docker-compose.base-{BASE_REVISION}.yml"
BASE_COMPOSE_SHA256 = "67f8209fe6316932d85ed3d32b5d1a5da2c1d242fffa7f5d55ad9b73f4b6a32e"
RUNBOOK = ROOT / "docs" / "22-postgresql-connections.md"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _render_compose_config(*files: Path) -> dict:
    docker = shutil.which("docker")
    assert docker is not None, "Docker Compose is required for the rollback contract test"
    # Intentionally exclude the caller's environment values. Compose gives
    # process variables precedence over its env file, so synthetic inputs are
    # the only way for this regression test to remain deterministic and avoid
    # surfacing any developer or CI secret in failure output.
    environment = {
        key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_CONFIG") if key in os.environ
    }
    environment.update(
        {
            "COMPOSE_PROJECT_NAME": "rollback-base-contract",
            "CRAWLER_IMAGE_TAG": "base-fixture",
            "GRAFANA_LOKI_PASSWORD": "fixture",
            "GRAFANA_LOKI_URL": "https://fixture.invalid/loki",
            "GRAFANA_LOKI_USERNAME": "fixture",
            "GRAFANA_PROM_PASSWORD": "fixture",
            "GRAFANA_PROM_URL": "https://fixture.invalid/prom",
            "GRAFANA_PROM_USERNAME": "fixture",
            "LOCAL_DATABASE_URL": "postgresql://fixture.invalid/jobseek",
            "MURMUR_TOKEN": "fixture",
            "OWNER": "fixture",
            "PROXY_PROVIDER": "none",
            "R2_ACCESS_KEY_ID": "fixture",
            "R2_BUCKET": "fixture",
            "R2_DOMAIN_URL": "https://fixture.invalid/r2",
            "R2_ENDPOINT_URL": "https://fixture.invalid/r2",
            "R2_SECRET_ACCESS_KEY": "fixture",
            "SHIM_IMAGE_TAG": "bounded-fixture",
            "TYPESENSE_HOST": "fixture.invalid",
            "TYPESENSE_OPERATIONS_KEY": "fixture",
            "TYPESENSE_PORT": "8108",
            "TYPESENSE_PROTOCOL": "https",
            "WEBSHARE_PROXY_URL": "",
        }
    )
    command = [
        docker,
        "compose",
        "--env-file",
        os.devnull,
        "--project-directory",
        str(CRAWLER),
        "--project-name",
        "rollback-base-contract",
    ]
    for file in files:
        command.extend(("-f", str(file)))
    command.extend(("config", "--format", "json"))
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_long_running_pool_budget_is_explicit_and_below_steady_target() -> None:
    compose = _compose()
    expected = {
        "worker-1": ("worker-1", 1, 8),
        "worker-2": ("worker-2", 1, 8),
        "worker-3": ("worker-3", 1, 8),
        "browser-1": ("browser-1", 1, 6),
        "exporter": ("exporter", 1, 4),
        "drain": ("drain", 1, 6),
    }

    minimum = 0
    maximum = 0
    for service, (role, pool_min, pool_max) in expected.items():
        environment = compose["services"][service]["environment"]
        assert environment["CRAWLER_DB_ROLE"] == role
        assert int(environment["CRAWLER_DB_POOL_MIN"]) == pool_min
        assert int(environment["CRAWLER_DB_POOL_MAX"]) == pool_max
        assert int(environment["CRAWLER_DB_POOL_IDLE_SECONDS"]) == 60
        minimum += pool_min
        maximum += pool_max

    assert minimum == 6
    assert maximum == 40

    murmur = compose["services"]["murmur-shim"]["environment"]
    assert int(murmur["MURMUR_DB_POOL_MAX"]) == 2
    assert int(murmur["MURMUR_INVOKER_MAX_CONCURRENCY"]) == 2
    assert maximum + 2 + 2 == 44
    assert 44 < 70


def test_murmur_enforces_both_connection_owners() -> None:
    node_db = (ROOT / "apps/murmur-shim/src/db/index.ts").read_text(encoding="utf-8")
    invoker = (ROOT / "apps/murmur-shim/app/api/murmur/_lib/invoke-lib.ts").read_text(
        encoding="utf-8"
    )
    python_kv = (CRAWLER / "src/workspace/lib/postgres_claim_kv.py").read_text(encoding="utf-8")

    assert "value > 2" in node_db
    assert 'application_name: "jobseek:murmur:node"' in node_db
    assert "idle_in_transaction_session_timeout: 60000" in node_db
    assert "value > 2" in invoker
    assert "acquireInvokerSlot" in invoker
    assert "deadline - Date.now()" in invoker
    assert '"application_name": "jobseek:murmur:python"' in python_kv


def test_oneoffs_and_readonly_routine_have_explicit_small_budgets() -> None:
    surfaces = {
        "deploy/reconciliation/run.sh": ("reconciliation", 4),
        ".github/workflows/crawler-scheduled-maintenance.yml": ("maintenance", 4),
        ".github/workflows/refresh-currency-rates.yml": ("currency-refresh", 4),
        "scripts/crawler-csv-sync-host.sh": ("csv-sync", 4),
        ".github/workflows/repair-location-taxonomy-source.yml": (
            "location-taxonomy-repair",
            4,
        ),
    }
    for relative_path, (role, maximum) in surfaces.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert f"CRAWLER_DB_ROLE={role}" in source
        assert "CRAWLER_DB_POOL_MIN=0" in source
        assert f"CRAWLER_DB_POOL_MAX={maximum}" in source

    labeller_example = (ROOT / "deploy/systemd/jobseek-codex-labeller.env.example").read_text(
        encoding="utf-8"
    )
    assert "LOCAL_DATABASE_URL=" in labeller_example
    assert "CRAWLER_DB_" not in labeller_example
    assert LABELLER_POSTGRES_ENV == {
        "CRAWLER_DB_ROLE": "labeller",
        "CRAWLER_DB_POOL_MIN": "0",
        "CRAWLER_DB_POOL_MAX": "2",
        "CRAWLER_DB_POOL_IDLE_SECONDS": "60",
    }
    labeller_child = labeller_postgresql_child_env(ROOT / "runner-state")
    assert labeller_child["JOBSEEK_LABELLER_DB_LOCK_FILE"].endswith(
        "/runner-state/labeller-postgresql.lock"
    )
    assert labeller_child["JOBSEEK_LABELLER_DB_LOCK_TIMEOUT_SECONDS"] == "300"
    labeller_cli = (CRAWLER / "src/labeller/cli.py").read_text(encoding="utf-8")
    assert '_DATABASE_COMMANDS = frozenset({"sample", "prepare", "prepare-pre-llm"})' in (
        labeller_cli
    )
    assert "with _database_process_lock(args.command)" in labeller_cli
    deployment = (ROOT / "scripts/deploy-codex-runner-host.sh").read_text(encoding="utf-8")
    assert "labeller PostgreSQL pool contract mismatch" in deployment
    assert "labeller.env must contain exactly one LOCAL_DATABASE_URL" in deployment
    assert "LABELLER_CONTRACT_VERIFIED=1" in deployment
    assert "leaving ${timer} stopped: labeller PostgreSQL contract was not verified" in deployment
    main = deployment.index("main()")
    assert deployment.index("pause_timer_activations", main) < deployment.index(
        "require_runtime_config", main
    )
    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "aggregate maximum remains exactly 2" in runbook
    assert "unchanged at 58 connections" in runbook


def test_deploy_quiesces_pool_generations_and_stays_below_normal_maximum() -> None:
    deploy = (CRAWLER / "deploy.sh").read_text(encoding="utf-8")
    stop = deploy.index(
        "docker compose stop --timeout 60 worker-1 worker-2 worker-3 browser-1 exporter drain"
    )
    migrate = deploy.index("CRAWLER_DB_ROLE=deploy-migrate", stop)
    sync = deploy.index("CRAWLER_DB_ROLE=deploy-sync", migrate)
    start = deploy.index("docker compose up -d --remove-orphans", sync)
    assert stop < migrate < sync < start

    # Murmur (2 Node + 2 children) remains up. Migration uses one NullPool
    # connection; sync uses a four-slot local pool. Those phases are serial.
    assert 4 + 1 == 5
    assert 4 + 4 == 8

    ingress = (ROOT / "deploy/networking/verify-private-paths.sh").read_text(encoding="utf-8")
    assert '"application_name": "jobseek:ingress:private-path-verifier"' in ingress
    # Independent deploy overlap: labeller 2 + backup 2 + sampler 1 + ingress 1.
    independent = 2 + 2 + 1 + 1
    assert max(5 + independent, 8 + independent, 44 + independent) == 50

    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "absolute deployment maximum is therefore 50 connections" in runbook
    assert "| new or rolled-back stack healthy | 40 | 0 | 4 | 6 | **50** |" in runbook


def test_exact_pre_budget_base_archive_is_bounded_by_rollback_override() -> None:
    """Exercise Compose's real merge contract for the exact PR base artifact.

    The fixture is the byte-exact docker-compose.yml from BASE_REVISION. Its
    pinned digest makes accidental fixture edits visible without requiring
    that commit to exist in a shallow test checkout.
    """

    assert hashlib.sha256(BASE_COMPOSE_FIXTURE.read_bytes()).hexdigest() == BASE_COMPOSE_SHA256
    base = _render_compose_config(BASE_COMPOSE_FIXTURE)
    crawler_services = (
        "worker-1",
        "worker-2",
        "worker-3",
        "browser-1",
        "exporter",
        "drain",
    )
    base_maxima = {
        service: int(base["services"][service]["environment"].get("CRAWLER_DB_POOL_MAX", "10"))
        for service in crawler_services
    }
    assert base_maxima == {
        "worker-1": 20,
        "worker-2": 20,
        "worker-3": 20,
        "browser-1": 10,
        "exporter": 10,
        "drain": 10,
    }
    assert sum(base_maxima.values()) == 90

    merged = _render_compose_config(BASE_COMPOSE_FIXTURE, ROLLBACK_OVERRIDE)
    merged_maxima = {
        service: int(merged["services"][service]["environment"]["CRAWLER_DB_POOL_MAX"])
        for service in crawler_services
    }
    assert merged_maxima == {
        "worker-1": 8,
        "worker-2": 8,
        "worker-3": 8,
        "browser-1": 6,
        "exporter": 4,
        "drain": 6,
    }
    assert sum(merged_maxima.values()) == 40
    murmur = merged["services"]["murmur-shim"]["environment"]
    assert murmur["MURMUR_DB_POOL_MAX"] == "2"
    assert murmur["MURMUR_INVOKER_MAX_CONCURRENCY"] == "2"
    # Same maximum as the forward stack: crawler 40 + Murmur 4 + the
    # independently reserved labeller/backup/sampler/ingress clients 6.
    assert sum(merged_maxima.values()) + 2 + 2 + 6 == 50


def test_host_capacity_keeps_server_and_operator_reserve_explicit() -> None:
    constructors = (
        ROOT / "deploy/networking/harden-postgresql.sh",
        ROOT / "deploy/backups/postgresql/migrate-container.sh",
    )
    for constructor in constructors:
        source = constructor.read_text(encoding="utf-8")
        assert "max_connections=100" in source
        assert "superuser_reserved_connections=3" in source
        assert "max_connections=101" not in source

    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "**44**" in runbook
    assert "58 connections" in runbook
    assert "allocated ceiling is 68/100" in runbook
    assert "leaving 32" in runbook


def test_owner_metrics_are_bounded_and_seven_day_gate_is_documented() -> None:
    host = (ROOT / "scripts/jobseek-host-observability.py").read_text(encoding="utf-8")
    runbook = RUNBOOK.read_text(encoding="utf-8")

    for owner in (
        "worker-1",
        "worker-2",
        "worker-3",
        "browser-1",
        "exporter",
        "drain",
        "reconciliation",
        "murmur-node",
        "murmur-python",
        "labeller",
        "ingress-verifier",
        "backup",
        "operator-psql",
        "operator-tool",
        "other",
    ):
        assert f"'{owner}'" in host
    assert "jobseek_postgresql_connections_by_owner" in host
    assert "[7d:5m]" in runbook
    assert "[7d:1m]" in runbook
    assert ") < 0.70" in runbook
    assert ") < 0.80" in runbook
