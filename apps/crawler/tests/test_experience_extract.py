"""Tests for experience extraction heuristics."""

from __future__ import annotations

from src.core.experience_extract import ExperienceRequirement, extract_experience


class TestBasicPatterns:
    def test_plus_years(self):
        html = "<li>5+ years of experience in consumer electronics</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=5, max_years=None)

    def test_range_years(self):
        html = "<li>3-5 years of software development experience</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=3, max_years=5)

    def test_exact_years(self):
        html = "<li>5 years of experience with 3D CAD modeling</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=5, max_years=None)

    def test_apostrophe_years(self):
        html = "<li>1+ years' experience with Microsoft Office</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=1, max_years=None)

    def test_non_internship(self):
        html = "<li>3+ years of non-internship professional software development experience</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=3, max_years=None)

    def test_domain_specific(self):
        html = "<li>7+ years of engineering experience</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=7, max_years=None)

    def test_decimal_years(self):
        html = "<li>Minimum 2.5 years of software engineering experience</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=2.5, max_years=None)

    def test_decimal_years_does_not_start_matching_after_dot(self):
        html = "<li>At least 1.5+ years of sourcing or buying experience</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=1.5, max_years=None)

    def test_months(self):
        html = "<li>8 months experience supporting enterprise customers</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=0.7, max_years=None)

    def test_plus_months(self):
        html = "<li>6+ months of relevant experience in customer support</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=0.5, max_years=None)

    def test_month_range(self):
        html = "<li>6-18 months of professional experience with logistics operations</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=0.5, max_years=1.5)

    def test_mixed_month_year_range(self):
        html = "<li>Minimum 6 months to 1 year work experience in a similar BPO field</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=0.5, max_years=1.0)


class TestMultipleRequirements:
    """When multiple experience requirements exist, return the highest minimum."""

    def test_picks_highest(self):
        html = """
        <h3>Basic Qualifications</h3>
        <li>7+ years of experience in consumer electronics products</li>
        <li>5+ years of experience with 3D CAD modeling</li>
        <li>5+ years of experience with designing for high volume manufacturing</li>
        """
        result = extract_experience(html)
        assert result is not None
        assert result.min_years == 7

    def test_google_multiple(self):
        html = """
        <li>5 years of experience with software development</li>
        <li>3 years of experience testing, maintaining, or launching software products.</li>
        """
        result = extract_experience(html)
        assert result is not None
        assert result.min_years == 5

    def test_amazon_multiple(self):
        html = """
        <li>8+ years of specific technology domain areas experience</li>
        <li>3+ years of design, implementation, or consulting in applications experience</li>
        <li>10+ years of IT development or implementation/consulting experience</li>
        """
        result = extract_experience(html)
        assert result is not None
        assert result.min_years == 10


class TestReversedWordOrder:
    def test_german(self):
        html = "<li>Erfahrung von mindestens 5 Jahren im Bereich Softwareentwicklung</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=5, max_years=None)

    def test_french(self):
        html = "<li>expérience de 3 ans minimum en développement</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=3, max_years=None)

    def test_german_berufserfahrung(self):
        html = "<li>Berufserfahrung von 7+ Jahren</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=7, max_years=None)

    def test_italian(self):
        html = "<li>esperienza di almeno 4 anni nel settore</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=4, max_years=None)


class TestAtLeast:
    def test_at_least_english(self):
        html = "<li>At least 5 years of experience in software engineering</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=5, max_years=None)

    def test_minimum_prefix(self):
        html = "<li>Minimum 3 years of professional experience</li>"
        result = extract_experience(html)
        assert result == ExperienceRequirement(min_years=3, max_years=None)


class TestFalsePositives:
    """These must return None — company history / unrelated years."""

    def test_company_history_generic(self):
        html = (
            "<p>For over 15 years, our company has been the market leader in cloud computing.</p>"
        )
        assert extract_experience(html) is None

    def test_company_history_specific(self):
        html = "<p>For over 20 years, Acme Corp has been delivering innovation.</p>"
        assert extract_experience(html) is None

    def test_company_supports(self):
        html = "<p>The team with 11+ years, and supports our legal teams.</p>"
        assert extract_experience(html) is None

    def test_no_experience_word(self):
        html = "<p>We have been operating for 10 years in 5 countries.</p>"
        assert extract_experience(html) is None

    def test_years_without_context(self):
        html = "<p>The company was founded 20 years ago.</p>"
        assert extract_experience(html) is None

    def test_warranty(self):
        html = "<p>All products come with a 2 years warranty.</p>"
        assert extract_experience(html) is None

    def test_no_experience_info(self):
        html = (
            "<p>We are looking for a passionate team player who loves building great products.</p>"
        )
        assert extract_experience(html) is None

    def test_since_year(self):
        html = "<p>Since 2005, we have spent 20 years building the best platform.</p>"
        assert extract_experience(html) is None

    def test_internship_duration_months_is_not_experience_requirement(self):
        html = (
            "<li>Able to commit at least 3 months, preferably in a business "
            "administration or data analytics major.</li>"
            "<li>Experience with Excel and data analytical tools.</li>"
        )
        assert extract_experience(html) is None

    def test_commitment_duration_months_is_not_experience_requirement(self):
        html = (
            "<li>Able to commit to a full-time internship for at least 3 months.</li>"
            "<li>Preferred Qualifications: Proven experience in procurement.</li>"
        )
        assert extract_experience(html) is None
