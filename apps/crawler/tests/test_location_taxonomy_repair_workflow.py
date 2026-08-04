from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github/workflows/repair-location-taxonomy-source.yml"
RUNBOOK = ROOT / "docs/20-supabase-free-downgrade.md"


def _job(workflow: str, name: str, next_name: str | None = None) -> str:
    start = workflow.index(f"  {name}:\n")
    end = workflow.index(f"  {next_name}:\n", start) if next_name else len(workflow)
    return workflow[start:end]


def test_dispatch_is_owner_main_revision_and_confirmation_bound_before_approval() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    preauthorize = _job(workflow, "preauthorize", "repair")
    repair = _job(workflow, "repair")

    assert "workflow_dispatch:" in workflow
    assert "expected_crawler_revision:" in workflow
    assert "confirmation:" in workflow
    assert "environment:" not in preauthorize
    assert 'test "$DISPATCH_ACTOR" = viktor-shcherb' in preauthorize
    assert 'test "$DISPATCH_TRIGGERING_ACTOR" = viktor-shcherb' in preauthorize
    assert 'test "$DISPATCH_REF" = refs/heads/main' in preauthorize
    assert 'test "$DISPATCH_SHA" = "$EXPECTED_CRAWLER_REVISION"' in preauthorize
    assert "REPAIR-LOCAL-LOCATION-TAXONOMY-37526" in preauthorize
    assert "needs: preauthorize" in repair
    assert "environment: production-migrations" in repair
    assert "github.triggering_actor == 'viktor-shcherb'" in repair


def test_operation_attests_exact_deployment_image_and_credentials_without_exposing_values() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    repair = _job(workflow, "repair")

    assert "JOBSEEK_DEPLOY_REVISION" in repair
    assert "CRAWLER_IMAGE_TAG" in repair
    assert 'test "$active_revision" = "$EXPECTED_CRAWLER_REVISION"' in repair
    assert 'test "$active_tag" = "$EXPECTED_IMAGE_TAG"' in repair
    assert 'test "$owner" = colophon-group' in repair
    assert "Expected exactly one live exporter container" in repair
    assert "Live exporter has a relational database credential" in repair
    assert "LOCAL_DATABASE_URL" in repair
    assert "WEB_DATABASE_URL" in repair
    assert "DATABASE_URL_UNPOOLED" in repair
    assert 'test "$(wc -l < "$RUNTIME_ENV")" -eq 2' in repair
    assert "postgres(ql)?://|password=" in repair
    assert "Repair output violated the nonsecret evidence contract" in repair
    assert "${{ secrets.DATABASE_URL" not in workflow


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
