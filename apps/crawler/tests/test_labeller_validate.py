"""Tests for the labeller's JSON Schema + custom validators."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.labeller.validate import (
    SECTION_EXTRACT_KINDS,
    qa_report,
    run_qa_rules,
    validate_file,
    validate_globals_consistency,
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


def test_sections_schema_rejects_legal_kind():
    """`legal` was dropped from the closed vocab."""
    data = {"sections": [{"kind": "legal", "block_ids": [0]}]}
    assert validate_schema("sections", data)


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
        {"id": "x", "input": {"blocks": [{"id": 0, "tag": "p", "html": "", "text": ""}]}},
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
    if kind == "team":
        return {"team_name": None, "team_function_tags": []}
    if kind == "role":
        return {
            "role_summary": None,
            "responsibilities": [],
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
            "remote_policy": None,
            "remote_region": None,
            "relocation_assistance": None,
            "visa_sponsorship": None,
            "annual_leave_days": None,
            "annual_leave_unlimited": None,
            "parental_leave_weeks": None,
            "learning_budget_amount_year": None,
            "other_perks": [],
        }
    raise AssertionError(f"unhandled kind {kind}")


@pytest.mark.parametrize("kind", sorted(SECTION_EXTRACT_KINDS))
def test_minimal_section_payload_validates(kind: str):
    payload = _minimal_section_payload(kind)
    assert validate_schema(kind, payload) == []


def test_requirements_schema_accepts_decimal_experience_years():
    payload = _minimal_section_payload("requirements")
    payload["years_experience_min"] = 0.5
    payload["years_experience_max"] = 1.5
    assert validate_schema("requirements", payload) == []


def test_skill_category_closed_set_enforced():
    payload = _minimal_section_payload("requirements")
    payload["required_skills"] = [{"skill": "python", "category": "invented_category"}]
    assert validate_schema("requirements", payload)


def test_cut_fields_rejected_by_benefits_schema():
    """Fields cut in the slimdown (item 4) must NOT re-appear silently."""
    payload = _minimal_section_payload("benefits")
    payload["bonus_offered"] = True
    errors = validate_schema("benefits", payload)
    assert errors, "benefits schema should reject cut field 'bonus_offered'"


def _minimal_globals(**overrides: object) -> dict:
    base = {
        "profession": None,
        "title_normalized": None,
        "store_number": None,
        "reference_number": None,
        "seniority": None,
        "employment_type": None,
        "locales_in_posting": [],
        "locations": [],
    }
    base.update(overrides)
    return base


def test_globals_schema_minimal():
    assert validate_schema("globals", _minimal_globals()) == []


def test_globals_schema_rejects_cut_field():
    """technologies_aggregate was cut; schema must reject it."""
    data = _minimal_globals()
    data["technologies_aggregate"] = []
    assert validate_schema("globals", data)


def test_globals_schema_rejects_old_occupation_field():
    """occupation was renamed to profession in the title-normalization refactor."""
    data = _minimal_globals()
    data["occupation"] = "backend engineering"
    assert validate_schema("globals", data)


def test_globals_schema_location_type_required():
    data = _minimal_globals(locations=[{"raw": "Dublin, Ireland"}])  # missing type
    assert validate_schema("globals", data)


# ---------- qa validation ----------


def _minimal_merged_posting(overrides: dict | None = None) -> dict:
    base = {
        "id": "p1",
        "schema_version": 1,
        "normalizer_version": "v0.1.0",
        "sampled_at": "2026-04-24T00:00:00+00:00",
        "source": {"source_url": "https://example.com/job/1"},
        "input": {
            "title_raw": "Senior Engineer",
            "description_html": "<p>body</p>",
            "description_text": "body",
            "blocks": [
                {"id": 0, "tag": "p", "html": "<p>a</p>", "text": "a"},
                {"id": 1, "tag": "p", "html": "<p>b</p>", "text": "b"},
                {"id": 2, "tag": "p", "html": "<p>c</p>", "text": "c"},
                {"id": 3, "tag": "p", "html": "<p>d</p>", "text": "d"},
                {"id": 4, "tag": "p", "html": "<p>e</p>", "text": "e"},
            ],
        },
        "labels": {
            "sections": [
                {
                    "kind": "role",
                    "block_ids": [0, 1],
                    "extracted": {
                        "role_summary": "build things",
                        "responsibilities": ["Ship services"],
                        "collaboration_partners": ["product"],
                        "travel_expected": None,
                        "shift_pattern": None,
                        "hours_per_week": None,
                        "on_call_required": None,
                    },
                },
                {
                    "kind": "requirements",
                    "block_ids": [2, 3],
                    "extracted": {
                        "years_experience_min": 5,
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
                    },
                },
            ],
            "globals": {
                "profession": "backend engineer",
                "title_normalized": "Senior Backend Engineer",
                "store_number": None,
                "reference_number": None,
                "seniority": "senior",
                "employment_type": "full_time",
                "locales_in_posting": ["en"],
                "locations": [
                    {"raw": "Dublin, Ireland", "city": "Dublin", "country": "IE", "type": "onsite"}
                ],
            },
        },
        "labelling_meta": {"qa_verdict": "accepted", "qa_rationale": None, "retries": {}},
    }
    if overrides:
        base = {**base, **overrides}
    return base


def test_qa_accepts_minimal_good_posting():
    report = qa_report(_minimal_merged_posting())
    assert report["verdict"] == "accepted", report


def test_qa_rejects_missing_profession():
    posting = _minimal_merged_posting()
    posting["labels"]["globals"]["profession"] = None
    report = qa_report(posting)
    assert report["verdict"] == "rejected"
    failed = [r["name"] for r in report["rules"] if not r["passed"]]
    assert "profession_non_empty" in failed


def test_qa_accepts_no_locations():
    """Many ATSes (Lever, Workday) store location outside the description. An empty
    locations array is acceptable when the description itself doesn't state one.
    """
    posting = _minimal_merged_posting()
    posting["labels"]["globals"]["locations"] = []
    assert qa_report(posting)["verdict"] == "accepted"


def test_qa_rejects_null_extraction():
    posting = _minimal_merged_posting()
    posting["labels"]["sections"][0]["extracted"] = None
    assert qa_report(posting)["verdict"] == "rejected"


def test_qa_rejects_empty_responsibilities_when_role_present():
    posting = _minimal_merged_posting()
    posting["labels"]["sections"][0]["extracted"]["responsibilities"] = []
    assert qa_report(posting)["verdict"] == "rejected"


def test_qa_rejects_low_split_coverage():
    posting = _minimal_merged_posting()
    # Add 20 unclaimed blocks; only 4 of 25 are covered -> 16%
    posting["input"]["blocks"].extend(
        [{"id": i, "tag": "p", "html": "<p>x</p>", "text": "x"} for i in range(5, 25)]
    )
    assert qa_report(posting)["verdict"] == "rejected"


def test_qa_report_shape_matches_schema():
    report = qa_report(_minimal_merged_posting())
    assert validate_schema("qa", report) == []


def test_run_qa_rules_returns_list_of_dicts():
    rules = run_qa_rules(_minimal_merged_posting())
    assert isinstance(rules, list)
    assert all(isinstance(r, dict) and "name" in r and "passed" in r for r in rules)


def test_validate_file_qa_kind(tmp_path: Path):
    p = _write(tmp_path / "posting.json", _minimal_merged_posting())
    assert validate_file("qa", p) == []

    bad = _minimal_merged_posting()
    bad["labels"]["globals"]["profession"] = None
    p2 = _write(tmp_path / "bad.json", bad)
    errs = validate_file("qa", p2)
    assert errs
    assert any("profession_non_empty" in e for e in errs)


# ---------- globals consistency (leadership / IC-suffix mismatch) ----------


def test_globals_consistency_passes_when_either_field_is_null():
    assert validate_globals_consistency({"profession": None, "seniority": "head of"}) == []
    g = {"profession": "compliance specialist", "seniority": None}
    assert validate_globals_consistency(g) == []
    assert validate_globals_consistency({}) == []


def test_globals_consistency_passes_no_ic_suffix():
    """Bare role nouns or non-suffix role names — no rule fires regardless of seniority."""
    assert (
        validate_globals_consistency({"profession": "compliance officer", "seniority": "head of"})
        == []
    )
    assert validate_globals_consistency({"profession": "engineering", "seniority": "head of"}) == []
    assert (
        validate_globals_consistency({"profession": "account executive", "seniority": "director"})
        == []
    )


def test_globals_consistency_passes_ic_suffix_without_leadership():
    """Specialist/analyst at non-leadership rank is the legitimate IC pairing."""
    assert validate_globals_consistency({"profession": "data analyst", "seniority": "senior"}) == []
    assert (
        validate_globals_consistency({"profession": "events coordinator", "seniority": "junior"})
        == []
    )
    assert (
        validate_globals_consistency({"profession": "marketing specialist", "seniority": "mid"})
        == []
    )
    # `lead` is ambiguous (team lead vs lead engineer) — kept out of leadership markers
    assert validate_globals_consistency({"profession": "data analyst", "seniority": "lead"}) == []


def test_globals_consistency_passes_avp_carve_out():
    """AVP / Associate VP at banks is a mid-level IC role, not actual leadership.
    The rule must NOT fire on legitimate analyst+AVP pairings (Swiss Re-style)."""
    assert (
        validate_globals_consistency(
            {"profession": "market analyst", "seniority": "assistant vice president"}
        )
        == []
    )
    assert (
        validate_globals_consistency(
            {"profession": "data analyst", "seniority": "associate vice president"}
        )
        == []
    )


def test_globals_consistency_catches_compliance_specialist_head_of():
    """The HRT 2026-05-09 case: profession ends in 'specialist' but title is 'Head of'."""
    errs = validate_globals_consistency(
        {"profession": "compliance specialist", "seniority": "head of"}
    )
    assert errs
    assert "compliance specialist" in errs[0]
    assert "head of" in errs[0]
    assert "specialist" in errs[0]


@pytest.mark.parametrize(
    "profession,seniority",
    [
        ("marketing analyst", "director"),
        ("program coordinator", "vp"),
        ("events coordinator", "chief"),
        ("legal associate", "principal"),
        ("research assistant", "vice president"),
        ("compliance specialist", "Head of"),  # case-insensitive
        # `manager` seniority + IC-suffix profession (Clearway-class):
        ("wind plant operator", "manager"),
        ("data analyst", "manager"),
        ("warehouse associate", "manager"),
    ],
)
def test_globals_consistency_catches_other_leadership_pairings(profession: str, seniority: str):
    errs = validate_globals_consistency({"profession": profession, "seniority": seniority})
    assert errs, f"expected violation for {profession!r} + {seniority!r}"


@pytest.mark.parametrize(
    "raw",
    ["#LI-Hybrid", "#LI-Remote", "#LI-Onsite", "#BI-Remote", "#anywhere", " #LI-Hybrid"],
)
def test_globals_consistency_catches_hashtag_locations(raw: str):
    """The 2026-05-08 Datadog case: #LI-Hybrid in locations[] is a recruiter
    work-mode tag, not a place. Validator must catch it deterministically."""
    g = {
        "profession": "product manager",
        "seniority": "senior",
        "locations": [
            {"raw": raw, "city": None, "region": None, "country": None, "type": "hybrid"}
        ],
    }
    errs = validate_globals_consistency(g)
    assert errs
    assert any("hashtag" in e.lower() and raw.strip() in e for e in errs)


def test_globals_consistency_passes_normal_locations():
    """Real place names with leading whitespace stay valid."""
    g = {
        "profession": "engineer",
        "seniority": None,
        "locations": [
            {"raw": "Berlin", "city": "Berlin", "country": "DE", "type": "onsite"},
            {"raw": "  San Francisco", "city": "San Francisco", "country": "US", "type": "hybrid"},
        ],
    }
    assert validate_globals_consistency(g) == []


def test_globals_consistency_passes_canonical_manager_titles():
    """Canonical "<X> manager" professions don't end in IC-suffix, so they pass
    through even at seniority='manager' (e.g. engineering manager, store manager
    are recognized role titles per the prompt's exception list)."""
    cases = [
        ("engineering manager", "manager"),
        ("store manager", "manager"),
        ("project manager", "manager"),
        ("customer success manager", "manager"),
        ("channel sales manager", "manager"),
    ]
    for profession, seniority in cases:
        assert (
            validate_globals_consistency({"profession": profession, "seniority": seniority}) == []
        ), f"unexpected violation for {profession!r} + {seniority!r}"


def test_validate_file_globals_kind_runs_consistency_rule(tmp_path: Path):
    """Consistency rule fires through the granular globals validation path."""
    bad = _minimal_globals(profession="compliance specialist", seniority="head of")
    p = _write(tmp_path / "globals.json", bad)
    errs = validate_file("globals", p)
    assert any("subordinate-rank suffix" in e for e in errs)


def test_validate_file_extract_all_runs_consistency_rule(tmp_path: Path):
    """Consistency rule fires through the combined extractor validation path."""
    payload = {
        "sections": [
            {"kind": "company", "block_ids": [0], "extracted": None},
        ],
        "globals": _minimal_globals(profession="compliance specialist", seniority="head of"),
    }
    f = _write(tmp_path / "out.json", payload)
    ctx = _write(tmp_path / "ctx.json", {"input": {"blocks": [{"id": 0}]}})
    errs = validate_file("extract_all", f, context_path=ctx)
    assert any("subordinate-rank suffix" in e for e in errs)
