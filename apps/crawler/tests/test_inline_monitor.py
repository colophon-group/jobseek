"""Tests for the inline single-page monitor."""

from __future__ import annotations

import pytest

from src.core.monitors.inline import _generate_url, discover
from src.shared.extract import flatten, walk_steps

# ── walk_steps returns cursor ──────────────────────────────────────────


def test_walk_steps_returns_cursor():
    elements = [
        {"tag": "h1", "attrs": {}, "text": "Title"},
        {"tag": "p", "attrs": {}, "text": "Description"},
    ]
    result, cursor = walk_steps(elements, [{"tag": "h1", "field": "title"}])
    assert result["title"] == "Title"
    assert cursor == 1


def test_walk_steps_cursor_advances_through_range():
    elements = [
        {"tag": "h3", "attrs": {}, "text": "Job A"},
        {"tag": "p", "attrs": {}, "text": "Desc A line 1"},
        {"tag": "p", "attrs": {}, "text": "Desc A line 2"},
        {"tag": "h3", "attrs": {}, "text": "Job B"},
        {"tag": "p", "attrs": {}, "text": "Desc B"},
    ]
    steps = [
        {"tag": "h3", "field": "title"},
        {"tag": "p", "field": "description", "stop_tag": "h3"},
    ]

    result_a, cursor_a = walk_steps(elements, steps, start=0)
    assert result_a["title"] == "Job A"
    assert "Desc A line 1" in result_a["description"]
    assert cursor_a == 3  # at "Job B"

    result_b, cursor_b = walk_steps(elements, steps, start=cursor_a)
    assert result_b["title"] == "Job B"
    assert "Desc B" in result_b["description"]
    assert cursor_b > cursor_a


# ── Repeated extraction ────────────────────────────────────────────────


SAMPLE_HTML = """
<html><body>
<h3>Software Engineer</h3>
<p>Location: Zurich, Switzerland</p>
<p>We are looking for a talented engineer to join our team.</p>

<h3>Product Manager</h3>
<p>Location: Berlin, Germany</p>
<p>Lead our product strategy and roadmap.</p>

<h3>Data Scientist</h3>
<p>Location: London, UK</p>
<p>Apply ML to solve real problems.</p>
</body></html>
"""


def test_repeated_extraction():
    elements = flatten(SAMPLE_HTML)
    steps = [
        {"tag": "h3", "field": "title"},
        {"text": "Location", "field": "location"},
        {"tag": "p", "field": "description", "stop_tag": "h3"},
    ]

    jobs = []
    cursor = 0
    while cursor < len(elements):
        result, new_cursor = walk_steps(elements, steps, start=cursor)
        if not result.get("title") or new_cursor <= cursor:
            break
        jobs.append(result)
        cursor = new_cursor

    assert len(jobs) == 3
    assert jobs[0]["title"] == "Software Engineer"
    assert jobs[1]["title"] == "Product Manager"
    assert jobs[2]["title"] == "Data Scientist"
    assert "Zurich" in jobs[0]["location"]
    assert "Berlin" in jobs[1]["location"]


# ── URL generation ─────────────────────────────────────────────────────


def test_generate_url_stable():
    seen: dict[str, int] = {}
    url1 = _generate_url("https://example.com/careers", "Software Engineer", seen)
    seen2: dict[str, int] = {}
    url2 = _generate_url("https://example.com/careers", "Software Engineer", seen2)
    assert url1 == url2
    assert "_jid=software-engineer-" in url1


def test_generate_url_different_titles():
    seen: dict[str, int] = {}
    url1 = _generate_url("https://example.com/careers", "Software Engineer", seen)
    url2 = _generate_url("https://example.com/careers", "Product Manager", seen)
    assert url1 != url2


def test_generate_url_collision():
    seen: dict[str, int] = {}
    url1 = _generate_url("https://example.com/careers", "Engineer", seen)
    url2 = _generate_url("https://example.com/careers", "Engineer", seen)
    assert url1 != url2
    assert "-2" in url2


def test_generate_url_with_existing_params():
    seen: dict[str, int] = {}
    url = _generate_url("https://example.com/jobs?lang=en", "Engineer", seen)
    assert "lang=en" in url
    assert "_jid=" in url


def test_generate_url_slug_caps_length():
    seen: dict[str, int] = {}
    long_title = "A" * 200
    url = _generate_url("https://example.com/careers", long_title, seen)
    # The slug portion (before the hash) should be capped
    assert len(url) < 300


# ── discover() end-to-end ──────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        pass


class _FakeClient:
    def __init__(self, html: str):
        self._html = html

    async def get(self, url, **kwargs):
        return _FakeResponse(self._html)


@pytest.mark.asyncio
async def test_discover_static():
    client = _FakeClient(SAMPLE_HTML)
    board = {
        "board_url": "https://example.com/open-positions",
        "metadata": {
            "steps": [
                {"tag": "h3", "field": "title"},
                {"text": "Location", "field": "location", "regex": "Location:\\s*(.+)"},
                {"tag": "p", "field": "description", "stop_tag": "h3"},
            ],
        },
    }
    jobs = await discover(board, client)

    assert len(jobs) == 3
    assert jobs[0].title == "Software Engineer"
    assert jobs[0].locations == ["Zurich", "Switzerland"]
    assert jobs[1].title == "Product Manager"
    assert "_jid=" in jobs[0].url
    assert jobs[0].url != jobs[1].url


@pytest.mark.asyncio
async def test_discover_with_defaults():
    html = """
    <html><body>
    <h3>Engineer</h3>
    <p>Build things.</p>
    </body></html>
    """
    client = _FakeClient(html)
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "steps": [
                {"tag": "h3", "field": "title"},
                {"tag": "p", "field": "description", "stop_tag": "h3"},
            ],
            "defaults": {
                "employment_type": "full_time",
                "job_location_type": "onsite",
            },
        },
    }
    jobs = await discover(board, client)

    assert len(jobs) == 1
    assert jobs[0].employment_type == "full_time"
    assert jobs[0].job_location_type == "onsite"


@pytest.mark.asyncio
async def test_discover_empty_page():
    client = _FakeClient("<html><body></body></html>")
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "steps": [{"tag": "h3", "field": "title"}],
        },
    }
    jobs = await discover(board, client)
    assert jobs == []


@pytest.mark.asyncio
async def test_discover_no_steps():
    client = _FakeClient("")
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {},
    }
    jobs = await discover(board, client)
    assert jobs == []
