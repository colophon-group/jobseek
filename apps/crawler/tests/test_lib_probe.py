"""Tests for src.workspace.lib.probe — pure async probe functions."""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workspace.lib import (
    BoardConfigState,
    ProbeEntry,
    ProbeMonitorResult,
    ProbeScraperResult,
    WsConfigMissing,
    WsProbeFailed,
    probe_monitor,
    probe_scraper,
)
from src.workspace.lib.probe import (
    estimate_initial_load,
    estimate_monitor_cost,
    score_probe_entries,
)

# ── Fixtures / helpers ─────────────────────────────────────────────────


def _fixture_state(url: str = "https://test.example.com/jobs", **overrides) -> BoardConfigState:
    return BoardConfigState(board_url=url, alias="careers", slug="test", **overrides)


@pytest.fixture
def patched_playwright_and_http():
    """Patch Playwright async_playwright + http client used by probe lib."""
    with (
        patch("playwright.async_api.async_playwright") as pw_factory,
        patch("src.shared.http.create_http_client") as http_factory,
    ):
        pw_ctx = AsyncMock()
        pw_ctx.__aenter__.return_value = MagicMock(name="playwright")
        pw_ctx.__aexit__.return_value = False
        pw_factory.return_value = pw_ctx

        http_client = AsyncMock()
        http_client.aclose = AsyncMock()
        http_factory.return_value = http_client

        yield pw_factory, http_factory, http_client


# ── probe_monitor ──────────────────────────────────────────────────────


class TestProbeMonitorHappyPath:
    @pytest.mark.asyncio
    async def test_returns_entries_and_scored(self, patched_playwright_and_http):
        with patch("src.core.monitors.probe_all_monitors", new_callable=AsyncMock) as probe_fn:
            probe_fn.return_value = [
                ("greenhouse", {"token": "acme"}, "GH detected"),
                ("sitemap", {"urls": 12}, "sitemap"),
                ("dom", None, "no jobs"),
            ]
            result = await probe_monitor(_fixture_state(), expected_count=200)

        assert isinstance(result, ProbeMonitorResult)
        assert result.board_url == "https://test.example.com/jobs"
        assert result.current_jobs == 200
        assert [e.name for e in result.entries] == ["greenhouse", "sitemap", "dom"]
        # Scored mirrors entries; one rich (greenhouse) + URL-only (sitemap) + undetected (dom)
        rich_entries = [s for s in result.scored if s.rich]
        assert len(rich_entries) == 1
        assert rich_entries[0].name == "greenhouse"
        # Detected URL-only entry has cost
        sitemap = next(s for s in result.scored if s.name == "sitemap")
        assert sitemap.monitor_cost is not None
        # Undetected entry has None cost
        dom = next(s for s in result.scored if s.name == "dom")
        assert dom.monitor_cost is None

    @pytest.mark.asyncio
    async def test_state_is_not_mutated(self, patched_playwright_and_http):
        state = _fixture_state()
        before = state.to_dict()
        with patch("src.core.monitors.probe_all_monitors", new_callable=AsyncMock) as probe_fn:
            probe_fn.return_value = []
            await probe_monitor(state, expected_count=5)
        # Frozen dataclass — mutation would have raised, but verify dict snapshot too.
        assert state.to_dict() == before

    @pytest.mark.asyncio
    async def test_no_filesystem_writes(self, patched_playwright_and_http, tmp_path, monkeypatch):
        """probe_monitor must not create or write to .workspace/."""
        monkeypatch.chdir(tmp_path)
        before_listing = sorted(os.listdir("."))
        with patch("src.core.monitors.probe_all_monitors", new_callable=AsyncMock) as probe_fn:
            probe_fn.return_value = [("sitemap", {"urls": 1}, "ok")]
            await probe_monitor(_fixture_state(), expected_count=1)
        after_listing = sorted(os.listdir("."))
        assert before_listing == after_listing
        assert not (tmp_path / ".workspace").exists()


