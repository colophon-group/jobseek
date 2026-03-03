"""Tests for workspace action log."""

from __future__ import annotations

from src.workspace.log import (
    append,
    append_to_list,
    format_crawl_stats,
    format_transcript,
    read,
)


class TestLogAppend:
    def test_append_creates_file(self, tmp_path):
        log_path = tmp_path / "log.yaml"
        append(log_path, "new", True, "Created workspace")
        entries = read(log_path)
        assert len(entries) == 1
        assert entries[0]["cmd"] == "new"
        assert entries[0]["ok"] is True
        assert entries[0]["msg"] == "Created workspace"

    def test_append_accumulates(self, tmp_path):
        log_path = tmp_path / "log.yaml"
        append(log_path, "new", True, "Created")
        append(log_path, "set", True, "Set name")
        entries = read(log_path)
        assert len(entries) == 2
        assert entries[0]["cmd"] == "new"
        assert entries[1]["cmd"] == "set"

    def test_read_empty(self, tmp_path):
        log_path = tmp_path / "nonexistent.yaml"
        entries = read(log_path)
        assert entries == []


class TestAppendToList:
    def test_append_to_list(self):
        entries: list = []
        append_to_list(entries, "probe", True, "greenhouse ✓")
        assert len(entries) == 1
        assert entries[0]["cmd"] == "probe"


class TestFormatTranscript:
    def test_basic_transcript(self):
        ws_log = [
            {"ts": "2026-03-03T14:22:00Z", "cmd": "new", "ok": True, "msg": "Created workspace"},
            {"ts": "2026-03-03T14:26:00Z", "cmd": "submit", "ok": True, "msg": "Submitted"},
        ]
        board_logs = {
            "careers": [
                {"ts": "2026-03-03T14:23:00Z", "cmd": "add board", "ok": True, "msg": "Added careers"},
                {"ts": "2026-03-03T14:25:00Z", "cmd": "run monitor", "ok": True, "msg": "138 jobs"},
            ],
        }
        transcript = format_transcript(ws_log, board_logs)
        lines = transcript.split("\n")
        assert len(lines) == 4
        assert "new" in lines[0]
        assert "add board" in lines[1]
        assert "run monitor" in lines[2]
        assert "submit" in lines[3]

    def test_empty_logs(self):
        transcript = format_transcript([], {})
        assert transcript == ""


class TestFormatCrawlStats:
    def test_basic_stats(self):
        boards = {
            "careers": {
                "monitor_type": "sitemap",
                "scraper_type": "json-ld",
                "monitor_run": {"jobs": 138, "time": 4.2},
                "scraper_run": {"avg_time": 1.1},
            }
        }
        stats = format_crawl_stats(boards)
        assert "<!-- crawl-stats" in stats
        assert "| Jobs | 138 |" in stats
        assert "| Monitor | `sitemap`" in stats
        assert "4.2s" in stats
        assert "| Scraper | `json-ld`" in stats
        assert "1.1s" in stats

    def test_api_monitor_no_scraper(self):
        boards = {
            "careers": {
                "monitor_type": "greenhouse",
                "monitor_run": {"jobs": 50, "time": 2.0},
            }
        }
        stats = format_crawl_stats(boards)
        assert "| Monitor | `greenhouse`" in stats
        # No scraper row when scraper_type is None
        assert "Scraper" not in stats

    def test_extraction_quality(self):
        boards = {
            "careers": {
                "monitor_type": "sitemap",
                "scraper_type": "dom",
                "monitor_run": {"jobs": 10, "time": 1.0},
                "scraper_run": {
                    "avg_time": 0.5,
                    "count": 3,
                    "titles": 3,
                    "descriptions": 2,
                    "locations": 1,
                },
            }
        }
        stats = format_crawl_stats(boards)
        assert "| Titles | 3/3 |" in stats
        assert "| Descriptions | 2/3 |" in stats
        assert "| Locations | 1/3 |" in stats
