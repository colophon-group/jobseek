"""Tests for CPU-side posting-title normalization."""

from __future__ import annotations

from src.processing.cpu import _build_titles, _resolve_occupation_seniority


def test_build_titles_decodes_html_character_references() -> None:
    assert _build_titles("Senior Health &amp; Protection Consultant", None) == [
        "Senior Health & Protection Consultant"
    ]


def test_build_titles_decodes_numeric_character_references() -> None:
    assert _build_titles("R&#38;D Lead &#x2014; Zürich", None) == ["R&D Lead — Zürich"]


def test_build_titles_deduplicates_localizations_after_decoding() -> None:
    assert _build_titles(
        "Research &amp; Development",
        {
            "en": {"title": "Research & Development"},
            "de": {"title": "Forschung &amp; Entwicklung"},
        },
    ) == ["Research & Development", "Forschung & Entwicklung"]


def test_internship_employment_signal_falls_back_to_intern_seniority() -> None:
    assert _resolve_occupation_seniority(
        "Software Developer",
        {},
        {"intern": 7},
        employment_type="Internship",
    ) == (None, 7)


def test_internship_employment_signal_wins_over_title_seniority() -> None:
    assert _resolve_occupation_seniority(
        "Senior Software Engineer",
        {},
        {"senior": 3, "intern": 7},
        employment_type="internship",
    ) == (None, 7)
