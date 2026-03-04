"""Unit tests for src/shared/api_sniff — pure logic, no Playwright."""

from __future__ import annotations

import pytest

from src.shared.api_sniff import (
    ArrayCandidate,
    Exchange,
    JobListResult,
    PaginationInfo,
    auto_map_fields,
    detect_job_list,
    extract_items,
    extract_urls,
    find_arrays,
    find_total_count,
    find_url_field,
    infer_pagination,
    score_candidate,
    set_body_param,
    set_url_param,
)


def _make_exchange(url="https://example.com/api/jobs", method="GET", body=None,
                   post_data=None, phase="load"):
    return Exchange(
        method=method, url=url,
        request_headers={"accept": "application/json"},
        post_data=post_data, status=200,
        body=body, content_type="application/json",
        phase=phase,
    )


class TestFindArrays:
    def test_top_level_array(self):
        data = [{"id": 1}, {"id": 2}, {"id": 3}]
        result = find_arrays(data)
        assert len(result) == 1
        assert result[0][0] == "$"
        assert len(result[0][1]) == 3

    def test_nested_array(self):
        data = {"results": {"jobs": [{"id": 1}, {"id": 2}, {"id": 3}]}}
        result = find_arrays(data)
        assert len(result) == 1
        assert result[0][0] == "results.jobs"

    def test_too_few_items(self):
        data = {"items": [{"id": 1}, {"id": 2}]}
        result = find_arrays(data)
        assert len(result) == 0

    def test_non_dict_items_skipped(self):
        data = {"items": [1, 2, 3, 4]}
        result = find_arrays(data)
        assert len(result) == 0

    def test_multiple_arrays(self):
        data = {
            "jobs": [{"id": 1}, {"id": 2}, {"id": 3}],
            "categories": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
        }
        result = find_arrays(data)
        assert len(result) == 2

    def test_empty_object(self):
        assert find_arrays({}) == []
        assert find_arrays(None) == []

    def test_deep_nesting(self):
        data = {"a": {"b": {"c": [{"x": 1}, {"x": 2}, {"x": 3}]}}}
        result = find_arrays(data)
        assert result[0][0] == "a.b.c"


class TestFindUrlField:
    def test_by_name(self):
        items = [
            {"title": "Dev", "url": "https://example.com/1"},
            {"title": "PM", "url": "https://example.com/2"},
            {"title": "QA", "url": "https://example.com/3"},
        ]
        assert find_url_field(items) == "url"

    def test_by_name_slug(self):
        items = [
            {"title": "Dev", "slug": "/jobs/dev"},
            {"title": "PM", "slug": "/jobs/pm"},
            {"title": "QA", "slug": "/jobs/qa"},
        ]
        assert find_url_field(items) == "slug"

    def test_by_value(self):
        items = [
            {"title": "Dev", "page": "https://example.com/1"},
            {"title": "PM", "page": "https://example.com/2"},
            {"title": "QA", "page": "https://example.com/3"},
        ]
        assert find_url_field(items) == "page"

    def test_no_match(self):
        items = [
            {"title": "Dev", "score": 10},
            {"title": "PM", "score": 20},
            {"title": "QA", "score": 30},
        ]
        assert find_url_field(items) is None

    def test_empty(self):
        assert find_url_field([]) is None


class TestFindTotalCount:
    def test_sibling(self):
        body = {"total": 150, "results": [{"id": 1}]}
        assert find_total_count(body, "results") == 150

    def test_nested_sibling(self):
        body = {"data": {"total": 50, "items": [{"id": 1}]}}
        assert find_total_count(body, "data.items") == 50

    def test_top_level_fallback(self):
        body = {"totalCount": 200, "nested": {"jobs": [{"id": 1}]}}
        assert find_total_count(body, "nested.jobs") == 200

    def test_not_found(self):
        body = {"items": [{"id": 1}]}
        assert find_total_count(body, "items") is None

    def test_non_dict(self):
        assert find_total_count([1, 2, 3], "$") is None


