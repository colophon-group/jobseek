from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from src.core.monitors import (
    MonitorType,
    _build_comment,
    probe_all_monitors,
)


@pytest.fixture()
def _patch_registry(monkeypatch):
    """Replace the monitor registry with controllable fakes."""

    async def _gh_can_handle(url, client, pw=None):
        return {"token": "stripe", "jobs": 138}

    async def _lever_can_handle(url, client, pw=None):
        return {"token": "acme", "jobs": 42}

    async def _nextdata_can_handle(url, client, pw=None):
        return {"path": "props.pageProps.positions", "count": 629}

    async def _sitemap_can_handle(url, client, pw=None):
        return {"sitemap_url": "https://example.com/sitemap.xml", "urls": 322}

    async def _dom_can_handle(url, client, pw=None):
        return {"urls": 15}

    fake_registry = [
        MonitorType(name="greenhouse", cost=10, discover=AsyncMock(), can_handle=_gh_can_handle),
        MonitorType(name="lever", cost=10, discover=AsyncMock(), can_handle=_lever_can_handle),
        MonitorType(name="nextdata", cost=20, discover=AsyncMock(), can_handle=_nextdata_can_handle),
        MonitorType(name="sitemap", cost=50, discover=AsyncMock(), can_handle=_sitemap_can_handle),
        MonitorType(name="dom", cost=100, discover=AsyncMock(), can_handle=_dom_can_handle),
    ]
    monkeypatch.setattr("src.core.monitors._REGISTRY", fake_registry)
    return fake_registry


class TestBuildComment:
    def test_greenhouse_with_jobs(self):
        comment = _build_comment("greenhouse", {"token": "stripe", "jobs": 138})
        assert "Greenhouse API" in comment
        assert "stripe" in comment
        assert "138" in comment

    def test_greenhouse_without_jobs(self):
        comment = _build_comment("greenhouse", {"token": "stripe"})
        assert "Greenhouse API" in comment
        assert "stripe" in comment

    def test_lever_with_jobs(self):
        comment = _build_comment("lever", {"token": "acme", "jobs": 42})
        assert "Lever API" in comment
        assert "acme" in comment
        assert "42" in comment

    def test_lever_100_plus(self):
        comment = _build_comment("lever", {"token": "acme", "jobs": "100+"})
        assert "100+" in comment

    def test_nextdata_with_count(self):
        comment = _build_comment("nextdata", {"path": "props.pageProps.positions", "count": 629})
        assert "__NEXT_DATA__" in comment
        assert "629" in comment
        assert "props.pageProps.positions" in comment
        assert "(render)" not in comment

    def test_nextdata_with_render(self):
        comment = _build_comment("nextdata", {"path": "props.pageProps.positions", "count": 42, "render": True})
        assert "__NEXT_DATA__" in comment
        assert "42" in comment
        assert "(render)" in comment

    def test_sitemap_with_urls(self):
        comment = _build_comment("sitemap", {"sitemap_url": "https://example.com/sitemap.xml", "urls": 322})
        assert "Sitemap" in comment
        assert "322" in comment
        assert "https://example.com/sitemap.xml" in comment

    def test_dom_with_urls(self):
        comment = _build_comment("dom", {"urls": 15})
        assert "DOM" in comment
        assert "15" in comment


class TestProbeAllMonitors:
    @pytest.mark.usefixtures("_patch_registry")
    async def test_all_monitors_probed(self):
        client = AsyncMock()
        results = await probe_all_monitors("https://example.com/careers", client)
        assert len(results) == 5
        names = [r[0] for r in results]
        assert "greenhouse" in names
        assert "lever" in names
        assert "nextdata" in names
        assert "sitemap" in names
        assert "dom" in names

    @pytest.mark.usefixtures("_patch_registry")
    async def test_greenhouse_metadata(self):
        client = AsyncMock()
        results = await probe_all_monitors("https://example.com/careers", client)
        gh = next(r for r in results if r[0] == "greenhouse")
        assert gh[1] == {"token": "stripe", "jobs": 138}
        assert "Greenhouse API" in gh[2]
        assert "138" in gh[2]

    @pytest.mark.usefixtures("_patch_registry")
    async def test_lever_metadata(self):
        client = AsyncMock()
        results = await probe_all_monitors("https://example.com/careers", client)
        lever = next(r for r in results if r[0] == "lever")
        assert lever[1] == {"token": "acme", "jobs": 42}
        assert "Lever API" in lever[2]

    @pytest.mark.usefixtures("_patch_registry")
    async def test_nextdata_metadata(self):
        client = AsyncMock()
        results = await probe_all_monitors("https://example.com/careers", client)
        nd = next(r for r in results if r[0] == "nextdata")
        assert nd[1]["path"] == "props.pageProps.positions"
        assert nd[1]["count"] == 629
        assert "629" in nd[2]

    @pytest.mark.usefixtures("_patch_registry")
    async def test_sitemap_metadata(self):
        client = AsyncMock()
        results = await probe_all_monitors("https://example.com/careers", client)
        sm = next(r for r in results if r[0] == "sitemap")
        assert sm[1]["urls"] == 322
        assert "322" in sm[2]

    @pytest.mark.usefixtures("_patch_registry")
    async def test_dom_metadata(self):
        client = AsyncMock()
        results = await probe_all_monitors("https://example.com/careers", client)
        dom = next(r for r in results if r[0] == "dom")
        assert dom[1] == {"urls": 15}
        assert "DOM" in dom[2]
        assert "15" in dom[2]

    async def test_nothing_detected(self, monkeypatch):
        async def _fail(url, client, pw=None):
            return None

        fake_registry = [
            MonitorType(name="greenhouse", cost=10, discover=AsyncMock(), can_handle=_fail),
            MonitorType(name="lever", cost=10, discover=AsyncMock(), can_handle=_fail),
            MonitorType(name="dom", cost=100, discover=AsyncMock(), can_handle=_fail),
        ]
        monkeypatch.setattr("src.core.monitors._REGISTRY", fake_registry)

        client = AsyncMock()
        results = await probe_all_monitors("https://example.com/careers", client)
        gh = next(r for r in results if r[0] == "greenhouse")
        assert gh[1] is None
        assert "Not detected" in gh[2]
        dom = next(r for r in results if r[0] == "dom")
        assert "Not detected" in dom[2]

    async def test_timeout_handled(self, monkeypatch):
        async def _slow(url, client, pw=None):
            await asyncio.sleep(10)
            return {"token": "slow"}

        fake_registry = [
            MonitorType(name="greenhouse", cost=10, discover=AsyncMock(), can_handle=_slow),
        ]
        monkeypatch.setattr("src.core.monitors._REGISTRY", fake_registry)

        client = AsyncMock()
        results = await probe_all_monitors("https://example.com/careers", client, timeout=0.1)
        assert len(results) == 1
        assert results[0][1] is None
        assert "Timeout" in results[0][2]

    async def test_error_handled(self, monkeypatch):
        async def _boom(url, client, pw=None):
            raise RuntimeError("connection refused")

        fake_registry = [
            MonitorType(name="greenhouse", cost=10, discover=AsyncMock(), can_handle=_boom),
        ]
        monkeypatch.setattr("src.core.monitors._REGISTRY", fake_registry)

        client = AsyncMock()
        results = await probe_all_monitors("https://example.com/careers", client)
        assert len(results) == 1
        assert results[0][1] is None
        assert "Error:" in results[0][2]
        assert "connection refused" in results[0][2]
