"""Tests for src.shared.embedded — HTML → JSON extraction engine."""

from __future__ import annotations

import json

from src.shared.embedded import (
    extract_by_pattern,
    extract_by_variable,
    extract_script_by_id,
    find_json_extent,
    parse_embedded,
)

# ---------------------------------------------------------------------------
# find_json_extent
# ---------------------------------------------------------------------------


class TestFindJsonExtent:
    def test_simple_object(self):
        text = '{"a": 1}'
        assert find_json_extent(text, 0) == len(text)

    def test_nested_objects(self):
        text = '{"a": {"b": {"c": 1}}}'
        assert find_json_extent(text, 0) == len(text)

    def test_nested_arrays(self):
        text = "[[1, 2], [3, [4, 5]]]"
        assert find_json_extent(text, 0) == len(text)

    def test_strings_with_brackets(self):
        text = '{"key": "value with { and } inside"}'
        assert find_json_extent(text, 0) == len(text)

    def test_escaped_chars_in_strings(self):
        text = r'{"key": "escaped \" quote"}'
        assert find_json_extent(text, 0) == len(text)

    def test_string_with_escaped_backslash(self):
        text = r'{"key": "path\\to\\file"}'
        assert find_json_extent(text, 0) == len(text)

    def test_incomplete_returns_none(self):
        text = '{"a": 1'
        assert find_json_extent(text, 0) is None

    def test_start_not_bracket(self):
        text = "hello world"
        assert find_json_extent(text, 0) is None

    def test_offset_start(self):
        text = 'prefix{"a": 1}suffix'
        assert find_json_extent(text, 6) == 14

    def test_array_start(self):
        text = "[1, 2, 3]"
        assert find_json_extent(text, 0) == len(text)

    def test_empty_object(self):
        text = "{}"
        assert find_json_extent(text, 0) == 2

    def test_empty_array(self):
        text = "[]"
        assert find_json_extent(text, 0) == 2

    def test_out_of_bounds(self):
        text = "{}"
        assert find_json_extent(text, 10) is None

    def test_mixed_nesting(self):
        text = '{"a": [1, {"b": [2, 3]}]}'
        assert find_json_extent(text, 0) == len(text)


# ---------------------------------------------------------------------------
# extract_by_pattern
# ---------------------------------------------------------------------------


class TestExtractByPattern:
    def test_af_init_data_callback(self):
        data = [1, "hello", [3, 4]]
        html = f"AF_initDataCallback({{key: 'ds:1', data: {json.dumps(data)}}});"
        result = extract_by_pattern(html, r"AF_initDataCallback\s*\(\s*\{[^}}]*data\s*:\s*")
        assert result == data

    def test_custom_pattern(self):
        html = 'setupData({"title": "Engineer", "dept": "Eng"});'
        result = extract_by_pattern(html, r"setupData\(")
        assert result == {"title": "Engineer", "dept": "Eng"}

    def test_no_match_returns_none(self):
        html = "<html><body>No match here</body></html>"
        result = extract_by_pattern(html, r"setupData\(")
        assert result is None

    def test_pattern_with_json_object(self):
        html = 'window.config = {"api": "https://example.com"};'
        result = extract_by_pattern(html, r"window\.config\s*=\s*")
        assert result == {"api": "https://example.com"}

    def test_nested_json_after_pattern(self):
        html = 'init({"a": {"b": [1, 2, 3]}});'
        result = extract_by_pattern(html, r"init\(")
        assert result == {"a": {"b": [1, 2, 3]}}


# ---------------------------------------------------------------------------
# extract_by_variable
# ---------------------------------------------------------------------------


class TestExtractByVariable:
    def test_window_data(self):
        html = 'window.__DATA__ = {"job": "Engineer"};'
        result = extract_by_variable(html, "window.__DATA__")
        assert result == {"job": "Engineer"}

    def test_var_declaration(self):
        html = 'var jobData = {"title": "PM", "id": 42};'
        result = extract_by_variable(html, "jobData")
        assert result == {"title": "PM", "id": 42}

    def test_const_declaration(self):
        html = 'const appState = {"loaded": true};'
        result = extract_by_variable(html, "appState")
        assert result == {"loaded": True}

    def test_let_declaration(self):
        html = "let config = [1, 2, 3];"
        result = extract_by_variable(html, "config")
        assert result == [1, 2, 3]

    def test_missing_returns_none(self):
        html = "<html><body>Nothing here</body></html>"
        result = extract_by_variable(html, "window.__DATA__")
        assert result is None


# ---------------------------------------------------------------------------
# extract_script_by_id
# ---------------------------------------------------------------------------


class TestExtractScriptById:
    def test_standard_case(self):
        html = '<script id="app-data">{"key": "value"}</script>'
        result = extract_script_by_id(html, "app-data")
        assert result == '{"key": "value"}'

    def test_no_match_returns_none(self):
        html = '<script id="other">{"key": "value"}</script>'
        result = extract_script_by_id(html, "app-data")
        assert result is None

    def test_empty_content(self):
        html = '<script id="app-data"></script>'
        result = extract_script_by_id(html, "app-data")
        assert result is None

    def test_with_type_attribute(self):
        html = '<script id="app-data" type="application/json">{"a": 1}</script>'
        result = extract_script_by_id(html, "app-data")
        assert result == '{"a": 1}'

    def test_multiline_content(self):
        content = '{\n  "title": "Engineer",\n  "id": 42\n}'
        html = f'<script id="data">{content}</script>'
        result = extract_script_by_id(html, "data")
        assert result == content

    def test_next_data(self):
        data = {"props": {"pageProps": {"title": "Test"}}}
        html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'
        result = extract_script_by_id(html, "__NEXT_DATA__")
        assert json.loads(result) == data


# ---------------------------------------------------------------------------
# parse_embedded — top-level dispatcher
# ---------------------------------------------------------------------------


class TestParseEmbedded:
    def test_script_id(self):
        data = {"title": "Engineer"}
        html = f'<script id="job-data">{json.dumps(data)}</script>'
        result = parse_embedded(html, {"script_id": "job-data"})
        assert result == data

    def test_pattern(self):
        data = {"name": "Designer"}
        html = f"loadData({json.dumps(data)});"
        result = parse_embedded(html, {"pattern": r"loadData\("})
        assert result == data

    def test_variable(self):
        data = {"role": "PM"}
        html = f"window.__JOB__ = {json.dumps(data)};"
        result = parse_embedded(html, {"variable": "window.__JOB__"})
        assert result == data

    def test_script_id_priority(self):
        """script_id takes priority over pattern."""
        script_data = {"from": "script"}
        html = (
            f'<script id="data">{json.dumps(script_data)}</script>'
            f"window.__X__ = {json.dumps({'from': 'var'})};"
        )
        result = parse_embedded(html, {"script_id": "data", "variable": "window.__X__"})
        assert result == script_data

    def test_no_match_returns_none(self):
        html = "<html><body>Nothing</body></html>"
        result = parse_embedded(html, {"script_id": "missing"})
        assert result is None

    def test_empty_config(self):
        html = "<html><body>Test</body></html>"
        result = parse_embedded(html, {})
        assert result is None

    def test_script_id_invalid_json(self):
        html = '<script id="data">not json</script>'
        result = parse_embedded(html, {"script_id": "data"})
        assert result is None

    def test_trailing_comma_cleanup(self):
        html = '<script id="data">{"a": 1, "b": 2,}</script>'
        result = parse_embedded(html, {"script_id": "data"})
        assert result == {"a": 1, "b": 2}