class TestScoreCandidate:
    def test_high_score_with_url_and_title(self):
        items = [
            {"title": "Dev", "url": "https://example.com/1", "location": "NYC"},
            {"title": "PM", "url": "https://example.com/2", "location": "SF"},
            {"title": "QA", "url": "https://example.com/3", "location": "LA"},
        ]
        ex = _make_exchange(
            url="https://example.com/api/jobs",
            body={"total": 100, "jobs": items},
        )
        cand = ArrayCandidate(exchange=ex, json_path="jobs", items=items)
        score = score_candidate(cand, "https://example.com/careers")
        assert score >= 50  # URL(30) + title(15) + job keyword(10) + total(10) + uniformity(10) + origin(5)

    def test_low_score_few_keys(self):
        items = [{"a": 1}, {"a": 2}, {"a": 3}]
        ex = _make_exchange(
            url="https://other.com/data",
            body=items,
        )
        cand = ArrayCandidate(exchange=ex, json_path="$", items=items)
        score = score_candidate(cand, "https://example.com/careers")
        assert score < 10  # few keys penalty, no URL/title match

    def test_job_keyword_in_url(self):
        items = [{"title": "Dev"}, {"title": "PM"}, {"title": "QA"}]
        ex = _make_exchange(
            url="https://example.com/api/careers/list",
            body=items,
        )
        cand = ArrayCandidate(exchange=ex, json_path="$", items=items)
        score = score_candidate(cand, "https://example.com/careers")
        assert score > 0


class TestDetectJobList:
    def test_detects_best_array(self):
        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "x"},
            {"title": "PM", "url": "/jobs/2", "desc": "y"},
            {"title": "QA", "url": "/jobs/3", "desc": "z"},
        ]
        ex = _make_exchange(
            url="https://example.com/api/jobs",
            body={"total": 100, "results": items},
        )
        result = detect_job_list([ex], "https://example.com/careers")
        assert result is not None
        assert result.url_field == "url"
        assert result.total_count == 100

    def test_returns_none_no_exchanges(self):
        assert detect_job_list([], "https://example.com") is None

    def test_returns_none_low_score(self):
        items = [{"x": 1}, {"x": 2}, {"x": 3}]
        ex = _make_exchange(
            url="https://other.com/config",
            body=items,
        )
        result = detect_job_list([ex], "https://example.com")
        assert result is None


class TestExtractUrls:
    def test_with_url_field(self):
        items = [
            {"url": "/jobs/1"},
            {"url": "/jobs/2"},
        ]
        urls = extract_urls(items, "url", "https://example.com")
        assert urls == ["https://example.com/jobs/1", "https://example.com/jobs/2"]

    def test_absolute_urls(self):
        items = [{"url": "https://other.com/1"}]
        urls = extract_urls(items, "url", "https://example.com")
        assert urls == ["https://other.com/1"]

    def test_no_url_field_fallback(self):
        items = [{"page": "https://example.com/job/1"}]
        urls = extract_urls(items, None, "https://example.com")
        assert len(urls) == 1

    def test_empty(self):
        assert extract_urls([], "url", "https://example.com") == []


