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


def test_walk_steps_match_regex_filters_similar_headings():
    elements = [
        {"tag": "h3", "attrs": {}, "text": "MR職 よくあるご質問"},
        {"tag": "h3", "attrs": {}, "text": "MR職"},
    ]

    result, cursor = walk_steps(
        elements,
        [{"tag": "h3", "match_regex": r"^MR職$", "field": "title"}],
    )

    assert result == {"title": "MR職"}
    assert cursor == 2


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
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    def __init__(self, html: str, responses: dict[str, _FakeResponse] | None = None):
        self._html = html
        self._responses = responses or {}
        self.requested_urls: list[str] = []
        self.request_headers: list[dict | None] = []

    async def get(self, url, **kwargs):
        self.requested_urls.append(str(url))
        self.request_headers.append(kwargs.get("headers"))
        return self._responses.get(str(url), _FakeResponse(self._html))


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
async def test_discover_can_include_hidden_tab_panels_and_filter_titles():
    html = """
    <div aria-hidden="true">
      <h3>Engineer</h3><p>Build medicines.</p>
      <h3>Engineer FAQ</h3><p>Answers.</p>
      <h3>Scientist</h3><p>Run studies.</p>
    </div>
    """
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "include_hidden": True,
            "steps": [
                {
                    "tag": "h3",
                    "match_regex": r"^(Engineer|Scientist)$",
                    "field": "title",
                    "optional": True,
                },
                {"tag": "p", "field": "description", "stop_tag": "h3"},
            ],
        },
    }

    jobs = await discover(board, _FakeClient(html))

    assert [job.title for job in jobs] == ["Engineer", "Scientist"]
    assert jobs[0].description == "Build medicines."
    assert jobs[1].description == "Run studies."


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
async def test_discover_excludes_non_job_card_and_continues():
    html = """
    <html><body>
    <h3>Talent Community</h3><p>Register for future openings.</p>
    <h3>Watchmaker</h3><p>Assemble and test watch movements.</p>
    </body></html>
    """
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "steps": [
                {"tag": "h3", "field": "title"},
                {"tag": "p", "field": "description", "stop_tag": "h3"},
            ],
            "exclude_titles": ["Talent Community"],
        },
    }

    jobs = await discover(board, _FakeClient(html))

    assert [job.title for job in jobs] == ["Watchmaker"]
    assert jobs[0].description == "Assemble and test watch movements."


@pytest.mark.asyncio
async def test_discover_fetch_url_keeps_canonical_job_url_and_description_default():
    client = _FakeClient("<html><head><title>Evergreen Driver</title></head></html>")
    board = {
        "board_url": "https://careers.example.com/driver",
        "metadata": {
            "fetch_url": "https://render.example.net/driver",
            "steps": [{"tag": "title", "field": "title"}],
            "defaults": {
                "description": "<p>Deliver customer orders from a local depot.</p>",
                "locations": ["South Korea"],
            },
        },
    }

    jobs = await discover(board, client)

    assert client.requested_urls == ["https://render.example.net/driver"]
    assert len(jobs) == 1
    assert jobs[0].title == "Evergreen Driver"
    assert jobs[0].description == "<p>Deliver customer orders from a local depot.</p>"
    assert jobs[0].locations == ["South Korea"]
    assert jobs[0].url.startswith("https://careers.example.com/driver?_jid=")


@pytest.mark.asyncio
async def test_discover_fetch_urls_falls_back_and_validates_required_text():
    blocked = "https://careers.example.com/driver"
    incomplete = "https://render-one.example.net/driver"
    working = "https://render-two.example.net/driver"
    client = _FakeClient(
        "",
        responses={
            blocked: _FakeResponse("Access denied", 403),
            incomplete: _FakeResponse("<html><head><title>Gateway error</title></head></html>"),
            working: _FakeResponse("<html><head><title>Evergreen Driver</title></head></html>"),
        },
    )
    board = {
        "board_url": blocked,
        "metadata": {
            "fetch_urls": [
                blocked,
                {"url": incomplete, "headers": {"X-No-Cache": "true"}},
                working,
            ],
            "fetch_contains": "Evergreen Driver",
            "steps": [{"tag": "title", "field": "title"}],
            "defaults": {
                "description": "<p>Deliver customer orders.</p>",
                "locations": ["South Korea"],
            },
        },
    }

    jobs = await discover(board, client)

    assert client.requested_urls == [blocked, incomplete, working]
    assert client.request_headers == [None, {"X-No-Cache": "true"}, None]
    assert [job.title for job in jobs] == ["Evergreen Driver"]
    assert jobs[0].url.startswith(f"{blocked}?_jid=")


