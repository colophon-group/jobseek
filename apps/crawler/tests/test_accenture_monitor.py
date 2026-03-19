"""Tests for the dedicated Accenture monitor."""

from __future__ import annotations

from src.core.monitors.accenture import (
    FINDJOBS,
    JOBSEARCH,
    _build_body,
    _discover_values,
    _make_filter,
    _parse_findjobs_job,
    _parse_items,
    _parse_jobsearch_job,
    _url_key_for,
)

# ---------------------------------------------------------------------------
# _build_body
# ---------------------------------------------------------------------------


class TestBuildBody:
    def test_basic_fields(self):
        body = _build_body(0, "USA", "en", "us-en")
        assert 'name="startIndex"\r\n\r\n0' in body
        assert 'name="maxResultSize"\r\n\r\n500' in body
        assert 'name="jobCountry"\r\n\r\nUSA' in body
        assert 'name="jobLanguage"\r\n\r\nen' in body
        assert 'name="countrySite"\r\n\r\nus-en' in body
        assert 'name="sortBy"\r\n\r\n2' in body
        assert 'name="totalHits"\r\n\r\ntrue' in body

    def test_offset(self):
        body = _build_body(500, "India", "en", "in-en")
        assert 'name="startIndex"\r\n\r\n500' in body

    def test_no_filters_by_default(self):
        body = _build_body(0, "USA", "en", "us-en")
        assert "jobFilters" not in body

    def test_with_filters(self):
        filters = [{"fieldName": "businessArea.keyword", "items": ["Technology"]}]
        body = _build_body(0, "USA", "en", "us-en", filters=filters)
        assert "jobFilters" in body
        # Parse the jobFilters value from multipart
        assert "Technology" in body

    def test_multipart_format(self):
        body = _build_body(0, "USA", "en", "us-en")
        # Should start with delimiter and end with closing delimiter
        assert body.startswith("------FormBoundary\r\n")
        assert body.endswith("------FormBoundary--")

    def test_unicode_country(self):
        body = _build_body(0, "日本", "ja", "jp-ja")
        assert 'name="jobCountry"\r\n\r\n日本' in body


# ---------------------------------------------------------------------------
# _parse_findjobs_job
# ---------------------------------------------------------------------------


class TestParseFindjobsJob:
    def test_basic(self):
        raw = {
            "guid": "abc-123",
            "title": "Software Engineer",
            "jobDescription": "<p>Great job</p>",
            "location": "New York, NY",
            "remoteType": "Remote",
            "postedDate": "2025-01-15",
            "businessArea": "Technology",
            "careerLevel": "Senior",
        }
        job = _parse_findjobs_job(raw, "us-en")
        assert job is not None
        assert job.url == "https://www.accenture.com/us-en/careers/jobdetails?id=abc-123"
        assert job.title == "Software Engineer"
        assert job.description == "<p>Great job</p>"
        assert job.locations == ["New York, NY"]
        assert job.job_location_type == "Remote"
        assert job.date_posted == "2025-01-15"
        assert job.metadata == {
            "businessArea": "Technology",
            "careerLevel": "Senior",
            "guid": "abc-123",
        }

    def test_missing_guid_returns_none(self):
        assert _parse_findjobs_job({}, "us-en") is None
        assert _parse_findjobs_job({"title": "No GUID"}, "us-en") is None

    def test_location_as_list(self):
        raw = {"guid": "x", "location": ["Berlin", "Munich"]}
        job = _parse_findjobs_job(raw, "de-de")
        assert job.locations == ["Berlin", "Munich"]

    def test_no_optional_fields(self):
        raw = {"guid": "x"}
        job = _parse_findjobs_job(raw, "us-en")
        assert job is not None
        assert job.title is None
        assert job.description is None
        assert job.locations is None
        assert job.metadata == {"guid": "x"}

    def test_site_in_url(self):
        raw = {"guid": "test-id"}
        job = _parse_findjobs_job(raw, "in-en")
        assert "in-en" in job.url


# ---------------------------------------------------------------------------
# _parse_jobsearch_job
# ---------------------------------------------------------------------------


