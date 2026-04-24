"""Tests for the Jinja task renderer.

Verifies every template renders with sample variables (StrictUndefined
catches forgotten placeholders) and the rendered text contains the
expected markers.
"""

from __future__ import annotations

import pytest

from src.labeller.render import TASKS, render_task


@pytest.fixture
def input_data() -> dict:
    return {
        "id": "test-id-1",
        "input": {
            "title_raw": "Senior Backend Engineer, Payments",
            "description_text": "",
            "description_html": "",
            "description_locale_detected": "en",
            "blocks": [
                {"id": 0, "tag": "h2", "html": "<h2>About us</h2>", "text": "About us"},
                {
                    "id": 1,
                    "tag": "p",
                    "html": "<p>Stripe builds the economic infrastructure for the internet.</p>",
                    "text": "Stripe builds the economic infrastructure for the internet.",
                },
                {"id": 2, "tag": "h2", "html": "<h2>The role</h2>", "text": "The role"},
                {
                    "id": 3,
                    "tag": "p",
                    "html": "<p>You'll design distributed systems.</p>",
                    "text": "You'll design distributed systems.",
                },
            ],
        },
    }


@pytest.fixture
def sections_data() -> dict:
    return {
        "sections": [
            {"kind": "company", "block_ids": [0, 1]},
            {"kind": "role", "block_ids": [2, 3]},
        ]
    }


def test_render_split_sections_contains_blocks(input_data):
    md = render_task(
        "split_sections",
        input_data=input_data,
        output_path="/tmp/out.json",
    )
    assert "Senior Backend Engineer" in md
    assert "[0]" in md and "[1]" in md
    assert "About us" in md
    assert "closed vocab" in md.lower()
    assert "company" in md
    assert "legal" in md


def test_render_extract_company_isolates_section(input_data, sections_data):
    md = render_task(
        "extract_company",
        input_data=input_data,
        sections_data=sections_data,
        kind="company",
        output_path="/tmp/out.json",
    )
    assert "Stripe builds" in md
    # Role section's text should NOT appear in the company-extract render
    assert "distributed systems" not in md


def test_render_extract_role_isolates_section(input_data, sections_data):
    md = render_task(
        "extract_role",
        input_data=input_data,
        sections_data=sections_data,
        kind="role",
        output_path="/tmp/out.json",
    )
    assert "distributed systems" in md
    assert "Stripe builds" not in md


def test_render_globals_includes_header_blocks(input_data):
    sections = {"sections": [{"kind": "role", "block_ids": [3]}]}
    md = render_task(
        "extract_globals",
        input_data=input_data,
        sections_data=sections,
        output_path="/tmp/out.json",
    )
    # Blocks 0, 1, 2 are unclaimed -> go into header_blocks
    assert "About us" in md
    assert "Stripe builds" in md


def test_previous_error_surface_in_retry(input_data):
    md = render_task(
        "split_sections",
        input_data=input_data,
        output_path="/tmp/out.json",
        previous_error="block_id 42 does not exist",
    )
    assert "Previous attempt failed" in md
    assert "42" in md


@pytest.mark.parametrize("task", sorted(TASKS))
def test_every_task_renders_with_fixtures(task, input_data, sections_data):
    """Smoke test — all 9 templates must render without StrictUndefined errors."""
    kwargs = {
        "input_data": input_data,
        "output_path": "/tmp/out.json",
    }
    if task.startswith("extract_") and task != "extract_globals":
        kwargs["sections_data"] = sections_data
        kwargs["kind"] = task.removeprefix("extract_")
    elif task == "extract_globals":
        kwargs["sections_data"] = sections_data
    rendered = render_task(task, **kwargs)
    assert rendered.strip()  # non-empty
    assert "{{" not in rendered  # no unrendered variables
