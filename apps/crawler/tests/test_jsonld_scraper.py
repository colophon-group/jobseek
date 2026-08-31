from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.core.scrapers import JobContent
from src.core.scrapers.jsonld import (
    _extract_locations,
    _extract_salary,
    _find_job_posting,
    _JsonLdExtractor,
    _normalize_meta_locations,
    _parse_posting,
    _strip_html,
    _text_or_list,
    can_handle,
    parse_html,
    probe,
    scrape,
)

FIXTURES = Path(__file__).parent / "fixtures"


class TestJsonLdExtractor:
    def test_extracts_single_block(self):
        html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "Engineer"}</script>
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert len(extractor.results) == 1
        assert extractor.results[0]["@type"] == "JobPosting"

    def test_extracts_html_entity_encoded_block(self):
        html = """<html><head><script type="application/ld+json">
        {&quot;@type&quot;:&quot;JobPosting&quot;,&quot;title&quot;:&quot;R&amp;D Engineer&quot;}
        </script></head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert extractor.results == [{"@type": "JobPosting", "title": "R&D Engineer"}]

    def test_extracts_vagas_style_javascript_cdata_block(self):
        html = """<html><head><script type="application/ld+json">
        //<![CDATA[
        {"@context":"https://schema.org","@type":"JobPosting",
         "title":"Analista Contábil",
         "description":"Responsabilidades e requisitos da função.",
         "datePosted":"2026-07-17","validThrough":"2026-08-17",
         "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
         "addressLocality":"Campinas","addressRegion":"SP","addressCountry":"Brasil"}}}
        //]]>
        </script></head></html>"""

        content = parse_html(html)

        assert content.title == "Analista Contábil"
        assert content.description == "Responsabilidades e requisitos da função."
        assert content.locations == ["Campinas, SP, Brasil"]
        assert content.date_posted == "2026-07-17"
        assert content.extras == {"valid_through": "2026-08-17"}

    def test_parses_talentbrew_double_escaped_description_line_breaks(self):
        html = """<html><head><script type="application/ld+json">
        {"@type":"JobPosting","title":"Engineer",
         "description":"<p>Build devices.</p>&amp;#xa;&amp;#xa;<p>Test them.</p>",
         "jobLocation":{"name":"Franklin Lakes, NJ"}}
        </script></head></html>"""

        content = parse_html(html)

        assert content.description == "<p>Build devices.</p>\n\n<p>Test them.</p>"
        assert content.locations == ["Franklin Lakes, NJ"]

    def test_extracts_multiple_blocks(self):
        html = """<html><head>
        <script type="application/ld+json">{"@type": "Organization"}</script>
        <script type="application/ld+json">{"@type": "JobPosting"}</script>
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert len(extractor.results) == 2

    def test_ignores_icims_unavailable_location_parts(self):
        html = """<html><head><script type="application/ld+json">
        {"@type":"JobPosting","title":"Director","description":"Lead programs.",
         "jobLocation":{"@type":"Place","address":{"@type":"PostalAddress",
         "addressLocality":"UNAVAILABLE","addressRegion":"UNAVAILABLE",
         "addressCountry":"US"}}}
        </script></head></html>"""

        assert parse_html(html).locations == ["US"]

    def test_ignores_non_jsonld_scripts(self):
        html = """<html><head>
        <script type="text/javascript">var x = 1;</script>
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert len(extractor.results) == 0

    def test_handles_invalid_json(self):
        html = """<html><head>
        <script type="application/ld+json">not valid json</script>
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert len(extractor.results) == 0

    def test_repairs_talemetry_missing_property_comma(self):
        html = """<html><head><script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": "Sanitation Team Member",
          "description": "<p>Keep the facility safe.</p>",
          "datePosted": "2026-08-15"
          "hiringOrganization": {"@type": "Organization", "name": "OFI"},
          "jobLocation": {
            "@type": "Place",
            "address": {
              "@type": "PostalAddress",
              "addressLocality": "Bayonne",
              "addressRegion": "NJ",
              "addressCountry": "United States"
            }
          }
        }
        </script></head></html>"""

        content = parse_html(html)

        assert content.title == "Sanitation Team Member"
        assert content.description == "<p>Keep the facility safe.</p>"
        assert content.date_posted == "2026-08-15"
        assert content.locations == ["Bayonne, NJ, United States"]

    def test_repairs_talemetry_invalid_dollar_escape(self):
        html = r"""<html><head><script type="application/ld+json">
        {"@type":"JobPosting","title":"Program Manager",
         "description":"The salary range is \$106490 to \$177476."}
        </script></head></html>"""

        content = parse_html(html)

        assert content.title == "Program Manager"
        assert content.description == "The salary range is $106490 to $177476."

    def test_handles_empty_script(self):
        html = """<html><head>
        <script type="application/ld+json">  </script>
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert len(extractor.results) == 0

    def test_collects_meta_content(self):
        html = """<html><head>
        <meta name="gtm_tbcn_location" content="Bengaluru~Karnataka~India">
        </head></html>"""
        extractor = _JsonLdExtractor()
        extractor.feed(html)
        assert extractor.meta["gtm_tbcn_location"] == "Bengaluru~Karnataka~India"


class TestFindJobPosting:
    def test_direct_match(self):
        data = {"@type": "JobPosting", "title": "Engineer"}
        result = _find_job_posting(data)
        assert result["title"] == "Engineer"

    def test_in_list(self):
        data = [{"@type": "Organization"}, {"@type": "JobPosting", "title": "X"}]
        result = _find_job_posting(data)
        assert result["title"] == "X"

    def test_in_graph(self):
        data = {"@graph": [{"@type": "Organization"}, {"@type": "JobPosting", "title": "Y"}]}
        result = _find_job_posting(data)
        assert result["title"] == "Y"

    def test_type_as_list(self):
        data = {"@type": ["JobPosting", "Thing"], "title": "Z"}
        result = _find_job_posting(data)
        assert result["title"] == "Z"

    def test_not_found_dict(self):
        assert _find_job_posting({"@type": "Organization"}) is None

    def test_not_found_list(self):
        assert _find_job_posting([{"@type": "Organization"}]) is None

    def test_empty_list(self):
        assert _find_job_posting([]) is None

    def test_nested_graph(self):
        data = {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "WebPage"},
                {"@type": "JobPosting", "title": "Nested"},
            ],
        }
        result = _find_job_posting(data)
        assert result["title"] == "Nested"

    def test_pascalcase_keys_normalized(self):
        """CSOD-style PascalCase keys are normalized to camelCase."""
        data = {
            "@type": "JobPosting",
            "Title": "Manager",
            "Description": "A role",
            "DatePosted": "2026-01-01",
            "ValidThrough": "2026-06-01",
            "jobLocation": [
                {
                    "@type": "Place",
                    "Address": {
                        "@type": "PostalAddress",
                        "addressLocality": "Geneva",
                    },
                }
            ],
        }
        result = _find_job_posting(data)
        assert result["title"] == "Manager"
        assert result["description"] == "A role"
        assert result["datePosted"] == "2026-01-01"
        assert result["validThrough"] == "2026-06-01"
        # Nested keys are also normalized
        assert result["jobLocation"][0]["address"]["addressLocality"] == "Geneva"


class TestExtractLocations:
    def test_with_name(self):
        posting = {"jobLocation": {"name": "New York"}}
        assert _extract_locations(posting) == ["New York"]

    def test_with_address(self):
        posting = {
            "jobLocation": {
                "address": {
                    "addressLocality": "San Francisco",
                    "addressRegion": "CA",
                    "addressCountry": "US",
                }
            }
        }
        result = _extract_locations(posting)
        assert result == ["San Francisco, CA, US"]

    def test_prefers_address_when_place_name_is_hiring_organization(self):
        posting = {
            "hiringOrganization": {"name": "dormakaba Schweiz AG"},
            "jobLocation": {
                "name": "dormakaba Schweiz AG",
                "address": {
                    "addressLocality": "Wetzikon",
                    "addressRegion": "ZH",
                    "addressCountry": "CH",
                },
            },
        }

        assert _extract_locations(posting) == ["Wetzikon, ZH, CH"]

    def test_preserves_distinct_place_name_when_address_is_also_present(self):
        posting = {
            "hiringOrganization": {"name": "Example Corp"},
            "jobLocation": {
                "name": "Downtown Campus",
                "address": {
                    "addressLocality": "Austin",
                    "addressRegion": "TX",
                    "addressCountry": "US",
                },
            },
        }

        assert _extract_locations(posting) == ["Downtown Campus"]

    def test_with_string_address(self):
        posting = {
            "jobLocation": {
                "@type": "Place",
                "address": "東京都新宿区西新宿1-26-2\n新宿野村ビル48階",
            }
        }
        assert _extract_locations(posting) == ["東京都新宿区西新宿1-26-2 新宿野村ビル48階"]

    def test_multiple_locations(self):
        posting = {
            "jobLocation": [
                {"name": "NYC"},
                {"name": "London"},
            ]
        }
        result = _extract_locations(posting)
        assert result == ["NYC", "London"]

    def test_deduplicates_cleaned_locations_in_first_seen_order(self):
        posting = {
            "jobLocation": [
                {"name": " Berlin\t"},
                {"name": "London"},
                {"address": {"addressLocality": "Berlin"}},
                {"address": " London  "},
            ]
        }

        assert _extract_locations(posting) == ["Berlin", "London"]

    def test_preserves_case_distinct_locations(self):
        posting = {
            "jobLocation": [
                {"name": "Berlin"},
                {"name": "berlin"},
            ]
        }

        assert _extract_locations(posting) == ["Berlin", "berlin"]

    def test_none_returns_none(self):
        assert _extract_locations({}) is None

    def test_country_as_dict(self):
        posting = {
            "jobLocation": {
                "address": {
                    "addressCountry": {"name": "United States"},
                }
            }
        }
        result = _extract_locations(posting)
        assert result == ["United States"]

    def test_empty_location_returns_none(self):
        posting = {"jobLocation": {}}
        assert _extract_locations(posting) is None

    def test_non_dict_location_skipped(self):
        posting = {"jobLocation": ["string_location"]}
        assert _extract_locations(posting) is None


class TestMetaLocationFallback:
    def test_normalizes_talentbrew_tilde_location(self):
        assert _normalize_meta_locations("San Jose~California~United States") == [
            "San Jose, California, United States"
        ]

    def test_normalizes_semicolon_separated_locations(self):
        assert _normalize_meta_locations(
            "San Jose~California~United States; Chantilly~Virginia~United States"
        ) == [
            "San Jose, California, United States",
            "Chantilly, Virginia, United States",
        ]

    def test_normalizes_pipe_separated_talentbrew_locations(self):
        assert _normalize_meta_locations("Germany|Madrid~Spain|Munich~Bavaria~Germany") == [
            "Germany",
            "Madrid, Spain",
            "Munich, Bavaria, Germany",
        ]

    def test_parse_html_uses_meta_location_when_jsonld_missing_location(self):
        html = """<html><head>
        <meta name="gtm_tbcn_location" content="Bengaluru~Karnataka~India">
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Business Analyst","description":"<p>Analyze data</p>"}
        </script>
        </head></html>"""

        result = parse_html(html)

        assert result.locations == ["Bengaluru, Karnataka, India"]

    def test_parse_html_prefers_jsonld_location_over_meta_location(self):
        html = """<html><head>
        <meta name="gtm_tbcn_location" content="Bengaluru~Karnataka~India">
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Business Analyst",
         "jobLocation":{"name":"Bangalore, Karnataka, IN"}}
        </script>
        </head></html>"""

        result = parse_html(html)

        assert result.locations == ["Bangalore, Karnataka, IN"]

    def test_parse_html_can_ignore_incorrect_provider_locations(self):
        html = """<html><head>
        <meta name="job-city" content="Wrong meta fallback">
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Apprentice",
         "description":"<p>Learn precision manufacturing.</p>",
         "jobLocation":{"name":"VAT Group"}}
        </script>
        </head></html>"""

        result = parse_html(html, {"ignore_locations": True})

        assert result.title == "Apprentice"
        assert result.description == "<p>Learn precision manufacturing.</p>"
        assert result.locations is None

    def test_parse_html_can_ignore_incorrect_address_region(self):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Chassis Application Advisor",
         "jobLocation":{"address":{"addressLocality":"Dorking",
         "addressRegion":"Region Zürich / Schaffhausen",
         "addressCountry":"GB"}}}
        </script>
        </head></html>"""

        assert parse_html(html).locations == ["Dorking, Region Zürich / Schaffhausen, GB"]

        result = parse_html(html, {"ignore_address_region": True})

        assert result.locations == ["Dorking, GB"]

    def test_ignore_locations_also_covers_meta_only_jobs(self):
        html = """<html><head>
        <meta name="job-title" content="Apprentice">
        <meta name="job-description" content="&lt;p&gt;Learn precision manufacturing.&lt;/p&gt;">
        <meta name="job-city" content="Wrong provider city">
        </head></html>"""

        result = parse_html(html, {"ignore_locations": True})

        assert result.title == "Apprentice"
        assert result.description == "<p>Learn precision manufacturing.</p>"
        assert result.locations is None

    def test_parse_html_can_ignore_provider_generated_posting_date(self):
        html = """<html><head><script type="application/ld+json">
        {"@type":"JobPosting","title":"Housekeeper",
         "datePosted":"2024-08-11T08:17:42.806Z",
         "validThrough":"2027-08-11T08:17:42.806Z"}
        </script></head></html>"""

        assert parse_html(html).date_posted == "2024-08-11T08:17:42.806Z"
        assert parse_html(html).extras == {"valid_through": "2027-08-11T08:17:42.806Z"}

        result = parse_html(
            html,
            {"ignore_date_posted": True, "ignore_valid_through": True},
        )

        assert result.date_posted is None
        assert result.extras is None

    def test_parses_job_meta_fallback_with_secondary_locations(self):
        html = """<html><head>
        <meta name="job-title" content="Lead Product Owner">
        <meta name="job-workingmode" content="Hybrid">
        <meta name="job-posteddate" content="2026-05-28T04:51:22Z">
        <meta name="job-city" content="Manila">
        <meta name="job-region" content="">
        <meta name="job-country" content="Philippines">
        <meta name="job-id" content="21014070">
        <meta name="job-function" content="Product Management">
        <meta name="job-experiencelevel" content="Experienced">
        <meta name="job-secondarylocations"
              content="Hyderabad,India; Mumbai,India; New Delhi,India">
        <meta name="job-description"
              content="&lt;p&gt;Own the product roadmap &amp;amp; delivery.&lt;/p&gt;">
        </head></html>"""

        result = parse_html(html)

        assert result.title == "Lead Product Owner"
        assert result.description == "<p>Own the product roadmap &amp; delivery.</p>"
        assert result.locations == [
            "Manila, Philippines",
            "Hyderabad, India",
            "Mumbai, India",
            "New Delhi, India",
        ]
        assert result.job_location_type == "Hybrid"
        assert result.date_posted == "2026-05-28T04:51:22Z"
        assert result.extras is None
        assert result.metadata == {
            "requisition_id": "21014070",
            "job_function": "Product Management",
            "experience_level": "Experienced",
        }

    def test_job_meta_fallback_requires_full_description(self):
        html = """<html><head>
        <meta name="job-title" content="Engineer">
        <meta name="job-city" content="London">
        </head></html>"""

        assert parse_html(html).title is None
        assert can_handle([html]) is None

    def test_can_handle_job_meta_fallback_on_majority(self):
        job_html = """<html><head>
        <meta name="job-title" content="Engineer">
        <meta name="job-description" content="&lt;p&gt;Build systems&lt;/p&gt;">
        </head></html>"""

        assert can_handle([job_html, job_html, "<html></html>"]) == {}


