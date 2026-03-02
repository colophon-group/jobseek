from __future__ import annotations

import json

from src.batch import BatchResult, _jsonb


class TestJsonb:
    def test_with_dict(self):
        assert _jsonb({"key": "value"}) == '{"key": "value"}'

    def test_with_none(self):
        assert _jsonb(None) is None

    def test_with_nested(self):
        result = _jsonb({"a": [1, 2, 3]})
        assert json.loads(result) == {"a": [1, 2, 3]}

    def test_with_empty_dict(self):
        assert _jsonb({}) == "{}"


class TestBatchResult:
    def test_defaults(self):
        r = BatchResult()
        assert r.processed == 0
        assert r.succeeded == 0
        assert r.failed == 0

    def test_custom_values(self):
        r = BatchResult(processed=10, succeeded=8, failed=2)
        assert r.processed == 10
        assert r.succeeded == 8
        assert r.failed == 2