class TestInferPagination:
    def test_query_param_diff(self):
        ex1 = _make_exchange(url="https://example.com/api/jobs?offset=0&limit=20", phase="load")
        ex2 = _make_exchange(url="https://example.com/api/jobs?offset=20&limit=20", phase="interaction")
        result = infer_pagination([ex1, ex2], "https://example.com/api/jobs?offset=0&limit=20", 20)
        assert result is not None
        assert result.param_name == "offset"
        assert result.style == "offset"
        assert result.increment == 20

    def test_page_style(self):
        ex1 = _make_exchange(url="https://example.com/api/jobs?page=1", phase="load")
        ex2 = _make_exchange(url="https://example.com/api/jobs?page=2", phase="interaction")
        result = infer_pagination([ex1, ex2], "https://example.com/api/jobs?page=1", 20)
        assert result is not None
        assert result.param_name == "page"
        assert result.style == "page"
        assert result.increment == 1

    def test_body_diff(self):
        ex1 = _make_exchange(
            url="https://example.com/api/jobs", method="POST",
            post_data='{"offset": 0, "limit": 20}', phase="load",
        )
        ex2 = _make_exchange(
            url="https://example.com/api/jobs", method="POST",
            post_data='{"offset": 20, "limit": 20}', phase="interaction",
        )
        result = infer_pagination([ex1, ex2], "https://example.com/api/jobs", 20)
        assert result is not None
        assert result.param_name == "offset"
        assert result.location == "body"

    def test_single_exchange_guessing(self):
        ex = _make_exchange(url="https://example.com/api/jobs?offset=0&limit=20")
        result = infer_pagination([ex], "https://example.com/api/jobs?offset=0&limit=20", 20)
        assert result is not None
        assert result.param_name == "offset"
        assert result.observed_value == 0

    def test_no_pagination(self):
        ex = _make_exchange(url="https://example.com/api/jobs")
        result = infer_pagination([ex], "https://example.com/api/jobs", 20)
        assert result is None


class TestAutoMapFields:
    def test_standard_names(self):
        items = [
            {"title": "Dev", "description": "HTML here", "location": "NYC"},
            {"title": "PM", "description": "Also HTML", "location": "SF"},
        ]
        mapping = auto_map_fields(items)
        assert mapping["title"] == "title"
        assert mapping["description"] == "description"
        assert mapping.get("locations") == "location"

    def test_location_array_of_objects(self):
        items = [
            {"title": "Dev", "offices": [{"name": "NYC"}, {"name": "SF"}]},
            {"title": "PM", "offices": [{"name": "LA"}]},
        ]
        mapping = auto_map_fields(items)
        assert mapping.get("locations") == "offices[].name"

    def test_location_array_of_strings(self):
        items = [
            {"title": "Dev", "locations": ["NYC", "SF"]},
            {"title": "PM", "locations": ["LA"]},
        ]
        mapping = auto_map_fields(items)
        assert mapping.get("locations") == "locations"

    def test_metadata_team(self):
        items = [
            {"title": "Dev", "department": "Engineering"},
            {"title": "PM", "department": "Product"},
        ]
        mapping = auto_map_fields(items)
        assert mapping.get("metadata.team") == "department"

    def test_empty_items(self):
        assert auto_map_fields([]) == {}

    def test_employment_type(self):
        items = [
            {"title": "Dev", "employmentType": "Full-time"},
            {"title": "PM", "employmentType": "Part-time"},
        ]
        mapping = auto_map_fields(items)
        assert mapping["employment_type"] == "employmentType"


class TestSetUrlParam:
    def test_set_existing(self):
        result = set_url_param("https://example.com/api?page=1", "page", 2)
        assert "page=2" in result

    def test_add_new(self):
        result = set_url_param("https://example.com/api", "page", 1)
        assert "page=1" in result


class TestSetBodyParam:
    def test_set_value(self):
        result = set_body_param('{"offset": 0}', "offset", 20)
        import json
        parsed = json.loads(result)
        assert parsed["offset"] == 20

    def test_nested(self):
        result = set_body_param('{"paging": {"offset": 0}}', "paging.offset", 20)
        import json
        parsed = json.loads(result)
        assert parsed["paging"]["offset"] == 20


class TestExtractItems:
    def test_exact_path(self):
        data = {"results": {"jobs": [{"id": 1}, {"id": 2}, {"id": 3}]}}
        items = extract_items(data, "results.jobs")
        assert len(items) == 3

    def test_fallback_largest(self):
        data = {
            "small": [{"id": 1}, {"id": 2}, {"id": 3}],
            "large": [{"id": i} for i in range(10)],
        }
        items = extract_items(data, "nonexistent")
        assert len(items) == 10

    def test_no_arrays(self):
        assert extract_items({"key": "value"}, "key") == []