class TestProbeMonitorErrorCases:
    @pytest.mark.asyncio
    async def test_invalid_url_raises_typed(self, patched_playwright_and_http):
        with patch("src.core.monitors.probe_all_monitors", new_callable=AsyncMock) as probe_fn:
            probe_fn.side_effect = ValueError("Invalid URL: not-a-url")
            with pytest.raises(WsProbeFailed) as exc_info:
                await probe_monitor(_fixture_state(url="not-a-url"), expected_count=10)
        assert "Invalid URL" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, ValueError)

    @pytest.mark.asyncio
    async def test_timeout_5xx_raises_typed(self, patched_playwright_and_http):
        with patch("src.core.monitors.probe_all_monitors", new_callable=AsyncMock) as probe_fn:
            probe_fn.side_effect = TimeoutError("upstream 504")
            with pytest.raises(WsProbeFailed):
                await probe_monitor(_fixture_state(), expected_count=10)


class TestProbeMonitorOutputSchema:
    @pytest.mark.asyncio
    async def test_to_dict_is_json_serializable(self, patched_playwright_and_http):
        with patch("src.core.monitors.probe_all_monitors", new_callable=AsyncMock) as probe_fn:
            probe_fn.return_value = [
                ("sitemap", {"urls": 12, "sitemap_url": "https://x/s.xml"}, "ok"),
                ("dom", None, "no detect"),
            ]
            result = await probe_monitor(_fixture_state(), expected_count=42)
        d = result.to_dict()
        # Round-trip through JSON ensures the snapshot stays JSON-safe
        round_trip = json.loads(json.dumps(d))
        assert round_trip["board_url"] == "https://test.example.com/jobs"
        assert round_trip["current_jobs"] == 42
        assert {"name", "metadata", "comment"} <= set(round_trip["entries"][0].keys())
        assert {"monitor_cost", "rich", "initial_load"} <= set(round_trip["scored"][0].keys())


# ── probe_scraper ──────────────────────────────────────────────────────


