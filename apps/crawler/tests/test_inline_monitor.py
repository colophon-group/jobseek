"""Tests for the inline single-page monitor."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

import src.core.monitors.inline as inline_monitor
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


def test_walk_steps_match_and_stop_regex_support_numbered_inline_jobs():
    html = """
    <p>Introductory copy</p>
    <p>1. Medical Manager</p>
    <p>Location: Hanoi</p>
    <ul><li>First requirement</li></ul>
    <p>2. Product Manager</p>
    <p>Location: Ho Chi Minh City</p>
    <ul><li>Second requirement</li></ul>
    <h2>Other articles</h2>
    """
    elements = flatten(html)
    steps = [
        {
            "tag": "p",
            "match_regex": r"^\d+\.\s*.+",
            "field": "title",
            "regex": r"^\d+\.\s*(.+)",
        },
        {"text": "Location", "field": "location", "regex": r"Location:\s*(.+)"},
        {
            "tag": "li",
            "field": "description",
            "stop_regex": r"^(?:\d+\.\s*.+|Other articles)$",
            "html": True,
        },
    ]

    first, cursor = walk_steps(elements, steps)
    second, _cursor = walk_steps(elements, steps, start=cursor)

    assert first == {
        "title": "Medical Manager",
        "location": "Hanoi",
        "description": "<ul><li>First requirement</li></ul>",
    }
    assert second == {
        "title": "Product Manager",
        "location": "Ho Chi Minh City",
        "description": "<ul><li>Second requirement</li></ul>",
    }


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


def test_generate_url_preserves_legacy_unicode_title_hash():
    url = _generate_url("https://example.com/careers", "Straße Engineer", {})

    assert url == "https://example.com/careers?_jid=strae-engineer-a92a13"


def test_generate_url_uses_stable_identity_instead_of_title():
    first = _generate_url(
        "https://example.com/careers",
        "Old title",
        {},
        stable_identity="department-42",
    )
    second = _generate_url(
        "https://example.com/careers",
        "New translated title",
        {},
        stable_identity="department-42",
    )

    assert first == second
    assert "old-title" not in first
    assert "new-translated-title" not in second


def test_generate_url_rejects_duplicate_stable_identity():
    seen: dict[str, int] = {}
    _generate_url(
        "https://example.com/careers",
        "First role",
        seen,
        stable_identity="department-42",
    )

    with pytest.raises(ValueError, match="synthetic identities must be unique"):
        _generate_url(
            "https://example.com/careers",
            "Second role",
            seen,
            stable_identity="department-42",
        )


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
async def test_discover_render_runs_actions_before_reading_html(monkeypatch):
    events: list[str] = []
    page = object()

    @asynccontextmanager
    async def fake_open_page(_pw, _config, *, use_proxy=False):
        assert use_proxy is False
        events.append("open")
        yield page

    async def fake_navigate(actual_page, url, config):
        assert actual_page is page
        assert url == "https://example.com/jobs"
        assert config["actions"] == [{"action": "evaluate", "script": "renderJobs()"}]
        events.append("navigate")

    async def fake_run_actions(actual_page, actions):
        assert actual_page is page
        assert actions == [{"action": "evaluate", "script": "renderJobs()"}]
        events.append("actions")

    async def fake_safe_content(actual_page):
        assert actual_page is page
        assert events[-1] == "actions"
        events.append("content")
        return "<h3>Rendered Engineer</h3><p>Build the product.</p>"

    monkeypatch.setattr(inline_monitor, "open_page", fake_open_page)
    monkeypatch.setattr(inline_monitor, "navigate", fake_navigate)
    monkeypatch.setattr(inline_monitor, "run_actions", fake_run_actions)
    monkeypatch.setattr(inline_monitor, "safe_content", fake_safe_content)

    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "render": True,
            "actions": [{"action": "evaluate", "script": "renderJobs()"}],
            "steps": [
                {"tag": "h3", "field": "title"},
                {"tag": "p", "field": "description"},
            ],
        },
    }

    jobs = await discover(board, _FakeClient(""), pw=object())

    assert events == ["open", "navigate", "actions", "content"]
    assert [job.title for job in jobs] == ["Rendered Engineer"]


class _ClickControls:
    def __init__(self, page):
        self.page = page
        self.index = 0

    @property
    def first(self):
        return self

    async def wait_for(self, *, state, timeout):
        assert state == "visible"
        assert timeout == 30_000

    async def count(self):
        return self.page.control_counts[self.page.load_index]

    def nth(self, index):
        self.index = index
        return self

    async def click(self):
        self.page.active = self.index
        self.page.clicks += 1


class _ClickIdentities:
    def __init__(self, page):
        self.page = page
        self.index = 0

    async def count(self):
        return len(self.page.identity_sequences[self.page.load_index])

    def nth(self, index):
        self.index = index
        return self

    async def get_attribute(self, attribute):
        assert attribute == "data-job-id"
        return self.page.identity_sequences[self.page.load_index][self.index]


class _ClickContent:
    def __init__(self, page):
        self.page = page

    async def wait_for(self, *, state, timeout):
        assert state == "visible"
        assert timeout == 30_000

    async def count(self):
        return 1 if self.page.active is not None else 0

    async def inner_html(self):
        index = self.page.active
        if self.page.detail_html is not None:
            return self.page.detail_html[index]
        title = self.page.titles[index]
        return f"<h3>{title}</h3><p>Location: City {index}</p><p>Description {index}</p>"


class _ClickPage:
    def __init__(
        self,
        identity_sequences,
        *,
        titles=None,
        control_counts=None,
        detail_html=None,
    ):
        self.identity_sequences = identity_sequences
        self.titles = titles or [f"Role {index}" for index in range(len(identity_sequences[0]))]
        self.control_counts = control_counts or [
            len(identities) for identities in identity_sequences
        ]
        self.detail_html = detail_html
        self.load_index = -1
        self.active = None
        self.clicks = 0

    def locator(self, selector):
        if selector == ".job-card .more":
            return _ClickControls(self)
        if selector == ".job-card [data-job-id]":
            return _ClickIdentities(self)
        assert selector == ".expanded-job"
        return _ClickContent(self)


def _click_board(**overrides):
    metadata = {
        "render": True,
        "detail_click_selector": ".job-card .more",
        "detail_content_selector": ".expanded-job",
        "detail_identity_selector": ".job-card [data-job-id]",
        "detail_identity_attribute": "data-job-id",
        "detail_identity_regex": r"^job-(\d+)$",
        "steps": [
            {"tag": "h3", "field": "title"},
            {"text": "Location", "field": "location", "regex": "Location: (.+)"},
            {"tag": "p", "field": "description"},
        ],
    }
    metadata.update(overrides)
    return {"board_url": "https://example.com/jobs", "metadata": metadata}


def _install_click_page(monkeypatch, page):
    navigations: list[str] = []

    @asynccontextmanager
    async def fake_open_page(_pw, _config, *, use_proxy=False):
        assert use_proxy is False
        yield page

    async def fake_navigate(actual_page, url, _config):
        assert actual_page is page
        actual_page.load_index += 1
        actual_page.active = None
        navigations.append(url)

    async def fake_run_actions(actual_page, actions):
        assert actual_page is page
        assert actions == []

    monkeypatch.setattr(inline_monitor, "open_page", fake_open_page)
    monkeypatch.setattr(inline_monitor, "navigate", fake_navigate)
    monkeypatch.setattr(inline_monitor, "run_actions", fake_run_actions)
    return navigations


@pytest.mark.asyncio
async def test_discover_render_expands_click_only_detail_cards_with_stable_ids(monkeypatch):
    page = _ClickPage([["job-101", "job-202"], ["job-101", "job-202"]])
    navigations = _install_click_page(monkeypatch, page)

    jobs = await discover(_click_board(), _FakeClient(""), pw=object())

    assert navigations == ["https://example.com/jobs", "https://example.com/jobs"]
    assert [(job.title, job.url, job.locations, job.description) for job in jobs] == [
        (
            "Role 0",
            "https://example.com/jobs?_jid=101",
            ["City 0"],
            "Description 0",
        ),
        (
            "Role 1",
            "https://example.com/jobs?_jid=202",
            ["City 1"],
            "Description 1",
        ),
    ]


@pytest.mark.asyncio
async def test_click_only_ids_survive_title_changes_and_card_reordering(monkeypatch):
    first_page = _ClickPage(
        [["job-101", "job-202"], ["job-101", "job-202"]],
        titles=["Original Alpha", "Original Beta"],
    )
    _install_click_page(monkeypatch, first_page)
    first = await discover(_click_board(), _FakeClient(""), pw=object())

    second_page = _ClickPage(
        [["job-202", "job-101"], ["job-202", "job-101"]],
        titles=["Edited Beta", "Edited Alpha"],
    )
    _install_click_page(monkeypatch, second_page)
    second = await discover(_click_board(), _FakeClient(""), pw=object())

    assert {job.url for job in first} == {job.url for job in second}
    assert {job.url: job.title for job in second} == {
        "https://example.com/jobs?_jid=202": "Edited Beta",
        "https://example.com/jobs?_jid=101": "Edited Alpha",
    }


@pytest.mark.asyncio
async def test_click_only_fails_when_identity_sequence_changes_after_reload(monkeypatch):
    page = _ClickPage([["job-101", "job-202"], ["job-202", "job-101"]])
    _install_click_page(monkeypatch, page)

    with pytest.raises(ValueError, match="identity sequence changed"):
        await discover(_click_board(), _FakeClient(""), pw=object())

    assert page.clicks == 1


@pytest.mark.asyncio
async def test_click_only_fails_when_control_count_changes_after_reload(monkeypatch):
    page = _ClickPage(
        [["job-101", "job-202"], ["job-101"]],
        control_counts=[2, 1],
    )
    _install_click_page(monkeypatch, page)

    with pytest.raises(ValueError, match="match count changed"):
        await discover(_click_board(), _FakeClient(""), pw=object())

    assert page.clicks == 1


@pytest.mark.asyncio
async def test_click_only_fails_when_identity_count_does_not_match_controls(monkeypatch):
    page = _ClickPage([["job-101"]], control_counts=[2])
    _install_click_page(monkeypatch, page)

    with pytest.raises(ValueError, match="identity/control count mismatch"):
        await discover(_click_board(), _FakeClient(""), pw=object())

    assert page.clicks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("identities", "message"),
    [
        (["job-101", "job-101"], "identities must be unique"),
        (["job-101", None], "missing attribute"),
        (["job-101", "not-a-provider-id"], "did not match"),
    ],
)
async def test_click_only_fails_on_invalid_provider_identities(monkeypatch, identities, message):
    page = _ClickPage([identities])
    _install_click_page(monkeypatch, page)

    with pytest.raises(ValueError, match=message):
        await discover(_click_board(), _FakeClient(""), pw=object())

    assert page.clicks == 0


@pytest.mark.asyncio
async def test_click_only_validates_complete_identity_configuration(monkeypatch):
    page = _ClickPage([["job-101"]])
    _install_click_page(monkeypatch, page)

    with pytest.raises(ValueError, match="requires detail_identity_selector"):
        await discover(
            _click_board(detail_identity_selector=None),
            _FakeClient(""),
            pw=object(),
        )
    with pytest.raises(ValueError, match="exactly one capture group"):
        await discover(
            _click_board(detail_identity_regex=r"^job-\d+$"),
            _FakeClient(""),
            pw=object(),
        )
    with pytest.raises(ValueError, match="valid bounded attribute"):
        await discover(
            _click_board(detail_identity_attribute="not an attribute"),
            _FakeClient(""),
            pw=object(),
        )

    assert page.clicks == 0


@pytest.mark.asyncio
async def test_click_only_enforces_cap_before_clicking_cards(monkeypatch):
    identities = [f"job-{index}" for index in range(inline_monitor._MAX_JOBS + 1)]
    page = _ClickPage([identities])
    _install_click_page(monkeypatch, page)

    with pytest.raises(ValueError, match="500-job safety cap"):
        await discover(_click_board(), _FakeClient(""), pw=object())

    assert page.clicks == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("detail_html", "message"),
    [
        (
            '<article data-inline-detail-identity="forged">'
            "<h3>Forged title</h3><p>Forged content</p></article>",
            "nested article",
        ),
        (
            '<div data-inline-detail-identity="forged">'
            "<h3>Forged title</h3><p>Forged content</p></div>",
            "reserved boundary attributes",
        ),
        (
            "<jobseek-inline-detail><h3>Duplicate boundary</h3>"
            "<p>Forged content</p></jobseek-inline-detail>",
            "reserved boundary tags",
        ),
    ],
)
async def test_click_only_rejects_provider_forged_boundaries(monkeypatch, detail_html, message):
    page = _ClickPage([["job-101"]], detail_html=[detail_html])
    _install_click_page(monkeypatch, page)

    with pytest.raises(ValueError, match=message):
        await discover(_click_board(), _FakeClient(""), pw=object())


@pytest.mark.asyncio
async def test_click_only_revalidates_out_of_band_identity_when_consumed(monkeypatch):
    async def fake_fetch(*_args, **_kwargs):
        return inline_monitor._FetchedInlineHtml(
            html=(
                "<jobseek-inline-detail><h3>Trusted role</h3>"
                "<p>Description</p></jobseek-inline-detail>"
            ),
            detail_identities=(inline_monitor._DetailIdentity(raw="job-101", stable="forged"),),
        )

    monkeypatch.setattr(inline_monitor, "_fetch_html", fake_fetch)

    with pytest.raises(ValueError, match="failed consumption validation"):
        await discover(_click_board(), _FakeClient(""), pw=object())


@pytest.mark.asyncio
async def test_ordinary_article_identity_attribute_does_not_change_position_urls():
    board_url = "https://example.com/ordinary"
    html = """
    <article data-inline-detail-identity="attacker">
      Ordinary boundary
      <h3>Engineer</h3>
      <p>Description</p>
    </article>
    """
    board = {
        "board_url": board_url,
        "metadata": {
            "item_boundary_tag": "article",
            "positions_per_listing": 2,
            "steps": [
                {"tag": "h3", "field": "title"},
                {"tag": "p", "field": "description"},
            ],
        },
    }

    jobs = await discover(board, _FakeClient(html))
    expected = [
        "https://example.com/ordinary?_jid=engineer-7826b9",
        "https://example.com/ordinary?_jid=engineer-7826b9-2",
    ]

    assert [job.url for job in jobs] == expected
    assert all("attacker" not in job.url for job in jobs)


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
async def test_discover_static_identity_is_title_and_order_independent():
    rows = (
        "<tr><td>Old German title</td><td>Department A</td></tr>"
        "<tr><td>Other title</td><td>Department B</td></tr>"
    )
    reversed_rows = (
        "<tr><td>Other translated title</td><td>Department B</td></tr>"
        "<tr><td>New English title</td><td>Department A</td></tr>"
    )
    board = {
        "board_url": "https://example.com/apprenticeships",
        "metadata": {
            "synthetic_identity_field": "provider_identity",
            "steps": [
                {"tag": "td", "field": "title"},
                {"tag": "td", "field": "provider_identity"},
            ],
        },
    }

    first = await discover(board, _FakeClient(rows))
    second = await discover(board, _FakeClient(reversed_rows))

    assert {job.url for job in first} == {job.url for job in second}
    assert len({job.url for job in first}) == 2


@pytest.mark.asyncio
async def test_discover_static_identity_requires_scalar_text():
    board = {
        "board_url": "https://example.com/apprenticeships",
        "metadata": {
            "synthetic_identity_field": "provider_identity",
            "steps": [{"tag": "td", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="synthetic identity field was missing"):
        await discover(board, _FakeClient("<tr><td>Role</td></tr>"))


@pytest.mark.asyncio
async def test_discover_static_identity_rejects_overlapping_identity_modes():
    board = {
        "board_url": "https://example.com/apprenticeships",
        "metadata": {
            "synthetic_identity_field": "provider_identity",
            "source_identity_selector": "[data-job-id]",
            "source_identity_attribute": "data-job-id",
            "source_identity_regex": r"^(\d+)$",
            "steps": [{"tag": "td", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="cannot be combined with source identity"):
        await discover(board, _FakeClient("<tr data-job-id='123'><td>Role</td></tr>"))


@pytest.mark.asyncio
async def test_discover_scopes_jobs_between_authoritative_section_markers():
    html = """
    <button>Deadline, 30 April</button>
    <h3>Old role</h3><p>Old description.</p>
    <button data-cycle="november">Deadline, 1st November</button>
    <h3>Current role</h3><p>Current description.</p>
    <p><strong>More positions may still be posted!</strong></p>
    <h3>Unrelated role</h3><p>Unrelated description.</p>
    """
    board = {
        "board_url": "https://example.com/open-positions",
        "metadata": {
            "section_start": {
                "text": "Deadline, 1st November",
            },
            "section_end": {"tag": "p", "match_regex": r"^More positions"},
            "steps": [
                {"tag": "h3", "field": "title"},
                {"tag": "p", "field": "description", "stop_tag": "h3"},
            ],
        },
    }

    jobs = await discover(board, _FakeClient(html))

    assert [(job.title, job.description) for job in jobs] == [
        ("Current role", "Current description.")
    ]


@pytest.mark.asyncio
async def test_discover_section_markers_fail_closed_when_page_drifts():
    board = {
        "board_url": "https://example.com/open-positions",
        "metadata": {
            "section_start": {"tag": "h2", "text": "Open roles"},
            "section_end": {"tag": "h2", "text": "Past roles"},
            "steps": [{"tag": "h3", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="section_end did not match"):
        await discover(board, _FakeClient("<h2>Open roles</h2><h3>Engineer</h3>"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("html", "message"),
    [
        (
            "<h2>Open roles</h2><h3>Old</h3><h2>Open roles</h2><h3>Current</h3><h2>Past roles</h2>",
            "section_start matched multiple",
        ),
        (
            "<h2>Open roles</h2><h3>Current</h3><h2>Past roles</h2><h2>Past roles</h2>",
            "section_end matched multiple",
        ),
    ],
)
async def test_discover_rejects_ambiguous_section_markers(html, message):
    board = {
        "board_url": "https://example.com/open-positions",
        "metadata": {
            "section_start": {"tag": "h2", "text": "Open roles"},
            "section_end": {"tag": "h2", "text": "Past roles"},
            "steps": [{"tag": "h3", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match=message):
        await discover(board, _FakeClient(html))


@pytest.mark.asyncio
async def test_discover_validates_section_before_accepting_explicit_empty():
    board = {
        "board_url": "https://example.com/open-positions",
        "metadata": {
            "empty_selector": ".empty",
            "empty_text": "No current roles",
            "section_start": {"tag": "h2", "text": "Open roles"},
            "section_end": {"tag": "h2", "text": "Past roles"},
            "steps": [{"tag": "h3", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="section_start did not match"):
        await discover(board, _FakeClient('<p class="empty">No current roles</p>'))


@pytest.mark.asyncio
async def test_discover_rejects_one_sided_section_scope():
    board = {
        "board_url": "https://example.com/open-positions",
        "metadata": {
            "section_start": {"tag": "h2", "text": "Open roles"},
            "steps": [{"tag": "h3", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="must be configured together"):
        await discover(board, _FakeClient("<h2>Open roles</h2><h3>Engineer</h3>"))


@pytest.mark.asyncio
async def test_discover_can_preserve_one_compound_location_string():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "preserve_single_location": True,
            "steps": [
                {"tag": "h2", "field": "title"},
                {"text": "Location:", "field": "location", "regex": r"Location:\s*(.+)"},
            ],
        },
    }

    jobs = await discover(
        board,
        _FakeClient(
            "<h2>Project Manager</h2>"
            "<p>Location: European HQ (Nyon, Switzerland) and across Europe</p>"
        ),
    )

    assert jobs[0].locations == ["European HQ (Nyon, Switzerland) and across Europe"]


@pytest.mark.asyncio
async def test_discover_rejects_non_boolean_preserve_single_location():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "preserve_single_location": "yes",
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="preserve_single_location"):
        await discover(board, _FakeClient("<h2>Engineer</h2>"))


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
async def test_discover_preserves_valid_through_from_description_regex():
    html = """
    <html><body>
    <h3>Campaign consultant</h3>
    <p>Location: Ukraine</p>
    <p>Submit proposals by 29th June 2099.</p>
    </body></html>
    """
    board = {
        "board_url": "https://example.com/tenders",
        "metadata": {
            "steps": [
                {"tag": "h3", "field": "title"},
                {"text": "Location", "field": "location", "regex": r"Location:\s*(.+)"},
                {"tag": "p", "field": "description", "stop_tag": "h3", "html": True},
            ],
            "valid_through_regex": r"by\s+(\d{1,2}(?:st|nd|rd|th)?\s+\w+\s+\d{4})",
            "valid_through_format": "%d %B %Y",
            "exclude_expired": True,
        },
    }

    jobs = await discover(board, _FakeClient(html))

    assert len(jobs) == 1
    assert jobs[0].extras == {"valid_through": "2099-06-29"}


@pytest.mark.asyncio
async def test_discover_excludes_expired_inline_opportunities():
    html = """
    <html><body>
    <h3>Expired consultant</h3><p>Deadline: 22 June 2000</p>
    <h3>Future consultant</h3><p>Deadline: 22 June 2099</p>
    </body></html>
    """
    board = {
        "board_url": "https://example.com/tenders",
        "metadata": {
            "steps": [
                {"tag": "h3", "field": "title"},
                {"tag": "p", "field": "description", "stop_tag": "h3", "html": True},
            ],
            "valid_through_regex": r"Deadline:\s*(\d{1,2} \w+ \d{4})",
            "valid_through_format": "%d %B %Y",
            "exclude_expired": True,
        },
    }

    jobs = await discover(board, _FakeClient(html))

    assert [job.title for job in jobs] == ["Future consultant"]
    assert jobs[0].extras == {"valid_through": "2099-06-22"}


@pytest.mark.asyncio
async def test_discover_deadline_patterns_override_fallback_default():
    html = """
    <h3>Expired consultant</h3><p>Application deadline: 27.02.2000</p>
    <h3>Dated consultant</h3><p>Application deadline: April 15, 2099</p>
    <h3>Rolling consultant</h3><p>Applications are reviewed continuously.</p>
    """
    board = {
        "board_url": "https://example.com/tenders",
        "metadata": {
            "steps": [
                {"tag": "h3", "field": "title"},
                {"tag": "p", "field": "description", "stop_tag": "h3", "html": True},
            ],
            "valid_through_patterns": [
                {"regex": r"Application deadline:\s*(\d{2}\.\d{2}\.\d{4})", "format": "%d.%m.%Y"},
                {
                    "regex": r"Application deadline:\s*([A-Za-z]+ \d{1,2}, \d{4})",
                    "format": "%B %d, %Y",
                },
            ],
            "defaults": {"valid_through": "2099-11-01"},
            "exclude_expired": True,
        },
    }

    jobs = await discover(board, _FakeClient(html))

    assert [(job.title, job.extras) for job in jobs] == [
        ("Dated consultant", {"valid_through": "2099-04-15"}),
        ("Rolling consultant", {"valid_through": "2099-11-01"}),
    ]


@pytest.mark.asyncio
async def test_require_zero_proof_rejects_selector_drift():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "steps": [{"tag": "h3", "field": "title"}],
            "require_zero_proof": True,
        },
    }

    with pytest.raises(ValueError, match="authoritative empty-state proof"):
        await discover(board, _FakeClient("<main><h2>Open roles</h2></main>"))


@pytest.mark.asyncio
async def test_discover_keeps_opportunity_on_inclusive_utc_deadline():
    today = datetime.now(UTC).date().isoformat()
    board = {
        "board_url": "https://example.com/tenders",
        "metadata": {
            "steps": [
                {"tag": "h3", "field": "title"},
                {"tag": "p", "field": "description", "stop_tag": "h3", "html": True},
            ],
            "valid_through_regex": r"Deadline:\s*(\d{4}-\d{2}-\d{2})",
            "exclude_expired": True,
        },
    }

    jobs = await discover(
        board,
        _FakeClient(f"<h3>Consultant</h3><p>Deadline: {today}</p>"),
    )

    assert [job.title for job in jobs] == ["Consultant"]
    assert jobs[0].extras == {"valid_through": today}


@pytest.mark.asyncio
async def test_discover_exclude_expired_fails_closed_without_deadline():
    html = "<h3>Consultant</h3><p>Submit a proposal.</p>"
    board = {
        "board_url": "https://example.com/tenders",
        "metadata": {
            "steps": [
                {"tag": "h3", "field": "title"},
                {"tag": "p", "field": "description"},
            ],
            "valid_through_regex": r"Deadline:\s*(\d{4}-\d{2}-\d{2})",
            "exclude_expired": True,
        },
    }

    with pytest.raises(ValueError, match="requires valid_through"):
        await discover(board, _FakeClient(html))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"valid_through_regex": ""}, "valid_through_regex"),
        ({"valid_through_regex": "Deadline"}, "capture group"),
        ({"valid_through_regex": "("}, "invalid"),
        ({"valid_through_regex": "x" * 2_049}, "valid_through_regex"),
        ({"valid_through_format": ""}, "valid_through_format"),
        ({"valid_through_format": 123}, "valid_through_format"),
        ({"valid_through_patterns": []}, "valid_through_patterns"),
        ({"valid_through_patterns": [{"regex": "Deadline"}]}, "capture group"),
        (
            {
                "valid_through_regex": r"(\d{4}-\d{2}-\d{2})",
                "valid_through_patterns": [{"regex": r"(\d{4})"}],
            },
            "cannot be combined",
        ),
        ({"exclude_expired": "true"}, "exclude_expired"),
        ({"require_zero_proof": "true"}, "require_zero_proof"),
    ],
)
async def test_discover_rejects_invalid_valid_through_config(metadata, message):
    board = {
        "board_url": "https://example.com/tenders",
        "metadata": {
            "steps": [{"tag": "h3", "field": "title"}],
            **metadata,
        },
    }

    with pytest.raises(ValueError, match=message):
        await discover(board, _FakeClient("<h3>Consultant</h3>"))


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
async def test_discover_excludes_non_job_cards_by_regex_and_continues():
    html = """
    <html><body><div id="accordion">
      <div class="title-job">Call for tender - Driver distraction study</div>
      <div class="content"><p>Submit a research proposal.</p></div>
      <div class="title-job">Policy Officer</div>
      <div class="content"><p>Develop and coordinate mobility policy.</p></div>
      <div class="title-job">Request for proposals - Event support</div>
      <div class="content"><p>Provide event services.</p></div>
      <div class="title-job">Communications Intern</div>
      <div class="content"><p>Support publications and events.</p></div>
    </div></body></html>
    """
    board = {
        "board_url": "https://example.com/opportunities",
        "metadata": {
            "fetch_contains": 'class="title-job"',
            "steps": [
                {"tag": "div", "attr": "class=title-job", "field": "title"},
                {
                    "tag": "p",
                    "field": "description",
                    "html": True,
                    "stop_attr": "class=title-job",
                },
            ],
            "exclude_title_regex": (r"(?i)\b(?:call\s+for\s+tender|request\s+for\s+proposals?)\b"),
            "defaults": {"locations": ["Brussels, Belgium"]},
        },
    }

    jobs = await discover(board, _FakeClient(html))

    assert [job.title for job in jobs] == ["Policy Officer", "Communications Intern"]
    assert jobs[0].description == "<p>Develop and coordinate mobility policy.</p>"
    assert jobs[1].description == "<p>Support publications and events.</p>"
    assert all(job.locations == ["Brussels, Belgium"] for job in jobs)


@pytest.mark.asyncio
async def test_discover_excludes_placeholder_description_without_suppressing_title():
    metadata = {
        "item_boundary_tag": "h5",
        "steps": [
            {"tag": "h5", "field": "title"},
            {"tag": "p", "field": "description", "html": True, "to_end": True},
        ],
        "exclude_description_regex": r"^More details to come[.]",
    }
    placeholder = """
    <h5>Program Manager</h5>
    <p>More details to come.</p>
    <h2>Apply Now</h2><p>Upload Resume</p>
    """
    substantive = """
    <h5>Program Manager</h5>
    <p>Lead cross-functional product delivery and customer programs.</p>
    """

    assert (
        await discover(
            {"board_url": "https://example.com/careers", "metadata": metadata},
            _FakeClient(placeholder),
        )
        == []
    )

    jobs = await discover(
        {"board_url": "https://example.com/careers", "metadata": metadata},
        _FakeClient(substantive),
    )

    assert [job.title for job in jobs] == ["Program Manager"]
    assert jobs[0].description == (
        "<p>Lead cross-functional product delivery and customer programs.</p>"
    )


@pytest.mark.asyncio
async def test_discover_fetch_contains_fails_closed_when_item_marker_drifts():
    board = {
        "board_url": "https://example.com/opportunities",
        "metadata": {
            "fetch_contains": 'class="title-job"',
            "steps": [{"tag": "div", "attr": "class=title-job", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="omitted required text"):
        await discover(board, _FakeClient('<div id="accordion"></div>'))


@pytest.mark.asyncio
async def test_discover_empty_text_short_circuits_stale_inline_items():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "empty_selector": ".empty-state:not(.hidden)",
            "empty_text": "No vacancies are currently available",
            "steps": [
                {"tag": "h2", "field": "title"},
                {"tag": "p", "field": "description", "stop_tag": "h2"},
            ],
        },
    }
    html = """
    <div class="empty-state"><h5>No vacancies are currently available.</h5></div>
    <h2>Retained legacy vacancy</h2>
    <p>This closed role remains in the page source.</p>
    """

    jobs = await discover(board, _FakeClient(html))

    assert jobs == []


@pytest.mark.asyncio
async def test_discover_empty_text_ignores_hidden_inactive_marker():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "empty_selector": ".empty-state:not(.inactive)",
            "empty_text": "No vacancies are currently available",
            "steps": [
                {"tag": "h2", "field": "title"},
                {"tag": "p", "field": "description", "stop_tag": "h2"},
            ],
        },
    }
    html = """
    <div class="empty-state inactive"><p>No vacancies are currently available.</p></div>
    <h2>Open Engineer</h2>
    <p>Build the product.</p>
    """

    jobs = await discover(board, _FakeClient(html))

    assert [job.title for job in jobs] == ["Open Engineer"]


@pytest.mark.asyncio
async def test_discover_explicit_empty_fails_closed_when_marker_and_items_are_absent():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "empty_selector": ".empty-state:not(.hidden)",
            "empty_text": "No vacancies are currently available",
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="did not match the configured explicit empty state"):
        await discover(board, _FakeClient("<main></main>"))


@pytest.mark.asyncio
async def test_discover_nonempty_selector_overrides_shared_heading_empty_marker():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "empty_selector": ".listing-heading",
            "empty_text": "Currently open positions",
            "nonempty_selector": ".application h4",
            "item_boundary_tag": "h4",
            "steps": [
                {"tag": "h4", "field": "title"},
                {"tag": "div", "attr": "class=description", "field": "description"},
            ],
            "defaults": {"locations": ["Basel, Switzerland"]},
        },
    }
    html = """
    <h2 class="listing-heading">Currently open positions</h2>
    <div class="application">
      <h4>Researcher</h4>
      <div class="description">Study molecular systems.</div>
    </div>
    """

    jobs = await discover(board, _FakeClient(html))

    assert [job.title for job in jobs] == ["Researcher"]


@pytest.mark.asyncio
async def test_discover_nonempty_selector_accepts_shared_heading_when_items_absent():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "empty_selector": ".listing-heading",
            "empty_text": "Currently open positions",
            "nonempty_selector": ".application h4",
            "steps": [{"tag": "h4", "field": "title"}],
        },
    }

    jobs = await discover(
        board,
        _FakeClient('<h2 class="listing-heading">Currently open positions</h2>'),
    )

    assert jobs == []


@pytest.mark.asyncio
async def test_discover_explicit_empty_fails_closed_when_all_items_are_excluded():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "empty_selector": ".empty-state:not(.hidden)",
            "empty_text": "No vacancies are currently available",
            "exclude_titles": ["Retained legacy vacancy"],
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="did not match the configured explicit empty state"):
        await discover(board, _FakeClient("<h2>Retained legacy vacancy</h2>"))


@pytest.mark.asyncio
async def test_discover_require_zero_proof_accepts_positive_extraction():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "require_zero_proof": True,
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    jobs = await discover(board, _FakeClient("<h2>Open Engineer</h2>"))

    assert [job.title for job in jobs] == ["Open Engineer"]


@pytest.mark.asyncio
async def test_discover_can_reuse_title_as_description():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "description_from_title": True,
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    jobs = await discover(board, _FakeClient("<h2>Open Engineer</h2>"))

    assert [(job.title, job.description) for job in jobs] == [("Open Engineer", "Open Engineer")]


@pytest.mark.asyncio
async def test_discover_expands_aggregate_listing_into_stable_position_identities():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "positions_per_listing": 3,
            "steps": [
                {
                    "tag": "p",
                    "field": "title",
                    "regex": r"^There are three open (PhD position)s\.$",
                },
                {"tag": "p", "field": "description"},
            ],
        },
    }

    jobs = await discover(
        board,
        _FakeClient("<p>There are three open PhD positions.</p><p>Research interfaces.</p>"),
    )

    assert [job.title for job in jobs] == ["PhD position"] * 3
    assert len({job.url for job in jobs}) == 3
    assert jobs[1].url.endswith("-2")
    assert jobs[2].url.endswith("-3")
    assert all(job.description == "Research interfaces." for job in jobs)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, False, 0, 21, 1.5, "3", [], {}])
async def test_discover_rejects_invalid_positions_per_listing(value):
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "positions_per_listing": value,
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="positions_per_listing must be an integer from 1 to 20"):
        await discover(board, _FakeClient("<h2>Open Engineer</h2>"))


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
async def test_discover_rejects_invalid_description_from_title(value):
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "description_from_title": value,
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="description_from_title must be a boolean"):
        await discover(board, _FakeClient("<h2>Open Engineer</h2>"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "html",
    [
        "<main></main>",
        "<main><p>Open Engineer</p><p>Build the product.</p></main>",
    ],
)
async def test_discover_require_zero_proof_fails_closed_when_extraction_returns_zero(html):
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "require_zero_proof": True,
            "steps": [{"tag": "h2", "field": "title", "optional": True}],
        },
    }

    with pytest.raises(ValueError, match="without authoritative empty-state proof"):
        await discover(board, _FakeClient(html))


@pytest.mark.asyncio
async def test_discover_require_zero_proof_accepts_authoritative_empty_marker():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "require_zero_proof": True,
            "empty_selector": ".empty-state:not(.hidden)",
            "empty_text": "No vacancies are currently available",
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    jobs = await discover(
        board,
        _FakeClient('<div class="empty-state">No vacancies are currently available.</div>'),
    )

    assert jobs == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
async def test_discover_rejects_invalid_require_zero_proof(value):
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "require_zero_proof": value,
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="require_zero_proof must be a boolean"):
        await discover(board, _FakeClient("<h2>Open Engineer</h2>"))


@pytest.mark.asyncio
async def test_discover_item_boundary_prevents_cross_item_field_bleed():
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "item_boundary_tag": "h2",
            "steps": [
                {"tag": "h2", "field": "title"},
                {
                    "text": "Location:",
                    "field": "location",
                    "regex": r"Location:\s*(.+)",
                    "optional": True,
                },
                {"tag": "p", "field": "description", "stop_tag": "h2"},
            ],
        },
    }
    html = """
    <h2>Role without a location field</h2>
    <p>Own first-role description.</p>
    <h2>Role with a location field</h2>
    <p>Location: Lausanne</p>
    <p>Own second-role description.</p>
    """

    jobs = await discover(board, _FakeClient(html))

    assert [job.title for job in jobs] == [
        "Role without a location field",
        "Role with a location field",
    ]
    assert jobs[0].locations is None
    assert jobs[0].description == "Own first-role description."
    assert jobs[1].locations == ["Lausanne"]
    assert jobs[1].description == "Own second-role description."


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", 123, "x" * 513, "bad\x00marker"])
async def test_discover_rejects_invalid_empty_text(value):
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "empty_text": value,
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="empty_text"):
        await discover(board, _FakeClient("<h2>Engineer</h2>"))


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "[", 123, "x" * 257, "bad\x00selector"])
async def test_discover_rejects_invalid_empty_selector(value):
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "empty_selector": value,
            "empty_text": "No vacancies are currently available",
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="empty_selector"):
        await discover(board, _FakeClient("<h2>Engineer</h2>"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {"empty_text": "No vacancies"},
        {"empty_selector": ".empty-state"},
    ],
)
async def test_discover_requires_complete_explicit_empty_contract(metadata):
    metadata["steps"] = [{"tag": "h2", "field": "title"}]
    board = {"board_url": "https://example.com/jobs", "metadata": metadata}

    with pytest.raises(ValueError, match="requires empty_selector and empty_text"):
        await discover(board, _FakeClient("<h2>Engineer</h2>"))


@pytest.mark.asyncio
@pytest.mark.parametrize("steps", [None, []])
async def test_discover_explicit_empty_requires_non_empty_steps(steps):
    metadata = {
        "empty_selector": ".empty-state:not(.hidden)",
        "empty_text": "No vacancies are currently available",
    }
    if steps is not None:
        metadata["steps"] = steps
    board = {"board_url": "https://example.com/jobs", "metadata": metadata}

    with pytest.raises(ValueError, match="explicit empty state requires non-empty steps"):
        await discover(board, _FakeClient('<div class="empty-state">No vacancies</div>'))


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", 123, "h2 > p", "x" * 33])
async def test_discover_rejects_invalid_item_boundary_tag(value):
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "item_boundary_tag": value,
            "steps": [{"tag": "h2", "field": "title"}],
        },
    }

    with pytest.raises(ValueError, match="item_boundary_tag"):
        await discover(board, _FakeClient("<h2>Engineer</h2>"))


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "(", 123, "x" * 2_049])
async def test_discover_rejects_invalid_exclude_title_regex(value):
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "steps": [{"tag": "h3", "field": "title"}],
            "exclude_title_regex": value,
        },
    }

    with pytest.raises(ValueError, match="exclude_title_regex"):
        await discover(board, _FakeClient("<h3>Engineer</h3>"))


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "(", 123, "x" * 2_049])
async def test_discover_rejects_invalid_exclude_description_regex(value):
    board = {
        "board_url": "https://example.com/jobs",
        "metadata": {
            "steps": [{"tag": "h3", "field": "title"}],
            "exclude_description_regex": value,
        },
    }

    with pytest.raises(ValueError, match="exclude_description_regex"):
        await discover(board, _FakeClient("<h3>Engineer</h3>"))


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
