"""Tests for src.workspace.lib.run — pure async run functions."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.workspace.lib import (
    BoardConfigState,
    RunMonitorResult,
    RunScraperResult,
    WsConfigMissing,
    WsMonitorRunFailed,
    WsScraperRunFailed,
    run_monitor,
    run_scraper,
)

# ── Fixtures / helpers ─────────────────────────────────────────────────


@dataclass
class FakeMonitorResult:
    urls: set[str] = field(default_factory=set)
    jobs_by_url: dict | None = None
    filtered_count: int = 0


@dataclass
class FakeJobContent:
    title: str | None = None
    description: str | None = None
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None
    language: str | None = None
    extras: dict | None = None
    metadata: dict | None = None


@dataclass
class FakeDiscoveredJob:
    url: str
    title: str | None = None
    description: str | None = None
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None
    extras: dict | None = None


def _state(**overrides) -> BoardConfigState:
    base = dict(
        board_url="https://test.example.com/jobs",
        alias="careers",
        slug="test",
        monitor_type="sitemap",
        monitor_config={},
        scraper_type="json-ld",
        scraper_config={},
        sample_urls=("https://test.example.com/jobs/1", "https://test.example.com/jobs/2"),
    )
    base.update(overrides)
    return BoardConfigState(**base)


@pytest.fixture
def patched_run_deps():
    """Patch Playwright + logging http for run_monitor / run_scraper."""
    with (
        patch("playwright.async_api.async_playwright") as pw_factory,
        patch("src.shared.http.create_logging_http_client") as http_factory,
    ):
        pw_ctx = AsyncMock()
        pw_ctx.__aenter__.return_value = MagicMock(name="pw")
        pw_ctx.__aexit__.return_value = False
        pw_factory.return_value = pw_ctx

        http_client = AsyncMock()
        http_client.aclose = AsyncMock()
        http_log: list[dict] = []
        http_factory.return_value = (http_client, http_log)

        yield pw_factory, http_factory, http_client, http_log


# ── run_monitor ────────────────────────────────────────────────────────


class TestRunMonitorHappyPath:
    @pytest.mark.asyncio
    async def test_url_only_result(self, patched_run_deps):
        with patch("src.core.monitor.monitor_one", new_callable=AsyncMock) as fake:
            fake.return_value = FakeMonitorResult(
                urls={"https://test.example.com/jobs/1", "https://test.example.com/jobs/2"},
                jobs_by_url=None,
            )
            result = await run_monitor(_state())
        assert isinstance(result, RunMonitorResult)
        assert result.has_rich_data is False
        assert sorted(result.urls) == [
            "https://test.example.com/jobs/1",
            "https://test.example.com/jobs/2",
        ]
        assert result.quality is None

    @pytest.mark.asyncio
    async def test_rich_result_with_quality(self, patched_run_deps):
        jobs = {
            "https://test.example.com/jobs/1": FakeDiscoveredJob(
                url="https://test.example.com/jobs/1",
                title="Eng",
                description="<p>ok</p>",
                locations=["NYC"],
                employment_type="FULL_TIME",
            ),
            "https://test.example.com/jobs/2": FakeDiscoveredJob(
                url="https://test.example.com/jobs/2",
                title="Des",
                description="<p>do</p>",
                locations=["SF"],
            ),
        }
        with patch("src.core.monitor.monitor_one", new_callable=AsyncMock) as fake:
            fake.return_value = FakeMonitorResult(
                urls=set(jobs), jobs_by_url=jobs, filtered_count=3
            )
            result = await run_monitor(_state(monitor_type="greenhouse"))
        assert result.has_rich_data is True
        assert result.filtered_count == 3
        assert result.quality is not None
        assert result.quality["total"] == 2
        assert result.quality["fields"]["title"]["count"] == 2
        assert result.quality["fields"]["employment_type"]["count"] == 1
        # Description samples populated
        assert len(result.description_samples) == 2

    @pytest.mark.asyncio
    async def test_state_not_mutated(self, patched_run_deps):
        state = _state()
        before = state.to_dict()
        with patch("src.core.monitor.monitor_one", new_callable=AsyncMock) as fake:
            fake.return_value = FakeMonitorResult(urls=set(), jobs_by_url=None)
            await run_monitor(state)
        assert state.to_dict() == before

    @pytest.mark.asyncio
    async def test_no_filesystem_writes(self, patched_run_deps, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = sorted(os.listdir("."))
        with patch("src.core.monitor.monitor_one", new_callable=AsyncMock) as fake:
            fake.return_value = FakeMonitorResult(urls=set(), jobs_by_url=None)
            await run_monitor(_state())
        assert sorted(os.listdir(".")) == before
        assert not (tmp_path / ".workspace").exists()


class TestRunMonitorErrorCases:
    @pytest.mark.asyncio
    async def test_missing_monitor_type(self, patched_run_deps):
        with pytest.raises(WsConfigMissing):
            await run_monitor(_state(monitor_type=None))

    @pytest.mark.asyncio
    async def test_invalid_url_wraps_typed(self, patched_run_deps):
        with patch("src.core.monitor.monitor_one", new_callable=AsyncMock) as fake:
            fake.side_effect = ValueError("Cannot derive Greenhouse token from board URL")
            with pytest.raises(WsMonitorRunFailed) as exc_info:
                await run_monitor(_state(monitor_type="greenhouse"))
        assert "Cannot derive" in str(exc_info.value)
        assert isinstance(exc_info.value.__cause__, ValueError)

    @pytest.mark.asyncio
    async def test_timeout_5xx_wraps_typed(self, patched_run_deps):
        with patch("src.core.monitor.monitor_one", new_callable=AsyncMock) as fake:
            fake.side_effect = TimeoutError("upstream 504")
            with pytest.raises(WsMonitorRunFailed):
                await run_monitor(_state())


class TestRunMonitorOutputSchema:
    @pytest.mark.asyncio
    async def test_to_dict_round_trips(self, patched_run_deps):
        with patch("src.core.monitor.monitor_one", new_callable=AsyncMock) as fake:
            fake.return_value = FakeMonitorResult(
                urls={"https://test.example.com/jobs/1"}, jobs_by_url=None, filtered_count=2
            )
            result = await run_monitor(_state())
        d = json.loads(json.dumps(result.to_dict()))
        assert d["board_url"] == "https://test.example.com/jobs"
        assert d["monitor_type"] == "sitemap"
        assert d["job_count"] == 1
        assert d["filtered_count"] == 2
        assert d["has_rich_data"] is False


# ── run_scraper ────────────────────────────────────────────────────────


class TestRunScraperHappyPath:
    @pytest.mark.asyncio
    async def test_happy_path(self, patched_run_deps):
        contents = [
            FakeJobContent(title="Eng", description="<p>x</p>", locations=["NYC"]),
            FakeJobContent(title="Des", description=None, locations=None),
        ]
        with (
            patch("src.core.scrape.scrape_one", new_callable=AsyncMock) as fake,
            patch("src.processing.scrape._apply_defaults", side_effect=lambda c, _cfg: c),
        ):
            fake.side_effect = contents
            result = await run_scraper(_state())

        assert isinstance(result, RunScraperResult)
        assert len(result.items) == 2
        assert result.items[0].url == "https://test.example.com/jobs/1"
        assert result.items[0].content.title == "Eng"
        assert result.skipped == []
        # avg elapsed should be a non-negative float
        assert result.avg_elapsed_seconds >= 0
        # Description samples (only the one with description)
        assert len(result.description_samples) == 1

    @pytest.mark.asyncio
    async def test_per_url_http_status_error_skipped(self, patched_run_deps):
        from httpx import HTTPStatusError

        # First URL 404s, second succeeds.
        good = FakeJobContent(title="OK", description="<p>x</p>")
        fake_response = MagicMock()
        fake_response.status_code = 404

        async def _scrape(url, *_, **__):
            if url.endswith("/1"):
                raise HTTPStatusError("404", request=MagicMock(), response=fake_response)
            return good

        with (
            patch("src.core.scrape.scrape_one", side_effect=_scrape),
            patch("src.processing.scrape._apply_defaults", side_effect=lambda c, _cfg: c),
        ):
            result = await run_scraper(_state())
        assert len(result.items) == 1
        assert result.items[0].url == "https://test.example.com/jobs/2"
        assert result.skipped == [("https://test.example.com/jobs/1", "404")]

    @pytest.mark.asyncio
    async def test_state_not_mutated(self, patched_run_deps):
        state = _state()
        before = state.to_dict()
        with (
            patch("src.core.scrape.scrape_one", new_callable=AsyncMock) as fake,
            patch("src.processing.scrape._apply_defaults", side_effect=lambda c, _cfg: c),
        ):
            fake.return_value = FakeJobContent(title="x", description="<p>y</p>")
            await run_scraper(state)
        assert state.to_dict() == before

    @pytest.mark.asyncio
    async def test_no_filesystem_writes(self, patched_run_deps, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = sorted(os.listdir("."))
        with (
            patch("src.core.scrape.scrape_one", new_callable=AsyncMock) as fake,
            patch("src.processing.scrape._apply_defaults", side_effect=lambda c, _cfg: c),
        ):
            fake.return_value = FakeJobContent(title="x", description=None)
            await run_scraper(_state())
        assert sorted(os.listdir(".")) == before
        assert not (tmp_path / ".workspace").exists()


class TestRunScraperErrorCases:
    @pytest.mark.asyncio
    async def test_missing_scraper_type(self, patched_run_deps):
        with pytest.raises(WsConfigMissing):
            await run_scraper(_state(scraper_type=None))

    @pytest.mark.asyncio
    async def test_no_sample_urls(self, patched_run_deps):
        with pytest.raises(WsConfigMissing):
            await run_scraper(_state(sample_urls=()))

    @pytest.mark.asyncio
    async def test_fatal_scraper_error_wraps(self, patched_run_deps):
        with (
            patch("src.core.scrape.scrape_one", new_callable=AsyncMock) as fake,
            patch("src.processing.scrape._apply_defaults", side_effect=lambda c, _cfg: c),
        ):
            fake.side_effect = RuntimeError("playwright died")
            with pytest.raises(WsScraperRunFailed):
                await run_scraper(_state())


class TestRunScraperOutputSchema:
    @pytest.mark.asyncio
    async def test_to_dict_round_trips(self, patched_run_deps):
        with (
            patch("src.core.scrape.scrape_one", new_callable=AsyncMock) as fake,
            patch("src.processing.scrape._apply_defaults", side_effect=lambda c, _cfg: c),
        ):
            fake.return_value = FakeJobContent(
                title="Eng", description="<p>x</p>", locations=["NYC"]
            )
            result = await run_scraper(_state(sample_urls=("https://test.example.com/jobs/1",)))
        d = json.loads(json.dumps(result.to_dict()))
        assert d["scraper_type"] == "json-ld"
        assert d["count"] == 1
        assert d["items"][0]["url"] == "https://test.example.com/jobs/1"
        assert d["items"][0]["content"]["title"] == "Eng"


# ── Determinism ────────────────────────────────────────────────────────


class TestDeterminism:
    @pytest.mark.asyncio
    async def test_run_monitor_same_input_same_output(self, patched_run_deps):
        url_set = {"https://test.example.com/jobs/1"}

        async def make():
            with patch("src.core.monitor.monitor_one", new_callable=AsyncMock) as fake:
                fake.return_value = FakeMonitorResult(urls=set(url_set), jobs_by_url=None)
                return await run_monitor(_state())

        r1 = await make()
        r2 = await make()
        # Strip elapsed_seconds (timing) for the equality assertion.
        d1 = r1.to_dict()
        d2 = r2.to_dict()
        d1.pop("elapsed_seconds", None)
        d2.pop("elapsed_seconds", None)
        assert d1 == d2
