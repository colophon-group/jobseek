from __future__ import annotations

from src.core.scrapers import JobContent, enrich_description


def test_skips_empty_string_section():
    obj = JobContent(description="<p>About the role…</p>", extras={"responsibilities": ""})
    enrich_description(obj)
    assert "Responsibilities" not in obj.description


def test_skips_list_of_empty_strings():
    obj = JobContent(
        description="<p>About the role…</p>",
        extras={"responsibilities": [""], "qualifications": ["  ", "\n"]},
    )
    enrich_description(obj)
    assert "Responsibilities" not in obj.description
    assert "Qualifications" not in obj.description


def test_appends_non_empty_section():
    obj = JobContent(
        description="<p>About the role…</p>",
        extras={"qualifications": ["5+ years experience", "Python expertise"]},
    )
    enrich_description(obj)
    assert "<h3>Qualifications</h3>" in obj.description
    assert "5+ years experience" in obj.description


def test_drops_empty_items_but_keeps_non_empty():
    obj = JobContent(
        description="<p>About the role…</p>",
        extras={"responsibilities": ["", "Ship code", "   "]},
    )
    enrich_description(obj)
    assert "<h3>Responsibilities</h3>" in obj.description
    assert "<li>Ship code</li>" in obj.description
    assert "<li></li>" not in obj.description


def test_skips_duplicate_content():
    obj = JobContent(
        description="<p>You will build reliable systems for production use.</p>",
        extras={"responsibilities": ["You will build reliable systems for production use"]},
    )
    enrich_description(obj)
    assert obj.description.count("build reliable systems") == 1


def test_oracle_hcm_empty_responsibilities_regression():
    """Oracle HCM often returns ExternalResponsibilitiesStr="" — must not append bare heading."""
    obj = JobContent(
        description="<p>Full job description…</p>",
        extras={"responsibilities": [""], "qualifications": [""]},
    )
    enrich_description(obj)
    assert obj.description == "<p>Full job description…</p>"
