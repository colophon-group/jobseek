"""Tests for workspace artifact storage."""

from __future__ import annotations

import json

from src.workspace.artifacts import (
    save_events,
    save_http_log,
    save_probe,
    save_quality,
)


class TestSaveProbe:
    def test_saves_probe_json(self, tmp_path):
        results = [
            {"name": "greenhouse", "detected": True, "metadata": {"token": "x"}, "comment": "OK"},
            {"name": "lever", "detected": False, "metadata": None, "comment": "Not detected"},
        ]
        save_probe(tmp_path, results)

        data = json.loads((tmp_path / "probe.json").read_text())
        assert len(data) == 2
        assert data[0]["name"] == "greenhouse"
        assert data[0]["detected"] is True
        assert data[1]["detected"] is False


class TestSaveQuality:
    def test_saves_quality_json(self, tmp_path):
        quality = {
            "total": 10,
            "fields": {
                "title": {"count": 10, "pct": 100},
                "description": {"count": 8, "pct": 80},
            },
        }
        save_quality(tmp_path, quality)

        data = json.loads((tmp_path / "quality.json").read_text())
        assert data["total"] == 10
        assert data["fields"]["title"]["count"] == 10

    def test_saves_per_url_quality(self, tmp_path):
        quality = {
            "total": 2,
            "fields": {"title": {"count": 2, "pct": 100}},
            "per_url": [
                {"url": "https://a.com", "fields": {"title": True}},
                {"url": "https://b.com", "fields": {"title": True}},
            ],
        }
        save_quality(tmp_path, quality)

        data = json.loads((tmp_path / "quality.json").read_text())
        assert len(data["per_url"]) == 2


class TestSaveHttpLog:
    def test_saves_entries(self, tmp_path):
        entries = [
            {"method": "GET", "url": "https://example.com", "status": 200, "elapsed": 0.5},
        ]
        save_http_log(tmp_path, entries)

        data = json.loads((tmp_path / "http_log.json").read_text())
        assert len(data) == 1
        assert data[0]["status"] == 200

    def test_skips_empty(self, tmp_path):
        save_http_log(tmp_path, [])
        assert not (tmp_path / "http_log.json").exists()


class TestSaveEvents:
    def test_saves_jsonl(self, tmp_path):
        events = [
            {"event": "dom.complete", "level": "info", "urls_found": 15},
            {"event": "dom.fetch_failed", "level": "warning"},
        ]
        save_events(tmp_path, events)

        lines = (tmp_path / "events.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "dom.complete"
        assert json.loads(lines[1])["level"] == "warning"

    def test_skips_empty(self, tmp_path):
        save_events(tmp_path, [])
        assert not (tmp_path / "events.jsonl").exists()
