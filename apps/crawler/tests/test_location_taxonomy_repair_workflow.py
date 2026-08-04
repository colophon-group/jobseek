from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/repair-location-taxonomy-source.yml"
RUNBOOK = ROOT / "docs/20-supabase-free-downgrade.md"
COMPOSE = ROOT / "apps/crawler/docker-compose.yml"


def _job(workflow: str, name: str, next_name: str | None = None) -> str:
    start = workflow.index(f"  {name}:\n")
    end = workflow.index(f"  {next_name}:\n", start) if next_name else len(workflow)
    return workflow[start:end]


def test_dispatch_is_owner_main_revision_and_confirmation_bound_before_approval() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    preauthorize = _job(workflow, "preauthorize", "authorize")
    authorize = _job(workflow, "authorize", "repair")
    repair = _job(workflow, "repair")

    assert "workflow_dispatch:" in workflow
    assert "expected_crawler_revision:" in workflow
    assert "confirmation:" in workflow
    assert "environment:" not in preauthorize
    assert 'test "$DISPATCH_ACTOR" = viktor-shcherb' in preauthorize
    assert 'test "$DISPATCH_TRIGGERING_ACTOR" = viktor-shcherb' in preauthorize
    assert 'test "$DISPATCH_REF" = refs/heads/main' in preauthorize
    assert '[[ "$DISPATCH_SHA" =~ ^[0-9a-f]{40}$ ]]' in preauthorize
    assert 'test "$DISPATCH_SHA" = "$EXPECTED_CRAWLER_REVISION"' not in preauthorize
    assert "REPAIR-LOCAL-LOCATION-TAXONOMY-37526" in preauthorize
    assert "needs: preauthorize" in authorize
    assert "environment: production-migrations" in authorize
    assert 'test "$DISPATCH_ACTOR" = viktor-shcherb' in authorize
    assert 'test "$DISPATCH_TRIGGERING_ACTOR" = viktor-shcherb' in authorize
    assert 'test "$DISPATCH_REF" = refs/heads/main' in authorize
    assert "REPAIR-LOCAL-LOCATION-TAXONOMY-37526" in authorize
    assert 'echo "crawler_revision=${EXPECTED_CRAWLER_REVISION}"' in authorize
    assert "needs: [preauthorize, authorize]" in repair
    assert "needs.authorize.result == 'success'" in repair
    assert "environment: Production" in repair
    assert "github.triggering_actor == 'viktor-shcherb'" in repair


def test_authorize_allows_current_main_after_the_deployed_revision() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    authorize = _job(workflow, "authorize", "repair")

    assert "fetch-depth: 0" in authorize
    assert 'test "$(git rev-parse HEAD)" = "$DISPATCH_SHA"' in authorize
    assert 'test "$DISPATCH_SHA" = "$EXPECTED_CRAWLER_REVISION"' not in authorize
    assert (
        'version="$(git show "${EXPECTED_CRAWLER_REVISION}:apps/crawler/VERSION" '
        "| tr -d '[:space:]')\""
    ) in authorize
    assert 'echo "crawler_revision=${EXPECTED_CRAWLER_REVISION}"' in authorize
    assert 'echo "image_tag=v${version}"' in authorize


def test_authorize_rejects_a_non_ancestor_target_revision() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    authorize = _job(workflow, "authorize", "repair")

    object_check = 'git cat-file -e "${EXPECTED_CRAWLER_REVISION}^{commit}"'
    ancestor_check = 'git merge-base --is-ancestor "$EXPECTED_CRAWLER_REVISION" "$DISPATCH_SHA"'
    assert "set -euo pipefail" in authorize
    assert object_check in authorize
    assert ancestor_check in authorize
    assert authorize.index(object_check) < authorize.index(ancestor_check)
    assert f"{ancestor_check} ||" not in authorize