@pytest.mark.asyncio
async def test_discover_rejects_invalid_fetch_candidate_headers():
    board = {
        "board_url": "https://careers.example.com/driver",
        "metadata": {
            "fetch_urls": [
                {
                    "url": "https://render.example.net/driver",
                    "headers": {"Authorization": 123},
                }
            ],
            "steps": [{"tag": "title", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="headers must map strings to strings"):
        await discover(board, _FakeClient(""))


@pytest.mark.asyncio
async def test_discover_with_defaults_by_title():
    client = _FakeClient(SAMPLE_HTML)
    board = {
        "board_url": "https://example.com/open-positions",
        "metadata": {
            "steps": [
                {"tag": "h3", "field": "title"},
                {"tag": "p", "field": "description", "stop_tag": "h3"},
            ],
            "defaults": {
                "employment_type": "full_time",
                "job_location_type": "onsite",
            },
            "defaults_by_title": {
                "Software Engineer": {"locations": ["Zurich, Switzerland"]},
                "Product Manager": {
                    "locations": ["Berlin, Germany"],
                    "job_location_type": "hybrid",
                },
            },
        },
    }

    jobs = await discover(board, client)

    assert jobs[0].locations == ["Zurich, Switzerland"]
    assert jobs[0].employment_type == "full_time"
    assert jobs[0].job_location_type == "onsite"
    assert jobs[1].locations == ["Berlin, Germany"]
    assert jobs[1].job_location_type == "hybrid"
    assert jobs[2].locations is None
    assert jobs[2].job_location_type == "onsite"


@pytest.mark.asyncio
async def test_extracted_fields_override_defaults_by_title():
    client = _FakeClient(SAMPLE_HTML)
    board = {
        "board_url": "https://example.com/open-positions",
        "metadata": {
            "steps": [
                {"tag": "h3", "field": "title"},
                {"text": "Location", "field": "location", "regex": "Location:\\s*(.+)"},
                {"tag": "p", "field": "description", "stop_tag": "h3"},
            ],
            "defaults_by_title": {
                "Software Engineer": {"locations": ["Wrong default"]},
            },
        },
    }

    jobs = await discover(board, client)

    assert jobs[0].locations == ["Zurich", "Switzerland"]


@pytest.mark.asyncio
async def test_discover_accordion_jobs_with_mixed_boundary_tags():
    html = """
    <html><body>
    <h3>Available Positions</h3>
    <h3><div class="grow accordion-title">Research President</div></h3>
    <div class="accordion-content">
      <p>Lead a multidisciplinary research organization.</p>
      <p>Applications are accepted by email.</p>
    </div>
    <h3><div class="grow accordion-title">Assistant Scientist</div></h3>
    <div class="accordion-content">
      <p>Build an independent rare-disease research program.</p>
    </div>
    <h3><div class="grow accordion-title">Postdoctoral Fellow</div></h3>
    <div class="accordion-content">
      <p>Study cancer biology and biomedical engineering.</p>
    </div>
    <h4>Life in the city</h4>
    <p>General relocation information that is not part of the posting.</p>
    </body></html>
    """
    board = {
        "board_url": "https://example.com/research-careers",
        "metadata": {
            "steps": [
                {
                    "tag": "div",
                    "attr": "class=accordion-title",
                    "field": "title",
                    "optional": True,
                },
                {
                    "tag": "p",
                    "field": "description",
                    "html": True,
                    "stop_tag": ["div", "h4"],
                },
            ],
            "defaults": {"locations": ["Sioux Falls, South Dakota, USA"]},
        },
    }

    jobs = await discover(board, _FakeClient(html))

    assert [job.title for job in jobs] == [
        "Research President",
        "Assistant Scientist",
        "Postdoctoral Fellow",
    ]
    assert jobs[0].description == (
        "<p>Lead a multidisciplinary research organization.</p>"
        "<p>Applications are accepted by email.</p>"
    )
    assert jobs[2].description == ("<p>Study cancer biology and biomedical engineering.</p>")
    assert "relocation" not in jobs[2].description
    assert all(job.locations == ["Sioux Falls, South Dakota, USA"] for job in jobs)


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
