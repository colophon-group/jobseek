"""Unit tests for src.core.monitors._incremental."""

from __future__ import annotations

import pytest

from src.core.monitors._incremental import HardPageCap, paginate_all, paginate_until_old


def _make_fetcher(pages: list[list[dict]], *, page_size: int = 10):
    """Return an async fetcher that yields one page per call, records calls."""
    calls: list[int] = []

    async def fetch_page(offset: int) -> list[dict]:
        calls.append(offset)
        page_index = offset // page_size
        if page_index >= len(pages):
            return []
        return pages[page_index]

    return fetch_page, calls


def _item(ts: int | None) -> dict:
    return {"ts": ts}


def _get_ts(item: dict) -> int | None:
    return item.get("ts")


class TestPaginateUntilOld:
    async def test_first_page_all_old_stops_after_safety(self):
        """First page has all old items, safety=3 → fetch 4 pages total."""
        pages = [
            [_item(100) for _ in range(10)],
            [_item(90) for _ in range(10)],
            [_item(80) for _ in range(10)],
            [_item(70) for _ in range(10)],
            [_item(60) for _ in range(10)],  # should never reach
        ]
        fetch, calls = _make_fetcher(pages)
        result = await paginate_until_old(
            fetch,
            _get_ts,
            max_watermark=200,
            page_size=10,
            safety_pages=3,
        )
        assert calls == [0, 10, 20, 30]
        assert len(result) == 40

    async def test_mixed_page_continues(self):
        """A page with some new and some old items doesn't terminate."""
        pages = [
            [_item(500), _item(400), _item(300)],  # all new
            [_item(250), _item(150), _item(50)],  # mixed (150, 50 are old)
            [_item(40), _item(30)],  # all old
            [_item(20)],  # safety page 1
            [],  # safety page 2 → ends
        ]
        fetch, calls = _make_fetcher(pages)
        result = await paginate_until_old(
            fetch,
            _get_ts,
            max_watermark=200,
            page_size=3,  # fake — we use page_index*10 anyway
            safety_pages=3,
        )
        # Mixed page does not trigger termination, so we keep going until all-old.
        # The first all-old page is index 2 (ts 40,30).
        assert len(calls) >= 3
        assert len(result) >= 8

    async def test_empty_page_stops_immediately(self):
        """Empty upstream ends pagination regardless of watermark."""
        pages: list[list[dict]] = []
        fetch, calls = _make_fetcher(pages)
        result = await paginate_until_old(fetch, _get_ts, max_watermark=100, page_size=10)
        assert calls == [0]
        assert result == []

    async def test_hard_page_cap_raises(self):
        """If termination is never reached, HardPageCap is raised."""
        # Every page contains items just above the watermark — termination never fires.
        pages = [[_item(999) for _ in range(10)] for _ in range(10)]
        fetch, _ = _make_fetcher(pages)
        with pytest.raises(HardPageCap):
            await paginate_until_old(
                fetch,
                _get_ts,
                max_watermark=100,
                page_size=10,
                safety_pages=3,
                hard_page_cap=5,
            )

    async def test_missing_timestamp_treated_as_new(self):
        """Items with None timestamp are always kept and prevent termination."""
        pages = [
            [_item(100), _item(None)],  # has a None — not all-old, keep going
            [_item(50), _item(40)],  # all old
            [_item(30)],  # safety 1
            [_item(20)],  # safety 2
            [_item(10)],  # safety 3 — stop after this (safety_remaining reaches 0)
            [_item(5)],  # should never reach
        ]
        fetch, calls = _make_fetcher(pages)
        result = await paginate_until_old(
            fetch,
            _get_ts,
            max_watermark=200,
            page_size=10,
            safety_pages=3,
        )
        # The None item stayed in results; all pages up to and including safety
        # (4 safety pages: index 1 + 3 safety) got fetched but not the 6th.
        assert 5 not in [(r.get("ts") or -1) for r in result]
        assert any(r.get("ts") is None for r in result)

    async def test_safety_pages_zero(self):
        """safety_pages=0 stops immediately at the first all-old page."""
        pages = [
            [_item(500), _item(400)],  # new
            [_item(50), _item(40)],  # all old — stop here
            [_item(30)],  # should never reach
        ]
        fetch, calls = _make_fetcher(pages)
        result = await paginate_until_old(
            fetch,
            _get_ts,
            max_watermark=100,
            page_size=10,
            safety_pages=0,
        )
        assert calls == [0, 10]
        assert len(result) == 4

    async def test_custom_page_size(self):
        """Offsets are computed as page_index * page_size."""
        pages = [
            [_item(500)],
            [_item(50)],  # all old
        ]
        fetch, calls = _make_fetcher(pages, page_size=5)
        # Force page_size=5 so offsets should be 0, 5, 10 (3rd is empty).
        await paginate_until_old(
            fetch,
            _get_ts,
            max_watermark=100,
            page_size=5,
            safety_pages=10,  # large so it hits the end (empty page)
        )
        assert calls == [0, 5, 10]

    async def test_boundary_jitter_handled(self):
        """Two items with equal timestamps spanning a page boundary are caught
        by safety pages — termination doesn't happen at the first crossing."""
        pages = [
            [_item(200), _item(200), _item(100), _item(100)],  # mixed
            [_item(100), _item(100)],  # all old
            [_item(100)],  # safety 1: still at boundary
            [_item(90)],  # safety 2
            [_item(80)],  # safety 3 — stop after this
        ]
        fetch, calls = _make_fetcher(pages)
        await paginate_until_old(
            fetch,
            _get_ts,
            max_watermark=100,
            page_size=10,
            safety_pages=3,
        )
        # Safety pages let us keep fetching past the first all-old page.
        assert len(calls) >= 4


class TestPaginateAll:
    async def test_sequential_fetches_until_empty(self):
        pages = [
            [_item(i) for i in range(10)],
            [_item(i) for i in range(10, 20)],
            [_item(i) for i in range(20, 25)],
        ]
        fetch, calls = _make_fetcher(pages)
        result = await paginate_all(fetch, page_size=10)
        assert calls == [0, 10, 20, 30]
        assert len(result) == 25

    async def test_empty_first_page(self):
        fetch, calls = _make_fetcher([])
        result = await paginate_all(fetch, page_size=10)
        assert calls == [0]
        assert result == []

    async def test_max_items_truncates(self):
        pages = [[_item(i) for i in range(10)] for _ in range(5)]
        fetch, calls = _make_fetcher(pages)
        result = await paginate_all(fetch, page_size=10, max_items=15)
        assert len(result) == 15
        # Only fetched enough pages to hit the cap.
        assert len(calls) == 2

    async def test_hard_page_cap_returns_not_raises(self):
        """paginate_all returns gracefully instead of raising on the cap."""
        pages = [[_item(1)] for _ in range(20)]
        fetch, _ = _make_fetcher(pages)
        result = await paginate_all(fetch, page_size=10, hard_page_cap=3)
        assert len(result) == 3  # exactly hard_page_cap pages fetched
