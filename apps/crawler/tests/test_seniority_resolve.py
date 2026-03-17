"""Tests for seniority_resolve.match_seniority."""

from __future__ import annotations

import pytest

from src.core.seniority_resolve import match_seniority


class TestPrefixMatching:
    """Senior, Junior, Staff, Lead, Principal prefix patterns."""

    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Senior Software Engineer", "senior"),
            ("Sr. Data Analyst", "senior"),
            ("Sr Backend Developer", "senior"),
            ("Junior QA Engineer", "entry"),
            ("Jr. Frontend Developer", "entry"),
            ("Staff Engineer", "staff"),
            ("Staff Data Scientist", "staff"),
            ("Principal Engineer", "principal"),
            ("Distinguished Engineer", "principal"),
            ("Lead DevOps Engineer", "lead"),
            ("Tech Lead", "lead"),
            ("Team Lead", "lead"),
        ],
    )
    def test_prefix_matches(self, title: str, expected: str):
        assert match_seniority(title) == expected

    def test_lead_generation_excluded(self):
        """'Lead Generation' is not a seniority indicator."""
        assert match_seniority("Lead Generation Manager") is None

    def test_lead_qualification_excluded(self):
        assert match_seniority("Lead Qualification Specialist") is None


class TestDirectorVP:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Director of Engineering", "director"),
            ("Director, Product Management", "director"),
            ("VP of Sales", "director"),
            ("Vice President Engineering", "director"),
            ("Head of Product", "director"),
            ("Head of Engineering", "director"),
        ],
    )
    def test_director_level(self, title: str, expected: str):
        assert match_seniority(title) == expected

    def test_art_director_excluded(self):
        assert match_seniority("Art Director") is None

    def test_creative_director_excluded(self):
        assert match_seniority("Creative Director") is None

    def test_funeral_director_excluded(self):
        assert match_seniority("Funeral Director") is None


class TestExecutive:
    @pytest.mark.parametrize(
        "title",
        [
            "CEO",
            "CTO",
            "CFO",
            "Managing Director",
        ],
    )
    def test_executive_matches(self, title: str):
        assert match_seniority(title) == "executive"

    def test_cio_advisory_excluded(self):
        """PwC 'CIO Advisory' practice is not an exec title."""
        assert match_seniority("CIO Advisory Consultant") is None


class TestIntern:
    @pytest.mark.parametrize(
        "title",
        [
            "Software Engineering Intern",
            "Internship - Data Science",
            "Werkstudent Frontend-Entwicklung",
            "Praktikum Software Engineering",
            "Stagiaire Marketing",
            "Alternance Développeur",
            "Duales Studium Informatik",
            "Ausbildung Fachinformatiker",
            "Trainee Program",
        ],
    )
    def test_intern_keywords(self, title: str):
        assert match_seniority(title) == "intern"


class TestGraduate:
    def test_graduate_program(self):
        assert match_seniority("Graduate Program Data Analytics") == "entry"

    def test_graduate_scheme(self):
        assert match_seniority("Graduate Scheme Engineering") == "entry"


class TestNoMatch:
    """Titles that should NOT match any seniority level."""

    @pytest.mark.parametrize(
        "title",
        [
            "Software Engineer",
            "Data Analyst",
            "Product Manager",
            "Account Executive",
            "Solutions Architect",
            "Maintenance Technician",
            "Warehouse Associate",
        ],
    )
    def test_no_seniority(self, title: str):
        assert match_seniority(title) is None

    def test_empty_string(self):
        assert match_seniority("") is None

    def test_none(self):
        assert match_seniority(None) is None


class TestGermanTitles:
    def test_geschaeftsfuehrer(self):
        assert match_seniority("Geschäftsführer") == "executive"

    def test_senior_with_gender(self):
        assert match_seniority("Senior Entwickler:in") == "senior"

    def test_werkstudent_with_gender(self):
        assert match_seniority("Werkstudent:in Frontend") == "intern"
