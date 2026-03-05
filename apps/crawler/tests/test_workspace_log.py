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
                {
                    "ts": "2026-03-03T14:23:00Z",
                    "cmd": "add board",
                    "ok": True,
                    "msg": "Added careers",
                },
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
                "slug": "test-careers",
                "active_config": "sitemap",
                "configs": {
                    "sitemap": {
                        "monitor_type": "sitemap",
                        "scraper_type": "json-ld",
                        "run": {"jobs": 138, "time": 4.2},
                        "scraper_run": {"avg_time": 1.1},
                        "cost": {"monitor_per_cycle": 4.2},
                        "feedback": {"verdict": "good"},
                    },
                },
            }
        }
        stats = format_crawl_stats(boards)
        assert "<!-- crawl-stats" in stats
        # Per-board row with slug, monitor, jobs, cost, verdict columns
        assert "test-careers" in stats
        assert "`sitemap`" in stats
        assert "138" in stats
        assert "~4.2s" in stats
        assert "**good**" in stats
        # Table header
        assert "| Board |" in stats

    def test_api_monitor_no_scraper(self):
        boards = {
            "careers": {
                "slug": "test-careers",
                "active_config": "greenhouse",
                "configs": {
                    "greenhouse": {
                        "monitor_type": "greenhouse",
                        "run": {"jobs": 50, "time": 2.0},
                    },
                },
            }
        }
        stats = format_crawl_stats(boards)
        assert "`greenhouse`" in stats
        assert "50" in stats

    def test_verdict_in_metrics_table(self):
        boards = {
            "careers": {
                "slug": "test-careers",
                "active_config": "sitemap",
                "configs": {
                    "sitemap": {
                        "monitor_type": "sitemap",
                        "scraper_type": "dom",
                        "status": "tested",
                        "run": {"jobs": 10, "time": 1.0},
                        "scraper_run": {"avg_time": 0.5},
                        "feedback": {
                            "verdict": "acceptable",
                            "fields": {
                                "title": "clean",
                                "description": "clean",
                                "locations": "noisy",
                            },
                        },
                    },
                },
            }
        }
        stats = format_crawl_stats(boards)
        # Verdict appears in the board row
        assert "**acceptable**" in stats
        # Field coverage is NOT in the stats comment (lives in PR body only)
        assert "Field Coverage" not in stats
        assert "Required" not in stats

    def test_multi_board_total_row(self):
        boards = {
            "careers": {
                "slug": "kpmg-careers",
                "active_config": "dom",
                "configs": {
                    "dom": {
                        "monitor_type": "dom",
                        "run": {"jobs": 56, "time": 12.0},
                        "cost": {"monitor_per_cycle": 12.0},
                        "feedback": {"verdict": "good"},
                    },
                },
            },
            "fr": {
                "slug": "kpmg-fr",
                "active_config": "dom",
                "configs": {
                    "dom": {
                        "monitor_type": "dom",
                        "run": {"jobs": 217, "time": 5.0},
                        "cost": {"monitor_per_cycle": 5.0},
                        "feedback": {"verdict": "acceptable"},
                    },
                },
            },
        }
        stats = format_crawl_stats(boards)
        # Both boards appear as rows
        assert "kpmg-careers" in stats
        assert "kpmg-fr" in stats
        # Total row with summed jobs
        assert "**Total**" in stats
        assert "**273**" in stats
        # JSON marker has summed values
        assert '"jobs": 273' in stats
        assert '"monitor_time": 17.0' in stats