class TestParseJobsearchJob:
    def test_basic(self):
        raw = {
            "jobDetailUrl": "https://www.accenture.com/br-pt/careers/jobdetails?id=xyz",
            "title": "Analista",
            "jobCityState": "São Paulo, SP",
            "postedDate": "2025-03-01",
        }
        job = _parse_jobsearch_job(raw)
        assert job is not None
        assert job.url == "https://www.accenture.com/br-pt/careers/jobdetails?id=xyz"
        assert job.title == "Analista"
        assert job.locations == ["São Paulo, SP"]
        assert job.date_posted == "2025-03-01"

    def test_missing_url_returns_none(self):
        assert _parse_jobsearch_job({}) is None

    def test_relative_url_made_absolute(self):
        raw = {"jobDetailUrl": "/fr-fr/careers/jobdetails?id=123"}
        job = _parse_jobsearch_job(raw)
        assert job.url == "https://www.accenture.com/fr-fr/careers/jobdetails?id=123"

    def test_no_description(self):
        """jobsearch/result items don't include descriptions."""
        raw = {"jobDetailUrl": "https://example.com/job"}
        job = _parse_jobsearch_job(raw)
        assert job.description is None


# ---------------------------------------------------------------------------
# _discover_values
# ---------------------------------------------------------------------------


class TestDiscoverValues:
    def test_extracts_unique_values(self):
        items = [
            {"businessArea": "Technology"},
            {"businessArea": "Operations"},
            {"businessArea": "Technology"},
            {"businessArea": "Song"},
        ]
        result = _discover_values(items, "businessArea")
        assert result == {"Technology", "Operations", "Song"}

    def test_skips_missing_field(self):
        items = [
            {"businessArea": "Tech"},
            {"other": "field"},
            {},
        ]
        result = _discover_values(items, "businessArea")
        assert result == {"Tech"}

    def test_empty_items(self):
        assert _discover_values([], "businessArea") == set()


# ---------------------------------------------------------------------------
# _make_filter
# ---------------------------------------------------------------------------


class TestMakeFilter:
    def test_format(self):
        f = _make_filter("businessArea", "Technology")
        assert f == {
            "fieldName": "businessArea.keyword",
            "items": ["Technology"],
            "multiSelect": False,
        }


# ---------------------------------------------------------------------------
# _url_key_for
# ---------------------------------------------------------------------------


class TestUrlKeyFor:
    def test_findjobs(self):
        assert _url_key_for(FINDJOBS) == "guid"

    def test_jobsearch(self):
        assert _url_key_for(JOBSEARCH) == "jobDetailUrl"


# ---------------------------------------------------------------------------
# _parse_items
# ---------------------------------------------------------------------------


class TestParseItems:
    def test_findjobs_endpoint(self):
        raw_items = [
            {"guid": "a", "title": "Job A"},
            {"guid": "b", "title": "Job B"},
            {"no_guid": True},  # should be skipped
        ]
        jobs = _parse_items(raw_items, FINDJOBS, "us-en")
        assert len(jobs) == 2
        assert jobs[0].title == "Job A"
        assert jobs[1].title == "Job B"

    def test_jobsearch_endpoint(self):
        raw_items = [
            {"jobDetailUrl": "https://example.com/a", "title": "Job A"},
            {"no_url": True},
        ]
        jobs = _parse_items(raw_items, JOBSEARCH, "fr-fr")
        assert len(jobs) == 1
        assert jobs[0].title == "Job A"


# ---------------------------------------------------------------------------
# Dedup logic (integration-style)
# ---------------------------------------------------------------------------


class TestDedup:
    def test_guid_dedup(self):
        """Items with the same guid should be deduplicated."""
        items = [
            {"guid": "a", "title": "First"},
            {"guid": "b", "title": "Second"},
            {"guid": "a", "title": "Duplicate"},
        ]
        seen: set[str] = set()
        deduped: list[dict] = []
        for item in items:
            k = item.get("guid")
            if k and k not in seen:
                seen.add(k)
                deduped.append(item)
        assert len(deduped) == 2
        assert deduped[0]["title"] == "First"
        assert deduped[1]["title"] == "Second"