class TestExtractSalary:
    def test_range(self):
        posting = {
            "baseSalary": {
                "currency": "USD",
                "value": {"minValue": 100000, "maxValue": 150000, "unitText": "YEAR"},
            }
        }
        result = _extract_salary(posting)
        assert result == {"currency": "USD", "min": 100000, "max": 150000, "unit": "year"}

    def test_single_value(self):
        posting = {
            "baseSalary": {
                "currency": "USD",
                "value": 100000,
            }
        }
        result = _extract_salary(posting)
        assert result == {"currency": "USD", "min": 100000, "max": 100000, "unit": None}

    def test_no_salary(self):
        assert _extract_salary({}) is None

    def test_non_dict_salary(self):
        assert _extract_salary({"baseSalary": "competitive"}) is None

    def test_float_value(self):
        posting = {
            "baseSalary": {
                "currency": "EUR",
                "value": 75000.50,
            }
        }
        result = _extract_salary(posting)
        assert result["min"] == 75000.50

    def test_empty_unit_text(self):
        posting = {
            "baseSalary": {
                "currency": "USD",
                "value": {"minValue": 100, "maxValue": 200, "unitText": ""},
            }
        }
        result = _extract_salary(posting)
        assert result["unit"] is None

    def test_scalar_value_with_outer_unit_hour(self):
        """Form B (scalar value) with ``unitText: HOUR`` on the OUTER
        MonetaryAmount object — schema.org allows this and the extractor
        must respect it instead of dropping the unit on the floor (#3226).
        """
        posting = {
            "baseSalary": {
                "@type": "MonetaryAmount",
                "currency": "USD",
                "unitText": "HOUR",
                "value": 25,
            }
        }
        result = _extract_salary(posting)
        assert result == {"currency": "USD", "min": 25, "max": 25, "unit": "hour"}

    def test_scalar_value_with_outer_unit_year(self):
        """Form B with ``unitText: YEAR`` on the outer object."""
        posting = {
            "baseSalary": {
                "@type": "MonetaryAmount",
                "currency": "USD",
                "unitText": "YEAR",
                "value": 120000,
            }
        }
        result = _extract_salary(posting)
        assert result == {"currency": "USD", "min": 120000, "max": 120000, "unit": "year"}

    def test_scalar_value_without_unit_text(self):
        """Form B without any ``unitText`` — unit stays ``None`` (current
        default, downstream falls back to description-heuristic salary
        period inference)."""
        posting = {
            "baseSalary": {
                "currency": "USD",
                "value": 100000,
            }
        }
        result = _extract_salary(posting)
        assert result == {"currency": "USD", "min": 100000, "max": 100000, "unit": None}

    def test_nested_unit_text_still_respected(self):
        """Form A is unchanged: nested ``unitText`` on the QuantitativeValue
        is the source of truth when present (regression guard)."""
        posting = {
            "baseSalary": {
                "@type": "MonetaryAmount",
                "currency": "USD",
                "value": {
                    "@type": "QuantitativeValue",
                    "minValue": 80000,
                    "maxValue": 120000,
                    "unitText": "YEAR",
                },
            }
        }
        result = _extract_salary(posting)
        assert result == {"currency": "USD", "min": 80000, "max": 120000, "unit": "year"}

    def test_nested_unit_text_wins_over_outer(self):
        """When both outer and nested ``unitText`` are present the nested
        one wins — it is the more specific qualifier of the actual value."""
        posting = {
            "baseSalary": {
                "@type": "MonetaryAmount",
                "currency": "USD",
                "unitText": "HOUR",  # outer
                "value": {
                    "@type": "QuantitativeValue",
                    "minValue": 80000,
                    "maxValue": 120000,
                    "unitText": "YEAR",  # nested — wins
                },
            }
        }
        result = _extract_salary(posting)
        assert result["unit"] == "year"

    def test_range_falls_back_to_outer_unit_when_nested_missing(self):
        """Form A variant: nested ``unitText`` absent, outer present — the
        outer unit fills in.  This matches the symmetric handling for
        scalar Form B (#3226)."""
        posting = {
            "baseSalary": {
                "@type": "MonetaryAmount",
                "currency": "USD",
                "unitText": "MONTH",
                "value": {
                    "@type": "QuantitativeValue",
                    "minValue": 5000,
                    "maxValue": 7000,
                },
            }
        }
        result = _extract_salary(posting)
        assert result == {"currency": "USD", "min": 5000, "max": 7000, "unit": "month"}


