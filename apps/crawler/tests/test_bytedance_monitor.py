"""Tests for the dedicated ByteDance careers monitor."""

from __future__ import annotations

import json

import pytest

from src.core.monitors import bytedance
from src.core.monitors.bytedance import (
    _CAMPUS,
    _EXPERIENCED,
    _GLOBAL,
    _body,
    _category_partitions,
    _collect,
    _collect_partition,
    _portal_for_url,
    _to_job,
    can_handle,
)


def _item(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": f"Engineer {job_id}",
        "description": "Build products",
        "requirement": "Write reliable software",
        "city_info": {
            "en_name": "San Jose",
            "parent": {"en_name": "California"},
        },
        "recruit_type": {"en_name": "Regular"},
        "job_category": {"en_name": "Engineering"},
        "create_time": 1_700_000_000,
    }


def _response(items: list[dict], total: int) -> dict:
    return {"code": 0, "data": {"job_post_list": items, "count": total}}


@pytest.mark.parametrize(
    ("url", "portal"),
    [
        ("https://joinbytedance.com/", _GLOBAL),
        ("https://joinbytedance.com/search", _GLOBAL),
        ("https://jobs.bytedance.com/experienced/position", _EXPERIENCED),
        ("https://jobs.bytedance.com/campus/position", _CAMPUS),
    ],
)
def test_portal_for_canonical_urls(url, portal):
    assert _portal_for_url(url) is portal


def test_rejects_filtered_or_detail_urls():
    assert _portal_for_url("https://jobs.bytedance.com/experienced/position?cat=rd") is None
    assert _portal_for_url("https://jobs.bytedance.com/experienced/position/123/detail") is None
    assert _portal_for_url("https://example.com/search") is None


async def test_can_handle_is_config_free():
    assert await can_handle("https://joinbytedance.com/", None) == {}
    assert await can_handle("https://example.com/", None) is None


def test_builds_body_pagination_in_json():
    body = json.loads(_body(_EXPERIENCED, offset=2_000, category_ids=["rd"]))
    assert body["offset"] == 2_000
    assert body["limit"] == 1_000
    assert body["portal_type"] == 2
    assert body["job_category_id_list"] == ["rd"]


def test_category_partitions_use_children_only_above_cap():
    data = {
        "code": 0,
        "data": {
            "job_type_list": [
                {"id": "small", "children": []},
                {"id": "large", "children": [{"id": "large-a"}, {"id": "large-b"}]},
            ],
            "job_type_count_map": {"small": 12, "large": 10_500},
        },
    }
    partitions = _category_partitions(data)
    assert partitions == [["small"], ["large-a"], ["large-b"]]


async def test_collect_partition_updates_post_body_offset(monkeypatch):
    monkeypatch.setattr(bytedance, "_PAGE_SIZE", 2)
    seen_offsets: list[int] = []
    rows = [_item(str(index)) for index in range(5)]

    async def fetch(method, url, headers, body):
        assert method == "POST"
        payload = json.loads(body)
        seen_offsets.append(payload["offset"])
        offset = payload["offset"]
        return _response(rows[offset : offset + 2], len(rows))

    items, total = await _collect_partition(fetch, _CAMPUS, None)
    assert seen_offsets == [0, 2, 4]
    assert items == rows
    assert total == 5


async def test_experienced_collection_partitions_and_deduplicates(monkeypatch):
    monkeypatch.setattr(bytedance, "_PAGE_SIZE", 10)
    rows = {"engineering": [_item("1")], "sales": [_item("2")]}

    async def fetch(method, url, headers, body):
        if method == "GET":
            return {
                "code": 0,
                "data": {
                    "job_type_list": [{"id": "engineering"}, {"id": "sales"}],
                    "job_type_count_map": {"engineering": 1, "sales": 1},
                },
            }
        category = json.loads(body)["job_category_id_list"][0]
        return _response(rows[category], 1)

    items = await _collect(fetch, _EXPERIENCED)
    assert {item["id"] for item in items} == {"1", "2"}


async def test_incomplete_partition_fails_closed(monkeypatch):
    monkeypatch.setattr(bytedance, "_PAGE_SIZE", 2)

    async def fetch(method, url, headers, body):
        offset = json.loads(body)["offset"]
        return _response([_item("1")] if offset == 0 else [], 3)

    with pytest.raises(RuntimeError, match="pagination ended"):
        await _collect_partition(fetch, _CAMPUS, None)


async def test_explicit_zero_partition_is_authoritative():
    async def fetch(method, url, headers, body):
        return _response([], 0)

    assert await _collect_partition(fetch, _CAMPUS, None) == ([], 0)


def test_to_job_maps_required_and_optional_fields():
    job = _to_job(_item("123"), _EXPERIENCED)
    assert job.url == "https://jobs.bytedance.com/experienced/position/123/detail"
    assert job.source_identity == "bytedance:jobs.bytedance.com:123"
    assert job.title == "Engineer 123"
    assert "Responsibilities" in job.description
    assert "Requirements" in job.description
    assert job.locations == ["San Jose, California"]
    assert job.employment_type == "Regular"
    assert job.date_posted == "1700000000"
    assert job.metadata == {"team": "Engineering"}
