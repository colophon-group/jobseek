from __future__ import annotations

from src.shared.slug import slugify


class TestSlugify:
    def test_simple_name(self):
        assert slugify("Stripe") == "stripe"

    def test_spaces(self):
        assert slugify("Goldman Sachs") == "goldman-sachs"

    def test_ampersand(self):
        assert slugify("McKinsey & Company") == "mckinsey-and-company"

    def test_unicode(self):
        assert slugify("Zürich Insurance") == "zurich-insurance"

    def test_collapses_multiple_hyphens(self):
        assert slugify("A -- B") == "a-b"

    def test_strips_leading_trailing_hyphens(self):
        assert slugify("--hello--") == "hello"

    def test_mixed_case_and_numbers(self):
        assert slugify("Company123") == "company123"

    def test_all_special_chars(self):
        assert slugify("!!!") == ""

    def test_dots(self):
        assert slugify("Company.io") == "company-io"

    def test_parentheses(self):
        assert slugify("Company (Inc)") == "company-inc"

    def test_slash(self):
        assert slugify("A/B Testing") == "a-b-testing"
