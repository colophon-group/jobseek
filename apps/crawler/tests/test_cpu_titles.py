"""Tests for CPU-side posting-title normalization."""

from __future__ import annotations

from src.processing.cpu import _build_titles


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