class TestTextOrList:
    def test_string(self):
        assert _text_or_list("Python") == ["Python"]

    def test_list(self):
        assert _text_or_list(["A", "B"]) == ["A", "B"]

    def test_empty_string(self):
        assert _text_or_list("  ") is None

    def test_none(self):
        assert _text_or_list(None) is None

    def test_empty_list(self):
        assert _text_or_list([]) is None

    def test_list_with_falsy(self):
        assert _text_or_list(["A", "", None]) == ["A"]


class TestStripHtml:
    def test_strips_tags(self):
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_no_tags(self):
        assert _strip_html("plain text") == "plain text"

    def test_self_closing_tags(self):
        assert _strip_html("Hello<br/>world") == "Helloworld"


class TestParsePosting:
    def test_full_posting(self):
        posting = {
            "@type": "JobPosting",
            "title": "Engineer",
            "description": "Great role",
            "jobLocation": {"name": "NYC"},
            "employmentType": "FULL_TIME",
            "jobLocationType": "TELECOMMUTE",
            "datePosted": "2024-01-01",
            "validThrough": "2024-12-31",
            "baseSalary": {
                "currency": "USD",
                "value": {"minValue": 100000, "maxValue": 150000, "unitText": "YEAR"},
            },
            "skills": ["Python", "SQL"],
            "responsibilities": "Build software",
            "qualifications": "CS degree",
        }
        result = _parse_posting(posting)
        assert result.title == "Engineer"
        assert result.description == "Great role"
        assert result.locations == ["NYC"]
        assert result.employment_type == "FULL_TIME"
        assert result.job_location_type == "TELECOMMUTE"
        assert result.date_posted == "2024-01-01"
        assert result.base_salary is not None
        assert result.extras is not None
        assert result.extras["valid_through"] == "2024-12-31"
        assert result.extras["skills"] == ["Python", "SQL"]
        assert result.extras["responsibilities"] == ["Build software"]
        assert result.extras["qualifications"] == ["CS degree"]

    def test_uses_name_fallback(self):
        posting = {"name": "Designer"}
        result = _parse_posting(posting)
        assert result.title == "Designer"

    def test_prefers_specific_employment_nature_from_schema_list(self):
        from src.core.enum_normalize import normalize_employment_type

        result = _parse_posting(
            {"title": "Polymechanic apprentice", "employmentType": ["INTERN", "FULL_TIME"]}
        )

        assert result.employment_type == "INTERN"
        assert normalize_employment_type(result.employment_type) == "internship"

    def test_preserves_full_or_part_schema_list(self):
        from src.core.enum_normalize import normalize_employment_type

        result = _parse_posting(
            {"title": "Flexible role", "employmentType": ["FULL_TIME", "PART_TIME"]}
        )

        assert result.employment_type == "FULL_TIME, PART_TIME"
        assert normalize_employment_type(result.employment_type) == "full_or_part"

    def test_title_takes_precedence_over_name(self):
        posting = {"title": "Engineer", "name": "Designer"}
        result = _parse_posting(posting)
        assert result.title == "Engineer"

    def test_decodes_entities_in_title_and_location(self):
        posting = {
            "title": "Visual Merchandising &amp; Space Planning Manager",
            "jobLocation": {"name": "D&#252;sseldorf &amp; K&#246;ln"},
        }
        result = _parse_posting(posting)
        assert result.title == "Visual Merchandising & Space Planning Manager"
        assert result.locations == ["Düsseldorf & Köln"]

    def test_decodes_double_escaped_scalar_entities(self):
        result = _parse_posting(
            {
                "title": "Senior I&amp;amp;C Engineer",
                "jobLocation": {"name": "R&amp;amp;D Campus"},
            }
        )

        assert result.title == "Senior I&C Engineer"
        assert result.locations == ["R&D Campus"]

    def test_decodes_talentbrew_double_escaped_description_line_breaks(self):
        posting = {
            "description": (
                "<p>Build medical devices &amp; diagnostics.</p>"
                "&amp;#xa;&amp;#x0A;&amp;#10;"
                "<p>Improve patient outcomes.</p>"
            )
        }

        result = _parse_posting(posting)

        assert result.description == (
            "<p>Build medical devices &amp; diagnostics.</p>\n\n\n<p>Improve patient outcomes.</p>"
        )

    def test_preserves_double_escaped_non_whitespace_description_entities(self):
        posting = {"description": "<p>Literal &amp;#60;markup&amp;#62; &amp;amp; text.</p>"}

        result = _parse_posting(posting)

        assert result.description == "<p>Literal &amp;#60;markup&amp;#62; &amp;amp; text.</p>"

    def test_falls_back_to_page_title_when_organization_suffix_matches(self):
        html = """
        <html><head><title>Pharmacy Technician - Full Time - Lewis Drug</title></head>
        <body><script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": null,
          "hiringOrganization": {"@type": "Organization", "name": "Lewis Drug"}
        }
        </script></body></html>
        """
        result = parse_html(html)
        assert result.title == "Pharmacy Technician - Full Time"

    def test_does_not_use_generic_page_title_without_matching_organization(self):
        html = """
        <html><head><title>Explore our careers</title></head>
        <body><script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "JobPosting",
          "title": null,
          "hiringOrganization": {"@type": "Organization", "name": "Example Corp"}
        }
        </script></body></html>
        """
        result = parse_html(html)
        assert result.title is None

    def test_education_requirements_fallback(self):
        posting = {"educationRequirements": "Bachelor's degree"}
        result = _parse_posting(posting)
        assert result.extras is not None
        assert result.extras["qualifications"] == ["Bachelor's degree"]

    def test_minimal_posting(self):
        result = _parse_posting({})
        assert isinstance(result, JobContent)
        assert result.title is None