class TestProbeScraperHappyPath:
    @pytest.mark.asyncio
    async def test_returns_entries_and_spa_flag(self, patched_playwright_and_http):
        with patch("src.core.scrapers.probe_scrapers", new_callable=AsyncMock) as probe_fn:
            probe_fn.return_value = (
                [
                    ("json-ld", {"titles": 2, "descriptions": 2, "config": {}}, "ok"),
                    ("dom", None, "no schema"),
                ],
                False,
            )
            state = _fixture_state(sample_urls=("https://test.example.com/jobs/1",))
            result = await probe_scraper(state)
        assert isinstance(result, ProbeScraperResult)
        assert result.spa_suspect is False
        assert {e.name for e in result.entries} == {"json-ld", "dom"}

    @pytest.mark.asyncio
    async def test_explicit_sample_urls_override_state(self, patched_playwright_and_http):
        urls_seen: list[list[str]] = []

        async def _fake_probe(urls, http, pw):
            urls_seen.append(list(urls))
            return ([("json-ld", {"titles": 1, "descriptions": 1}, "ok")], False)

        with patch("src.core.scrapers.probe_scrapers", side_effect=_fake_probe):
            state = _fixture_state(sample_urls=("https://example.com/old/1",))
            await probe_scraper(state, sample_urls=["https://example.com/new/1"])
        assert urls_seen == [["https://example.com/new/1"]]

    @pytest.mark.asyncio
    async def test_no_filesystem_writes(self, patched_playwright_and_http, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = sorted(os.listdir("."))
        with patch("src.core.scrapers.probe_scrapers", new_callable=AsyncMock) as probe_fn:
            probe_fn.return_value = ([("json-ld", None, "no schema")], False)
            await probe_scraper(_fixture_state(sample_urls=("https://example.com/x",)))
        assert sorted(os.listdir(".")) == before


class TestProbeScraperErrorCases:
    @pytest.mark.asyncio
    async def test_no_sample_urls_raises_config_missing(self, patched_playwright_and_http):
        state = _fixture_state(sample_urls=())
        with pytest.raises(WsConfigMissing):
            await probe_scraper(state)

    @pytest.mark.asyncio
    async def test_upstream_failure_wraps(self, patched_playwright_and_http):
        with patch("src.core.scrapers.probe_scrapers", new_callable=AsyncMock) as probe_fn:
            probe_fn.side_effect = RuntimeError("playwright crashed")
            state = _fixture_state(sample_urls=("https://example.com/x",))
            with pytest.raises(WsProbeFailed):
                await probe_scraper(state)


class TestProbeScraperOutputSchema:
    @pytest.mark.asyncio
    async def test_to_dict_round_trips(self, patched_playwright_and_http):
        with patch("src.core.scrapers.probe_scrapers", new_callable=AsyncMock) as probe_fn:
            probe_fn.return_value = (
                [("json-ld", {"titles": 1, "descriptions": 1, "config": {}}, "ok")],
                True,
            )
            state = _fixture_state(sample_urls=("https://example.com/x",))
            result = await probe_scraper(state)
        d = json.loads(json.dumps(result.to_dict()))
        assert d["spa_suspect"] is True
        assert d["sample_urls"] == ["https://example.com/x"]
        assert d["entries"][0]["name"] == "json-ld"


# ── Cost helpers (pure-functional, deterministic) ──────────────────────


class TestCostHelpers:
    def test_estimate_monitor_cost_sitemap(self):
        assert estimate_monitor_cost("sitemap", 100) == 1.5

    def test_estimate_monitor_cost_dom(self):
        assert estimate_monitor_cost("dom", 100) == 1.0

    def test_estimate_monitor_cost_unknown_default(self):
        assert estimate_monitor_cost("zzz_unknown", 100) == 2.0

    def test_estimate_monitor_cost_api_sniffer_browser(self):
        # browser=True path: base 5.0 + 0.5 per page
        cost = estimate_monitor_cost("api_sniffer", 100, {"items": 50, "browser": True})
        assert cost == pytest.approx(5.0 + 0.5 * 2)

    def test_estimate_initial_load(self):
        assert estimate_initial_load(100) == pytest.approx(30.0)

    def test_score_entries_marks_rich_for_api_sniffer_with_fields(self):
        entries = [
            ProbeEntry(
                name="api_sniffer", metadata={"fields": ["title"], "items": 10}, comment="ok"
            ),
            ProbeEntry(name="sitemap", metadata={"urls": 5}, comment="ok"),
            ProbeEntry(name="dom", metadata=None, comment="no"),
        ]
        scored = score_probe_entries(entries, current_jobs=100)
        assert scored[0].rich is True
        assert scored[1].rich is False
        assert scored[2].monitor_cost is None

    def test_score_entries_is_pure(self):
        """Same input → same output."""
        entries = [ProbeEntry(name="sitemap", metadata={"urls": 5}, comment="ok")]
        a = score_probe_entries(entries, 100)
        b = score_probe_entries(entries, 100)
        assert [s.to_dict() for s in a] == [s.to_dict() for s in b]


# ── Determinism ────────────────────────────────────────────────────────


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_same_input_same_output(self, patched_playwright_and_http):
        """Modulo HTTP nondeterminism, identical mocked inputs → identical outputs."""
        fixture = [
            ("sitemap", {"urls": 5}, "ok"),
            ("dom", None, "no"),
        ]
        with patch("src.core.monitors.probe_all_monitors", new_callable=AsyncMock) as probe_fn:
            probe_fn.return_value = fixture
            r1 = await probe_monitor(_fixture_state(), expected_count=10)
        with patch("src.core.monitors.probe_all_monitors", new_callable=AsyncMock) as probe_fn:
            probe_fn.return_value = list(fixture)
            r2 = await probe_monitor(_fixture_state(), expected_count=10)
        assert r1.to_dict() == r2.to_dict()
