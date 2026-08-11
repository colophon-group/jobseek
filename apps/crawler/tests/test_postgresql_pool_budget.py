"""Static enforcement for the production PostgreSQL connection budget."""

from __future__ import annotations

from pathlib import Path

import yaml

from src.workspace.codex_routine_runner import (
    LABELLER_POSTGRES_ENV,
    labeller_postgresql_child_env,
)

ROOT = Path(__file__).resolve().parents[3]
CRAWLER = ROOT / "apps" / "crawler"
COMPOSE = CRAWLER / "docker-compose.yml"
RUNBOOK = ROOT / "docs" / "22-postgresql-connections.md"


def _compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


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
    assert "value > 2" in invoker
    assert "acquireInvokerSlot" in invoker
    assert '"application_name": "jobseek:murmur:python"' in python_kv


def test_oneoffs_and_readonly_routine_have_explicit_small_budgets() -> None:
    surfaces = {
        "deploy/reconciliation/run.sh": ("reconciliation", 4),
        ".github/workflows/crawler-scheduled-maintenance.yml": ("maintenance", 4),
        ".github/workflows/refresh-currency-rates.yml": ("currency-refresh", 4),
        ".github/workflows/sync-data.yml": ("csv-sync", 4),
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
