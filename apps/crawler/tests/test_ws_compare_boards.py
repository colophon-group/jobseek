"""Tests for cross-board overlap detection in the workspace CLI."""

from __future__ import annotations

from src.workspace.commands import crawl


def test_compare_boards_detects_cross_ats_subset_from_html_titles(monkeypatch):
    jobs = {
        "current": [
            {"url": "https://new.example/1", "title": "<b>Engineer &amp; Planner</b>"},
            {"url": "https://new.example/2", "title": "<b>Grid Operator</b>"},
            {"url": "https://new.example/3", "title": "<b>Project Lead</b>"},
            {"url": "https://new.example/4", "title": "<b>Logistics Specialist</b>"},
        ],
        "legacy": [
            {"url": "https://old.example/a", "title": "Engineer & Planner"},
            {"url": "https://old.example/b", "title": "Grid  Operator"},
            {"url": "https://old.example/c", "title": "Project Lead"},
            {"url": "https://old.example/d", "title": "Logistics Specialist"},
            {"url": "https://old.example/e", "title": "Legacy Translation"},
            {"url": "https://old.example/f", "title": "Speculative Application"},
        ],
    }
    monkeypatch.setattr(crawl, "_latest_jobs_json", lambda _slug, alias: jobs[alias])

    result = crawl._compare_two_boards("company", "current", "legacy")

    assert result["shared_urls"] == 0
    assert result["shared_titles"] == 4
    assert result["title_overlap_pct_a"] == 100
    assert result["title_overlap_pct_b"] == 67
    assert result["relationship"] == "subset"


def test_compare_boards_ignores_single_generic_title_match(monkeypatch):
    jobs = {
        "one": [{"url": "https://one.example/1", "title": "Software Engineer"}],
        "two": [{"url": "https://two.example/a", "title": "Software Engineer"}],
    }
    monkeypatch.setattr(crawl, "_latest_jobs_json", lambda _slug, alias: jobs[alias])

    result = crawl._compare_two_boards("company", "one", "two")

    assert result["shared_titles"] == 1
    assert result["relationship"] == "independent"