class TestScrape:
    async def test_extracts_from_page(self):
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Engineer", "description": "Build stuff"}
        </script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title == "Engineer"
            assert result.description == "Build stuff"

    async def test_defaults_by_url_fill_only_matching_missing_fields(self):
        canonical = "https://example.com/job/locationless"
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@type":"JobPosting","title":"Structural Designer",
         "description":"<p>Design advanced facilities.</p>"}
        </script>
        </head></html>"""
        config = {
            "defaults_by_url": {
                canonical: {"locations": ["Albany, New York, United States"]},
                "https://example.com/job/other": {"locations": ["London"]},
            }
        }

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=page_html))
        ) as client:
            result = await scrape(canonical, config, client)

        assert result.title == "Structural Designer"
        assert result.description == "<p>Design advanced facilities.</p>"
        assert result.locations == ["Albany, New York, United States"]

    async def test_defaults_by_url_never_replace_extracted_fields(self):
        canonical = "https://example.com/job/located"
        page_html = """<script type="application/ld+json">
        {"@type":"JobPosting","title":"Engineer",
         "jobLocation":{"name":"Cary, North Carolina, United States"}}
        </script>"""
        config = {"defaults_by_url": {canonical: {"locations": ["Wrong location"]}}}

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=page_html))
        ) as client:
            result = await scrape(canonical, config, client)

        assert result.locations == ["Cary, North Carolina, United States"]

    @pytest.mark.parametrize(
        "defaults_by_url",
        [
            [],
            {42: {"locations": ["London"]}},
            {"https://example.com/job/42": ["London"]},
            {"https://example.com/job/42": {"unknown": "value"}},
        ],
    )
    async def test_defaults_by_url_rejects_invalid_config(self, defaults_by_url):
        page_html = """<script type="application/ld+json">
        {"@type":"JobPosting","title":"Engineer"}
        </script>"""
        config = {"defaults_by_url": defaults_by_url}
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=page_html))
        ) as client:
            with pytest.raises(ValueError, match="defaults_by_url"):
                await scrape("https://example.com/job/42", config, client)

    async def test_no_jsonld_returns_empty(self):
        def handler(request):
            return httpx.Response(200, text="<html><body>No JSON-LD</body></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title is None

    async def test_multiple_blocks_finds_job_posting(self):
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "Organization", "name": "Acme"}</script>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "Dev"}</script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title == "Dev"

    async def test_graph_format(self):
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@context": "https://schema.org", "@graph": [
            {"@type": "WebPage"},
            {"@type": "JobPosting", "title": "GraphJob"}
        ]}
        </script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title == "GraphJob"

    async def test_render_uses_playwright(self):
        """When render=true, scrape should use browser rendering instead of HTTP."""
        from unittest.mock import AsyncMock, patch

        page_html = """<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Rendered"}
        </script>
        </head></html>"""

        with patch("src.shared.browser.render", new_callable=AsyncMock) as mock_render:
            mock_render.return_value = page_html
            transport = httpx.MockTransport(lambda r: httpx.Response(500))
            async with httpx.AsyncClient(transport=transport) as client:
                result = await scrape(
                    "https://example.com/job",
                    {"render": True},
                    client,
                    pw="fake_pw",
                )
                assert result.title == "Rendered"
                mock_render.assert_called_once_with("https://example.com/job", {}, pw="fake_pw")

    async def test_render_forwards_proxy_to_browser(self):
        """A proxy-enabled browser scraper must not silently use direct egress."""
        from unittest.mock import AsyncMock, patch

        page_html = """<script type="application/ld+json">
        {"@type": "JobPosting", "title": "Proxied"}
        </script>"""

        with patch("src.shared.browser.render", new_callable=AsyncMock) as mock_render:
            mock_render.return_value = page_html
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: None)) as client:
                result = await scrape(
                    "https://example.com/job",
                    {"render": True, "proxy": True},
                    client,
                    pw="fake_pw",
                )

        assert result.title == "Proxied"
        mock_render.assert_awaited_once_with(
            "https://example.com/job",
            {"proxy": True},
            pw="fake_pw",
        )

    async def test_render_false_uses_http(self):
        """When render is false/absent, scrape should use static HTTP."""
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@type": "JobPosting", "title": "Static"}
        </script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {"render": False}, client)
            assert result.title == "Static"

    async def test_pascalcase_csod_style(self):
        """CSOD-style PascalCase JSON-LD is extracted correctly."""
        page_html = """<html><head>
        <script type="application/ld+json">
        {"@context":"http://schema.org","@type":"JobPosting",
         "Title":"Senior Engineer","Description":"<p>Build things</p>",
         "DatePosted":"2026-01-01","ValidThrough":"2026-06-01",
         "jobLocation":[{"@type":"Place","Address":{"@type":"PostalAddress",
         "addressLocality":"Geneva","addressCountry":"CH"}}],
         "HiringOrganization":{"@type":"Organization","Name":"IATA"}}
        </script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert result.title == "Senior Engineer"
            assert result.description == "<p>Build things</p>"
            assert result.locations == ["Geneva, CH"]
            assert result.date_posted == "2026-01-01"


