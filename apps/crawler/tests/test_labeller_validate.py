"""Tests for the labeller's JSON Schema + custom validators."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.labeller.validate import (
    SECTION_EXTRACT_KINDS,
    validate_file,
    validate_schema,
    validate_sections_custom,
)


def _write(path: Path, obj: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))
    return path


# ---------- sections schema ----------


def test_sections_schema_accepts_valid():
    data = {
        "sections": [
            {"kind": "company", "block_ids": [0, 1]},
            {"kind": "role", "block_ids": [2, 3, 4]},
        ]
    }
    assert validate_schema("sections", data) == []


def test_sections_schema_rejects_unknown_kind():
    data = {"sections": [{"kind": "about_us", "block_ids": [0]}]}
    errors = validate_schema("sections", data)
    assert errors
    assert any("enum" in e.lower() or "not" in e.lower() for e in errors)


def test_sections_schema_requires_block_ids():
    data = {"sections": [{"kind": "company", "block_ids": []}]}
    assert validate_schema("sections", data)


# ---------- sections custom rules ----------


def test_custom_catches_missing_block_id():
    data = {"sections": [{"kind": "company", "block_ids": [0, 5]}]}
    errs = validate_sections_custom(data, block_ids={0, 1, 2})
    assert any("5" in e and "does not exist" in e for e in errs)


def test_custom_catches_non_contiguous_ids():
    data = {"sections": [{"kind": "role", "block_ids": [0, 2, 4]}]}
    errs = validate_sections_custom(data, block_ids={0, 1, 2, 3, 4})
    assert any("contiguous" in e for e in errs)


def test_custom_catches_overlap():
    data = {
        "sections": [
            {"kind": "company", "block_ids": [0, 1, 2]},
            {"kind": "role", "block_ids": [2, 3]},
        ]
    }
    errs = validate_sections_custom(data, block_ids={0, 1, 2, 3, 4})
    assert any("already appears" in e for e in errs)


def test_custom_accepts_gaps():
    data = {
        "sections": [
            {"kind": "company", "block_ids": [0, 1]},
            {"kind": "role", "block_ids": [3, 4]},
        ]
    }
    errs = validate_sections_custom(data, block_ids={0, 1, 2, 3, 4, 5})
    assert errs == []


# ---------- validate_file integration ----------


def test_validate_file_missing(tmp_path: Path):
    errs = validate_file("sections", tmp_path / "nope.json")
    assert errs
    assert "does not exist" in errs[0]


def test_validate_file_malformed_json(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    errs = validate_file("sections", p)
    assert any("not valid JSON" in e for e in errs)


def test_validate_file_reports_subagent_error_response(tmp_path: Path):
    p = _write(tmp_path / "err.json", {"error": "could not identify any sections"})
    errs = validate_file("sections", p)
    assert any("subagent reported an error" in e for e in errs)


def test_validate_file_sections_happy_path(tmp_path: Path):
    ctx_path = _write(
        tmp_path / "input.json",
        {
            "id": "x",
            "input": {"blocks": [{"id": 0, "tag": "p", "html": "", "text": ""}]},
        },
    )
    out_path = _write(
        tmp_path / "out.json",
        {"sections": [{"kind": "company", "block_ids": [0]}]},
    )
    errs = validate_file("sections", out_path, context_path=ctx_path)
    assert errs == []


def test_validate_file_sections_catches_invalid_id(tmp_path: Path):
    ctx_path = _write(
        tmp_path / "input.json",
        {"id": "x", "input": {"blocks": [{"id": 0, "tag": "p", "html": "", "text": ""}]}},
    )
    out_path = _write(
        tmp_path / "out.json",
        {"sections": [{"kind": "company", "block_ids": [0, 99]}]},
    )
    errs = validate_file("sections", out_path, context_path=ctx_path)
    assert any("99" in e for e in errs)


# ---------- per-section schemas smoke ----------


def _minimal_section_payload(kind: str) -> dict:
    """Produce the minimum valid extraction payload per kind."""
    if kind == "company":
        return {
            "industry_tags": [],
            "size_band": None,
            "funding_stage": None,
            "mission_verbatim": None,
        }
    if kind == "team":
        return {"team_name": None, "team_function_tags": []}
    if kind == "role":
        return {
            "role_summary": None,
            "responsibilities": [],
            "tools_used": [],
            "collaboration_partners": [],
            "travel_expected": None,
            "shift_pattern": None,
            "hours_per_week": None,
            "on_call_required": None,
        }
    if kind == "requirements":
        return {
            "years_experience_min": None,
            "years_experience_max": None,
            "education_level": None,
            "education_strict": None,
            "degree_fields": [],
            "required_skills": [],
            "required_languages": [],
            "required_certifications": [],
            "security_clearance": None,
            "physical_requirements": [],
            "background_check_required": None,
            "driving_license_required": None,
        }
    if kind == "preferred":
        return {
            "preferred_skills": [],
            "preferred_education": None,
            "preferred_certifications": [],
            "preferred_years_additional": None,
        }
    if kind == "benefits":
        return {
            "salary_min": None,
            "salary_max": None,
            "salary_currency": None,
            "salary_period": None,
            "salary_transparency": None,
            "compensation_type": None,
            "equity_offered": None,
            "equity_description": None,
            "bonus_offered": None,
            "signing_bonus_offered": None,
            "remote_policy": None,
            "remote_region": None,
            "hybrid_days_onsite": None,
            "relocation_assistance": None,
            "visa_sponsorship": None,
            "healthcare_offered": None,
            "annual_leave_days": None,
            "annual_leave_unlimited": None,
            "parental_leave_weeks": None,
            "learning_budget_amount_year": None,
            "learning_budget_currency": None,
            "retirement_plan": None,
            "other_perks": [],
        }
    if kind == "application":
        return {"application_deadline": None}
    raise AssertionError(f"unhandled kind {kind}")


@pytest.mark.parametrize("kind", sorted(SECTION_EXTRACT_KINDS))
def test_minimal_section_payload_validates(kind: str):
    payload = _minimal_section_payload(kind)
    assert validate_schema(kind, payload) == []


def test_skill_category_closed_set_enforced():
    payload = _minimal_section_payload("requirements")
    payload["required_skills"] = [{"skill": "python", "category": "invented_category"}]
    assert validate_schema("requirements", payload)


def test_globals_schema_minimal():
    data = {
        "occupation": None,
        "seniority": None,
        "employment_type": None,
        "locales_in_posting": [],
        "locations": [],
        "technologies_aggregate": [],
    }
    assert validate_schema("globals", data) == []


def test_globals_schema_location_type_required():
    data = {
        "occupation": None,
        "seniority": None,
        "employment_type": None,
        "locales_in_posting": [],
        "locations": [{"raw": "Dublin, Ireland"}],  # missing type
        "technologies_aggregate": [],
    }
    assert validate_schema("globals", data)
