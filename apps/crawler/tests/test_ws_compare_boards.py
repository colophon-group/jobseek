"""Tests for cross-board overlap detection in the workspace CLI."""

from __future__ import annotations

from src.workspace.commands import crawl


def test_compare_boards_detects_cross_ats_subset_from_html_titles(monkeypatch):
    jobs = {
        "current": [
            {
                "url": "https://new.example/1",
                "title": "<b>Engineer &amp; Planner</b>",
                "locations": ["Poschiavo", "Zurich"],
            },
            {
                "url": "https://new.example/2",
                "title": "<b>Grid Operator</b>",
                "locations": ["Zurich"],
            },
            {
                "url": "https://new.example/3",
                "title": "<b>Project Lead</b>",
                "locations": ["Milan"],
            },
            {
                "url": "https://new.example/4",
                "title": "<b>Logistics Specialist</b>",
                "locations": ["Zurich"],
            },
        ],
        "legacy": [
            {
                "url": "https://old.example/a",
                "title": "Engineer & Planner",
                "locations": ["Zurich", "Poschiavo"],
            },
            {
                "url": "https://old.example/b",
                "title": "Grid  Operator",
                "locations": "Zurich",
            },
            {
                "url": "https://old.example/c",
                "title": "Project Lead",
                "locations": ["Milan"],
            },
            {
                "url": "https://old.example/d",
                "title": "Logistics Specialist",
                "locations": ["Zurich"],
            },
            {
                "url": "https://old.example/e",
                "title": "Legacy Translation",
                "locations": ["Poschiavo"],
            },
            {
                "url": "https://old.example/f",
                "title": "Speculative Application",
                "locations": ["Milan"],
            },
        ],
    }
    monkeypatch.setattr(crawl, "_latest_jobs_json", lambda _slug, alias: jobs[alias])

    result = crawl._compare_two_boards("company", "current", "legacy")

    assert result["shared_urls"] == 0
    assert result["shared_titles"] == 4
    assert result["title_overlap_pct_a"] == 100
    assert result["title_overlap_pct_b"] == 67
    assert result["shared_identities"] == 4
    assert result["identity_overlap_pct_a"] == 100
    assert result["identity_overlap_pct_b"] == 67
    assert result["evidence"] == "identities"
    assert result["subset_board"] == "current"
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


def test_compare_boards_ignores_generic_titles_in_different_locations(monkeypatch):
    jobs = {
        "switzerland": [
            {
                "url": f"https://swiss.example/{index}",
                "title": title,
                "locations": ["Zurich"],
            }
            for index, title in enumerate(
                ["Software Engineer", "Project Manager", "HR Specialist", "Accountant"]
            )
        ],
        "italy": [
            {
                "url": f"https://italy.example/{index}",
                "title": title,
                "locations": ["Milan"],
            }
            for index, title in enumerate(
                ["Software Engineer", "Project Manager", "HR Specialist", "Accountant"]
            )
        ],
    }
    monkeypatch.setattr(crawl, "_latest_jobs_json", lambda _slug, alias: jobs[alias])

    result = crawl._compare_two_boards("company", "switzerland", "italy")

    assert result["shared_titles"] == 4
    assert result["shared_identities"] == 0
    assert result["evidence"] == "none"
    assert result["relationship"] == "independent"


def test_compare_boards_reports_url_subset_direction(monkeypatch):
    jobs = {
        "small": [
            {"url": f"https://example.com/{index}", "title": f"Job {index}"} for index in range(4)
        ],
        "large": [
            {"url": f"https://example.com/{index}", "title": f"Job {index}"} for index in range(6)
        ],
    }
    monkeypatch.setattr(crawl, "_latest_jobs_json", lambda _slug, alias: jobs[alias])

    result = crawl._compare_two_boards("company", "small", "large")

    assert result["evidence"] == "urls"
    assert result["subset_board"] == "small"
    assert result["relationship"] == "subset"