class TestProbe:
    async def test_found(self):
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting"}</script>
        </head></html>"""

        def handler(request):
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await probe("https://example.com/job", client) is True

    async def test_not_found(self):
        def handler(request):
            return httpx.Response(200, text="<html></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await probe("https://example.com/job", client) is False

    async def test_error_returns_false(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await probe("https://example.com/job", client) is False


class TestFetchRetry403:
    """``_fetch_html`` retries once on 403 to tolerate soft-WAF warmups."""

    async def test_retries_once_on_403_then_succeeds(self):
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "T"}</script>
        </head></html>"""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(403, text="blocked")
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
            assert calls["n"] == 2
            assert result.title == "T"

    async def test_configured_headers_are_cleaned_and_sent_on_every_attempt(self):
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "T"}</script>
        </head></html>"""
        seen: list[httpx.Headers] = []

        def handler(request):
            seen.append(request.headers)
            if len(seen) == 1:
                return httpx.Response(403, text="blocked")
            return httpx.Response(200, text=page_html)

        config = {
            "request_headers": {
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                "Host": "untrusted.example",
                "Connection": "close",
            }
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", config, client)

        assert result.title == "T"
        assert len(seen) == 2
        assert all(headers["accept"] == "application/json" for headers in seen)
        assert all(headers["x-requested-with"] == "XMLHttpRequest" for headers in seen)
        assert all(headers["host"] == "example.com" for headers in seen)
        assert all(headers["connection"] == "keep-alive" for headers in seen)

    async def test_does_not_retry_on_200(self):
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "T"}</script>
        </head></html>"""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await scrape("https://example.com/job", {}, client)
            assert calls["n"] == 1

    async def test_does_not_retry_on_410(self):
        """4xx statuses other than 403 should surface immediately so the pipeline
        can distinguish a permanently-gone job from a transient block."""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(410, text="gone")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                await scrape("https://example.com/job", {}, client)
                raise AssertionError("expected HTTPStatusError")
            except httpx.HTTPStatusError as e:
                assert e.response.status_code == 410
        assert calls["n"] == 1

    async def test_raises_if_retry_also_403(self):
        """A persistent 403 still raises after the single retry."""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(403, text="blocked")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                await scrape("https://example.com/job", {}, client)
                raise AssertionError("expected HTTPStatusError")
            except httpx.HTTPStatusError as e:
                assert e.response.status_code == 403
        assert calls["n"] == 2

    async def test_retry_carries_challenge_cookies(self):
        """The whole point of retrying on the same client is that challenge
        cookies set by the first response are attached to the retry. This
        pins that invariant — the RTX soft-WAF pattern only recovers if the
        challenge cookie set on the 403 makes it back on the retry."""
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "T"}</script>
        </head></html>"""
        calls: list[str] = []  # cookie header captured per call

        def handler(request):
            calls.append(request.headers.get("cookie", ""))
            if len(calls) == 1:
                # First response: 403 + sets a challenge cookie
                resp = httpx.Response(
                    403,
                    text="blocked",
                    headers={"set-cookie": "challenge=solved; Path=/"},
                )
                return resp
            return httpx.Response(200, text=page_html)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape("https://example.com/job", {}, client)
        assert len(calls) == 2
        # First call has no cookies, second call carries the challenge cookie
        assert calls[0] == ""
        assert "challenge=solved" in calls[1]
        assert result.title == "T"

    async def test_avature_406_retries_and_recovers(self):
        page_html = """<html><head>
        <script type="application/ld+json">{"@type": "JobPosting", "title": "T"}</script>
        </head></html>"""
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            status = 406 if calls["n"] == 1 else 200
            return httpx.Response(status, text=page_html if status == 200 else "busy")

        url = "https://jobs.ea.com/en_US/careers/JobDetail/Role/123"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(url, {}, client)

        assert calls["n"] == 2
        assert result.title == "T"

    async def test_generic_406_fails_without_retry(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(406, text="not acceptable")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await scrape("https://example.com/jobs/123", {}, client)

        assert calls["n"] == 1


class TestMetaCareersFixture:
    """Regression test for #2963 — metacareers.com requires browser rendering.

    Static fetches of ``www.metacareers.com/profile/job_details/...`` from
    the Hetzner crawler return HTTP 400 from a Facebook WAF; only Playwright
    renders that the WAF accepts as a real browser produce JSON-LD. This
    test pins the parser invariant via a captured fixture (no network).
    """

    def test_fixture_exists(self):
        path = FIXTURES / "jsonld_meta_software_engineer.html"
        assert path.exists(), f"missing fixture: {path}"

    def test_parses_meta_jobposting_jsonld(self):
        from src.core.scrapers.jsonld import parse_html

        html = (FIXTURES / "jsonld_meta_software_engineer.html").read_text()
        content = parse_html(html)
        assert content.title == "Software Engineer, Infrastructure"
        assert content.description and len(content.description) > 500
        # Multi-location: schema.org allows jobLocation as a list — we expect
        # all 9 to be picked up and formatted as "City, State".
        assert content.locations is not None and len(content.locations) >= 5
        assert "Sunnyvale, CA" in content.locations
        assert "Remote, US" in content.locations
        assert content.employment_type == "Full-time"

    async def test_render_true_uses_playwright_for_meta(self):
        """With ``render: true`` the meta scraper bypasses the static HTTP path
        entirely (which returns 400 in production) and runs against the
        browser-rendered HTML. The fixture stands in for the browser output.
        """
        from unittest.mock import AsyncMock, patch

        html = (FIXTURES / "jsonld_meta_software_engineer.html").read_text()
        with patch("src.shared.browser.render", new_callable=AsyncMock) as mock_render:
            mock_render.return_value = html
            # The static path would 500 here — proves we never hit it.
            transport = httpx.MockTransport(lambda r: httpx.Response(500))
            async with httpx.AsyncClient(transport=transport) as client:
                result = await scrape(
                    "https://www.metacareers.com/profile/job_details/677160418622314",
                    {"render": True, "wait": "networkidle", "timeout": 45000},
                    client,
                    pw="fake_pw",
                )
        assert result.title == "Software Engineer, Infrastructure"
        assert result.description and "Meta is seeking" in result.description
        # Browser keys are forwarded; non-browser keys (none here) would be filtered.
        mock_render.assert_called_once()
        call_kwargs = mock_render.call_args.args[1]
        assert call_kwargs.get("wait") == "networkidle"
        assert call_kwargs.get("timeout") == 45000


class TestNeuraRoboticsFixture:
    """Regression test for #2963 — jobs.neura-robotics.com (TalentsConnect ATS).

    The page DOES contain JobPosting JSON-LD in static HTML, but in production
    the Cloudflare-fronted host rejects the crawler's default UA from Hetzner
    egress, so descriptions stay empty for 86.8% of postings. Switching to
    ``render: true`` provides a full browser fingerprint that passes
    Cloudflare's bot detection. This fixture pins the parser invariant.
    """

    def test_fixture_exists(self):
        path = FIXTURES / "jsonld_neura_robotics_manager.html"
        assert path.exists(), f"missing fixture: {path}"

    def test_parses_neura_jobposting_jsonld(self):
        from src.core.scrapers.jsonld import parse_html

        html = (FIXTURES / "jsonld_neura_robotics_manager.html").read_text()
        content = parse_html(html)
        assert content.title == "Manager Ecosystem Integration & Certification (Human)"
        assert content.description and len(content.description) > 1000
        # NeuraVerse appears in the role description.
        assert "NeuraVerse" in (content.description or "")
        assert content.locations == ["Metzingen, DE"]

    async def test_render_true_uses_playwright_for_neura(self):
        """``render: true`` on jobs.neura-robotics.com routes through Playwright."""
        from unittest.mock import AsyncMock, patch

        html = (FIXTURES / "jsonld_neura_robotics_manager.html").read_text()
        with patch("src.shared.browser.render", new_callable=AsyncMock) as mock_render:
            mock_render.return_value = html
            transport = httpx.MockTransport(lambda r: httpx.Response(500))
            async with httpx.AsyncClient(transport=transport) as client:
                result = await scrape(
                    (
                        "https://jobs.neura-robotics.com/offer/"
                        "manager-ecosystem-integration-certi/"
                        "00df0928-b013-448c-b3dd-6c6fb80eadc2"
                    ),
                    {"render": True, "wait": "networkidle", "timeout": 45000},
                    client,
                    pw="fake_pw",
                )
        assert result.title == "Manager Ecosystem Integration & Certification (Human)"
        assert result.locations == ["Metzingen, DE"]
        assert result.description and len(result.description) > 1000
        mock_render.assert_called_once()
