"""Tests for the rule-based canonicalizer."""

from __future__ import annotations

from src.labeller.canonicalize import CANONICALIZER_VERSION, canonicalize_posting


def test_posting_with_no_canonicalizable_fields_returns_zero_coverage():
    posting = {
        "id": "p1",
        "labels": {
            "sections": [],
            "globals": {
                "occupation": None,
                "seniority": None,
                "technologies_aggregate": [],
            },
        },
    }
    result = canonicalize_posting(posting)
    assert result["canonicalizer_version"] == CANONICALIZER_VERSION
    assert result["posting_id"] == "p1"
    assert all(v == [] for v in result["mappings"].values())
    assert result["unmapped"] == []


def test_known_technology_is_mapped():
    posting = {
        "id": "p2",
        "labels": {
            "sections": [],
            "globals": {
                "occupation": None,
                "seniority": None,
                "technologies_aggregate": ["python"],
            },
        },
    }
    result = canonicalize_posting(posting)
    # Exact slug match — should be mapped
    assert "python" in result["mappings"]["technology_ids"]
    assert result["coverage"]["technologies"]["mapped"] == 1


def test_unknown_technology_is_unmapped():
    posting = {
        "id": "p3",
        "labels": {
            "sections": [],
            "globals": {
                "occupation": None,
                "seniority": None,
                "technologies_aggregate": ["xyz_made_up_tech_999"],
            },
        },
    }
    result = canonicalize_posting(posting)
    assert result["mappings"]["technology_ids"] == []
    assert len(result["unmapped"]) == 1
    assert result["unmapped"][0]["field"] == "labels.globals.technologies_aggregate[0]"


def test_section_tools_mapped_to_technologies():
    posting = {
        "id": "p4",
        "labels": {
            "sections": [
                {
                    "kind": "role",
                    "block_ids": [0],
                    "extracted": {
                        "role_summary": None,
                        "responsibilities": [],
                        "tools_used": ["python", "not_a_real_tech_zzz"],
                        "collaboration_partners": [],
                        "travel_expected": None,
                        "shift_pattern": None,
                        "hours_per_week": None,
                        "on_call_required": None,
                    },
                }
            ],
            "globals": {"occupation": None, "seniority": None, "technologies_aggregate": []},
        },
    }
    result = canonicalize_posting(posting)
    assert result["coverage"]["technologies"]["mapped"] >= 1
    assert result["coverage"]["technologies"]["unmapped"] >= 1


def test_duplicate_ids_deduplicated_in_mappings():
    posting = {
        "id": "p5",
        "labels": {
            "sections": [
                {
                    "kind": "role",
                    "block_ids": [0],
                    "extracted": {
                        "role_summary": None,
                        "responsibilities": [],
                        "tools_used": ["python"],
                        "collaboration_partners": [],
                        "travel_expected": None,
                        "shift_pattern": None,
                        "hours_per_week": None,
                        "on_call_required": None,
                    },
                },
                {
                    "kind": "requirements",
                    "block_ids": [1],
                    "extracted": {
                        "required_skills": [
                            {"skill": "python", "category": "programming_language"}
                        ],
                        "years_experience_min": None,
                        "years_experience_max": None,
                        "education_level": None,
                        "education_strict": None,
                        "degree_fields": [],
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
                "occupation": None,
                "seniority": None,
                "technologies_aggregate": ["python"],
            },
        },
    }
    result = canonicalize_posting(posting)
    # python appears in 3 places but should be deduped to one mapping id
    assert result["mappings"]["technology_ids"].count("python") == 1