def test_repair_secrets_resolve_only_after_protected_authorization() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    preauthorize = _job(workflow, "preauthorize", "authorize")
    authorize = _job(workflow, "authorize", "repair")
    repair = _job(workflow, "repair")

    assert "secrets." not in preauthorize
    assert "secrets." not in authorize
    assert workflow.count("environment: production-migrations") == 1
    assert workflow.count("environment: Production") == 1
    assert "EXPECTED_CRAWLER_REVISION: ${{ needs.authorize.outputs.crawler_revision }}" in repair
    assert "EXPECTED_IMAGE_TAG: ${{ needs.authorize.outputs.image_tag }}" in repair
    assert "${{ inputs.expected_crawler_revision }}" not in repair
    for secret in (
        "GRAFANA_PROM_URL",
        "GRAFANA_PROM_USERNAME",
        "GRAFANA_PROM_PASSWORD",
        "HETZNER_HOST",
        "HETZNER_SSH_KEY",
    ):
        assert f"${{{{ secrets.{secret} }}}}" in repair


def test_operation_attests_exact_deployment_image_and_credentials_without_exposing_values() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    repair = _job(workflow, "repair")

    assert "JOBSEEK_DEPLOY_REVISION" in repair
    assert "CRAWLER_IMAGE_TAG" in repair
    assert 'test "$active_revision" = "$EXPECTED_CRAWLER_REVISION"' in repair
    assert 'test "$active_tag" = "$EXPECTED_IMAGE_TAG"' in repair
    assert 'test "$owner" = colophon-group' in repair
    assert "Expected exactly one live exporter container" in repair
    assert "Live exporter has a forbidden database credential" in repair
    assert "LOCAL_DATABASE_URL" in repair
    assert "WEB_DATABASE_URL" in repair
    assert "DATABASE_URL_UNPOOLED" in repair
    assert 'test "$(wc -l < "$RUNTIME_ENV")" -eq 2' in repair
    assert "postgres(ql)?://|password=" in repair
    assert "Repair output violated the nonsecret evidence contract" in repair
    assert "${{ secrets.DATABASE_URL" not in workflow


def test_live_exporter_allows_only_its_required_local_database_boundary() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    repair = _job(workflow, "repair")
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    exporter_environment = compose["services"]["exporter"]["environment"]

    assert "LOCAL_DATABASE_URL" in exporter_environment
    assert "DATABASE_URL" not in exporter_environment
    assert "DATABASE_URL_UNPOOLED" not in exporter_environment
    assert "WEB_DATABASE_URL" not in exporter_environment

    guard_start = repair.index(
        "if docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}'"
    )
    guard_end = repair.index("            fi", guard_start)
    credential_guard = repair[guard_start:guard_end]
    assert "^(DATABASE_URL|DATABASE_URL_UNPOOLED|WEB_DATABASE_URL)$" in credential_guard
    assert "LOCAL_DATABASE_URL" not in credential_guard
    assert "Live exporter has a forbidden database credential" in credential_guard


def test_repair_is_one_bounded_mutation_locked_transactional_command() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    repair = _job(workflow, "repair")

    lock = repair.index("exec 9>/run/lock/jobseek-crawler-mutation.lock")
    preflight = repair.index("postgresql-operational-preflight.py", lock)
    operation = repair.index("crawler repair-location-taxonomy-source", preflight)
    evidence = repair.index("grep -Fq '\"source_local_equal\": true'", operation)
    assert lock < preflight < operation < evidence
    assert "timeout --foreground --signal=TERM --kill-after=30s 15m" in repair
    assert "jobseek.maintenance.issue=6282" in repair
    assert "jobseek.maintenance.budget-seconds=900" in repair
    assert "${#evidence} > 4096" in repair
    for required in (
        '"expected_rows": 37526',
        '"source_rows": 37526',
        '"local_rows": 37526',
        '"constraint_validated": true',
    ):
        assert required in repair


def test_actions_are_commit_pinned_and_runbook_preserves_rollout_order() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    for action in ("actions/checkout", "appleboy/scp-action", "appleboy/ssh-action"):
        matching = [line for line in workflow.splitlines() if f"uses: {action}@" in line]
        assert matching
        assert all("@v" not in line for line in matching)

    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "REPAIR-LOCAL-LOCATION-TAXONOMY-37526" in runbook
    assert "Do not combine this repair with the #6256 producer" in runbook
    assert runbook.index("deploy #6256") < runbook.index("only then permit #6258")
