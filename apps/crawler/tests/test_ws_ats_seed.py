"""Inventory-seeded ``ws`` fast-path tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.ats_inventory.candidates import Candidate, CandidatePlan, render_candidate_issue
from src.ats_inventory.models import CompanyImpact
from src.workspace.ats_seed import (
    INVENTORY_CONFIG_NAME,
    InventorySeedInvalid,
    apply_inventory_seed,
    available_inventory_board_alias,
    current_registry_hard_evidence,
    issue_has_inventory_label,
    parse_inventory_seed,
    preverify_inventory_context,
)
from src.workspace.state import Workspace
from src.workspace.workflow import render_parallel_prompt


def _candidate_body(
    *,
    family: str = "greenhouse",
    url: str = "https://boards.greenhouse.io/acme",
) -> tuple[Candidate, str]:
    impact = CompanyImpact(
        ats=family,
        name="Acme",
        slug="acme",
        url=url,
        impact_unknown=False,
        active_jobs=37,
        remote_jobs=5,
        location_count=8,
        country_codes=("CH", "US"),
        latest_posted_at="2026-08-01T00:00:00Z",
    )
    candidate = Candidate.from_impact(impact)
    _, body = render_candidate_issue(CandidatePlan(candidate, (), ()))
    return candidate, body


def test_valid_inventory_issue_seeds_native_monitor_and_scraper() -> None:
    candidate, body = _candidate_body()

    seed = parse_inventory_seed(body)

    assert seed is not None
    assert seed.source_key == candidate.source_key
    assert seed.monitor_type == "greenhouse"
    assert seed.published_active_jobs == 37

    workspace = Workspace(slug="acme", issue=123)
    board = apply_inventory_seed(workspace, seed)
    assert workspace.active_board == "careers"
    assert workspace.ats_inventory["status"] == "pending"
    assert workspace.ats_inventory["source_key"] == candidate.source_key
    assert board.url == candidate.board_url
    assert board.active_config == INVENTORY_CONFIG_NAME
    assert board.configs[INVENTORY_CONFIG_NAME]["monitor_type"] == "greenhouse"
    assert board.configs[INVENTORY_CONFIG_NAME]["scraper_type"] == "skip"
    assert board.configs[INVENTORY_CONFIG_NAME]["status"] == "selected"


def test_inventory_seed_alias_never_replaces_an_existing_board() -> None:
    assert available_inventory_board_alias([]) == "careers"
    assert available_inventory_board_alias(["careers"]) == "inventory-careers"
    assert (
        available_inventory_board_alias(["careers", "inventory-careers"]) == "inventory-careers-2"
    )


def test_generic_family_uses_jobseek_owned_shared_monitor_preset() -> None:
    _, body = _candidate_body(
        family="teamtailor",
        url="https://acme.teamtailor.com/jobs",
    )

    seed = parse_inventory_seed(body)

    assert seed is not None
    assert seed.native_ats == "family:teamtailor"
    assert seed.monitor_type == "rss"
    assert seed.monitor_config == {"preset": "teamtailor"}


def test_stale_or_rewritten_board_url_rejects_fast_path() -> None:
    _, body = _candidate_body()
    tampered = body.replace(
        "Normalized board URL: `https://boards.greenhouse.io/acme`",
        "Normalized board URL: `https://boards.greenhouse.io/other`",
    )

    with pytest.raises(InventorySeedInvalid, match="marker hash"):
        parse_inventory_seed(tampered)


def test_wrong_tenant_rejects_fast_path() -> None:
    _, body = _candidate_body()
    tampered = body.replace("Exact tenant: `greenhouse:acme`", "Exact tenant: `greenhouse:other`")

    with pytest.raises(InventorySeedInvalid, match="exact tenant"):
        parse_inventory_seed(tampered)


def test_wrong_native_family_for_known_url_rejects_fast_path() -> None:
    candidate, body = _candidate_body()
    wrong = replace(
        candidate,
        family="lever",
        native_ats="native:lever",
        tenant="lever:acme",
        source_key="ats-scrapers:lever:lever%3Aacme",
    )
    _, tampered = render_candidate_issue(CandidatePlan(wrong, (), ()))

    with pytest.raises(InventorySeedInvalid, match="identifies monitor 'greenhouse'"):
        parse_inventory_seed(tampered)


def test_unsupported_family_is_not_seeded() -> None:
    _, body = _candidate_body()
    tampered = body.replace(
        "Upstream inventory family: `greenhouse`",
        "Upstream inventory family: `unknown_ats`",
    )

    with pytest.raises(InventorySeedInvalid, match="not seedable"):
        parse_inventory_seed(tampered)


def test_human_request_has_no_seed_and_unchanged_preverify_path() -> None:
    body = "Please add Acme from https://acme.example/careers"

    assert parse_inventory_seed(body) is None
    assert preverify_inventory_context(body) == ""


def test_inventory_label_is_required_as_provenance() -> None:
    assert issue_has_inventory_label({"labels": [{"name": "source:ats-inventory"}]})
    assert issue_has_inventory_label({"labels": ["source:ats-inventory"]})
    assert not issue_has_inventory_label({"labels": [{"name": "company-request"}]})
    assert not issue_has_inventory_label({})


def test_seed_rechecks_current_exact_url_and_tenant_before_configuration(tmp_path) -> None:
    _, body = _candidate_body(url="https://job-boards.greenhouse.io/acme")
    seed = parse_inventory_seed(body)
    assert seed is not None
    companies_path = tmp_path / "companies.csv"
    boards_path = tmp_path / "boards.csv"
    companies_path.write_text(
        "slug,name,website,logo_url,icon_url,logo_type\n"
        "existing,Existing,https://existing.example,,,\n"
    )
    boards_path.write_text(
        "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,"
        "scraper_config\n"
        "existing,existing-careers,https://job-boards.greenhouse.io/acme,greenhouse,{},skip,{}\n"
    )

    evidence = current_registry_hard_evidence(
        seed,
        companies_path=companies_path,
        boards_path=boards_path,
    )

    assert {item.code for item in evidence} == {
        "existing_board_url",
        "existing_ats_tenant",
    }


def test_duplicate_check_remains_required_for_seeded_issue() -> None:
    _, body = _candidate_body()
    context = preverify_inventory_context(body)
    template = (
        __import__("pathlib").Path(__file__).parents[1] / "src/workspace/steps/00-pre-verify.md"
    ).read_text()
    rendered = template.format(
        issue=123,
        issue_title="Add company: Acme",
        issue_body=body,
        ats_inventory_context=context,
    )

    assert "Validated inventory seed" in rendered
    assert 'ws search "<company name>"' in rendered
    assert "Check if the company already exists" in rendered
    assert "verify the company/tenant" in rendered
    assert "configuration review" in rendered
    assert "ensure coverage" in rendered
    assert "is complete" in rendered
    assert "instead of rejecting the issue" in rendered
    assert "do not create" in rendered
    assert "a competing company PR" in rendered
    assert "--reconfig" in rendered


def test_orchestrator_keeps_additional_board_research_and_all_gates() -> None:
    seed, _ = _candidate_body()
    parsed = parse_inventory_seed(_candidate_body()[1])
    assert parsed is not None
    state = parsed.to_workspace_state()
    rendered = render_parallel_prompt(
        "orchestrator",
        {
            "slug": "acme",
            "company_name": "Acme",
            "ats_inventory_seed": state,
            "track_a_prompt": "A",
            "track_b_prompt": "B",
            "track_c_prompt": "C",
            "config_tester_raw": "tester",
            "config_comparison_raw": "compare",
        },
    )

    assert seed.source_key in rendered
    assert "--config inventory-seed" in rendered
    assert "successful run with one or more live jobs" in rendered
    assert "Track C must discover all official global" in rendered
    assert "All metadata fields set" in rendered
    assert "All boards configured and feedback recorded" in rendered
    assert "ws compare-boards" in rendered
    assert "ws submit" in rendered

    track_c = render_parallel_prompt(
        "track-c-boards",
        {
            "slug": "acme",
            "website": "https://acme.example",
            "company_name": "Acme",
            "monitor_table": "",
            "ats_inventory_seed": state,
        },
    )
    assert "Independently verify that this tenant belongs" in track_c
    assert "already-registered URL is not duplicated" in track_c
    assert "find every additional official board" in track_c

    verified_state = {**state, "status": "verified", "jobs": 37}
    verified = render_parallel_prompt(
        "orchestrator",
        {
            "slug": "acme",
            "company_name": "Acme",
            "ats_inventory_seed": verified_state,
            "track_a_prompt": "A",
            "track_b_prompt": "B",
            "track_c_prompt": "C",
            "config_tester_raw": "tester",
            "config_comparison_raw": "compare",
        },
    )
    assert "Inventory board already verified" in verified
    assert "Do not rerun the seed" in verified
    assert "--config inventory-seed" not in verified

    fallback_state = {**state, "status": "fallback", "reason": "current registry duplicate"}
    fallback = render_parallel_prompt(
        "orchestrator",
        {
            "slug": "acme",
            "company_name": "Acme",
            "ats_inventory_seed": fallback_state,
            "track_a_prompt": "A",
            "track_b_prompt": "B",
            "track_c_prompt": "C",
            "config_tester_raw": "tester",
            "config_comparison_raw": "compare",
        },
    )
    assert "Inventory fast path disabled" in fallback
    assert "current registry duplicate" in fallback
    assert "Do not rerun `inventory-seed`" in fallback
    assert "--config inventory-seed" not in fallback


def test_parallel_prompts_probe_provider_specific_scrapers() -> None:
    prompts = Path(__file__).parents[1] / "src/workspace/steps/parallel"
    tester = (prompts / "config-tester.md").read_text()
    rendered = render_parallel_prompt(
        "orchestrator",
        {
            "slug": "acme",
            "company_name": "Acme",
            "ats_inventory_seed": None,
            "track_a_prompt": "A",
            "track_b_prompt": "B",
            "track_c_prompt": "C",
            "config_tester_raw": tester,
            "config_comparison_raw": "compare",
        },
    )

    assert "ws probe scraper acme --board <alias>" in rendered
    assert "use `onlyfy` for Onlyfy/Prescreen job URLs" in rendered
    assert "do not assume that a generic `dom` monitor" in rendered
    assert "ws help browser-resources" in rendered
    assert '"resource_policy": "auto", "bot_protection": false' in rendered
    assert "If the control is already blocked, the result is inconclusive" in rendered
