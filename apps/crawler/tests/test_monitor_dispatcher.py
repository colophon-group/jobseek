from __future__ import annotations

import httpx
import pytest

from src.core.monitor import (
    MonitorResult,
    _apply_job_filter,
    _apply_url_allowlist,
    _apply_url_filter,
    _normalize_discovered,
    monitor_one,
)
from src.core.monitors import DiscoveredJob


class TestNormalizeDiscovered:
    def test_set_of_urls(self):
        urls = {"https://example.com/job1", "https://example.com/job2"}
        result = _normalize_discovered(urls)
        assert result.urls == urls
        assert result.jobs_by_url is None
        assert result.new_sitemap_url is None

    def test_tuple_from_sitemap(self):
        urls = {"https://example.com/job1"}
        sitemap_url = "https://example.com/sitemap.xml"
        result = _normalize_discovered((urls, sitemap_url))
        assert result.urls == urls
        assert result.new_sitemap_url == sitemap_url
        assert result.jobs_by_url is None

    def test_tuple_with_none_sitemap(self):
        urls = {"https://example.com/job1"}
        result = _normalize_discovered((urls, None))
        assert result.urls == urls
        assert result.new_sitemap_url is None

    def test_list_of_discovered_jobs(self):
        jobs = [
            DiscoveredJob(url="https://example.com/job1", title="Job 1"),
            DiscoveredJob(url="https://example.com/job2", title="Job 2"),
        ]
        result = _normalize_discovered(jobs)
        assert result.urls == {"https://example.com/job1", "https://example.com/job2"}
        assert result.jobs_by_url is not None
        assert result.jobs_by_url["https://example.com/job1"].title == "Job 1"
        assert result.jobs_by_url["https://example.com/job2"].title == "Job 2"

    def test_empty_list(self):
        result = _normalize_discovered([])
        assert result.urls == set()
        assert result.jobs_by_url == {}

    def test_empty_set(self):
        result = _normalize_discovered(set())
        assert result.urls == set()

    def test_monitor_result_passthrough(self):
        """Hybrid monitors can pre-build a MonitorResult with partial rich data
        and metadata_updates; _normalize_discovered must pass it through."""
        jobs = {
            "https://example.com/jobs/1": DiscoveredJob(
                url="https://example.com/jobs/1", title="Job 1"
            )
        }
        pre = MonitorResult(
            urls={"https://example.com/jobs/1", "https://example.com/jobs/2"},
            jobs_by_url=jobs,
            new_sitemap_url="https://example.com/sitemap.xml",
            metadata_updates={"pcsx_watermark": {"max_ts": 12345}},
            hybrid=True,
        )
        result = _normalize_discovered(pre)
        assert result is pre  # same instance, no copying
        assert result.urls == pre.urls
        assert result.jobs_by_url is pre.jobs_by_url
        assert result.new_sitemap_url == "https://example.com/sitemap.xml"
        assert result.metadata_updates == {"pcsx_watermark": {"max_ts": 12345}}
        assert result.hybrid is True


class TestApplyUrlFilter:
    def _make_result(self, urls, jobs_by_url=None, new_sitemap_url=None):
        return MonitorResult(
            urls=set(urls),
            jobs_by_url=jobs_by_url,
            new_sitemap_url=new_sitemap_url,
        )

    def test_no_filter(self):
        result = self._make_result(["https://example.com/jobs/1", "https://example.com/blog/2"])
        filtered = _apply_url_filter(result, {})
        assert filtered.urls == result.urls
        assert filtered.filtered_count == 0

    def test_include_string(self):
        result = self._make_result(
            [
                "https://example.com/jobs/1",
                "https://example.com/jobs/2",
                "https://example.com/blog/hello",
            ]
        )
        filtered = _apply_url_filter(result, {"url_filter": "/jobs/"})
        assert filtered.urls == {"https://example.com/jobs/1", "https://example.com/jobs/2"}
        assert filtered.filtered_count == 1

    def test_include_exclude_dict(self):
        result = self._make_result(
            [
                "https://example.com/jobs/1",
                "https://example.com/jobs/intern",
                "https://example.com/blog/post",
            ]
        )
        filtered = _apply_url_filter(
            result,
            {"url_filter": {"include": "/jobs/", "exclude": "/intern"}},
        )
        assert filtered.urls == {"https://example.com/jobs/1"}
        assert filtered.filtered_count == 2

    def test_filters_jobs_by_url(self):
        jobs = {
            "https://example.com/jobs/1": DiscoveredJob(
                url="https://example.com/jobs/1",
                title="Job 1",
            ),
            "https://example.com/blog/2": DiscoveredJob(
                url="https://example.com/blog/2",
                title="Blog",
            ),
        }
        result = self._make_result(jobs.keys(), jobs_by_url=jobs)
        filtered = _apply_url_filter(result, {"url_filter": "/jobs/"})
        assert filtered.urls == {"https://example.com/jobs/1"}
        assert filtered.jobs_by_url is not None
        assert len(filtered.jobs_by_url) == 1
        assert "https://example.com/jobs/1" in filtered.jobs_by_url

    def test_invalid_regex(self):
        result = self._make_result(["https://example.com/jobs/1"])
        filtered = _apply_url_filter(result, {"url_filter": "[invalid"})
        assert filtered.urls == result.urls
        assert filtered.filtered_count == 0

    def test_preserves_new_sitemap_url(self):
        result = self._make_result(
            ["https://example.com/jobs/1", "https://example.com/blog/2"],
            new_sitemap_url="https://example.com/sitemap.xml",
        )
        filtered = _apply_url_filter(result, {"url_filter": "/jobs/"})
        assert filtered.new_sitemap_url == "https://example.com/sitemap.xml"
        assert filtered.filtered_count == 1

    def test_preserves_metadata_updates_and_hybrid_flag(self):
        """Hybrid monitors set metadata_updates/hybrid on MonitorResult; these
        must survive url_filter and url_transform passes."""
        result = MonitorResult(
            urls={"https://example.com/jobs/1", "https://example.com/blog/2"},
            metadata_updates={"pcsx_watermark": {"max_ts": 999}},
            hybrid=True,
        )
        filtered = _apply_url_filter(result, {"url_filter": "/jobs/"})
        assert filtered.metadata_updates == {"pcsx_watermark": {"max_ts": 999}}
        assert filtered.hybrid is True


class TestApplyJobFilter:
    def _make_result(self):
        jobs = {
            "https://example.com/jobs/1": DiscoveredJob(
                url="https://example.com/jobs/1",
                title="Engineer",
                description="<p>Build products for Example Corp.</p>",
                locations=["London"],
                metadata={"department": "Technology"},
            ),
            "https://example.com/jobs/2": DiscoveredJob(
                url="https://example.com/jobs/2",
                title="Pilot",
                description="<p>Join Executive Jet Management.</p>",
                locations=["Lisbon"],
                metadata={"department": "Flight Operations"},
            ),
        }
        return MonitorResult(urls=set(jobs), jobs_by_url=jobs)

    def test_excludes_matching_rich_job_content(self):
        filtered = _apply_job_filter(
            self._make_result(),
            {"job_filter": {"exclude": "(?i)executive jet management"}},
        )

        assert filtered.urls == {"https://example.com/jobs/1"}
        assert filtered.jobs_by_url is not None
        assert set(filtered.jobs_by_url) == filtered.urls
        assert filtered.filtered_count == 1

    def test_include_searches_locations_and_metadata(self):
        by_location = _apply_job_filter(self._make_result(), {"job_filter": "London"})
        by_metadata = _apply_job_filter(self._make_result(), {"job_filter": "Flight Operations"})

        assert by_location.urls == {"https://example.com/jobs/1"}
        assert by_metadata.urls == {"https://example.com/jobs/2"}

    def test_invalid_regex_leaves_result_unchanged(self):
        result = self._make_result()
        filtered = _apply_job_filter(result, {"job_filter": {"exclude": "[invalid"}})

        assert filtered is result

    def test_url_only_result_is_left_unchanged(self):
        result = MonitorResult(urls={"https://example.com/jobs/1"})
        filtered = _apply_job_filter(result, {"job_filter": {"exclude": "Example"}})

        assert filtered is result

    def test_accumulates_prior_filter_count_and_preserves_flags(self):
        result = self._make_result()
        result.filtered_count = 3
        result.metadata_updates = {"cursor": 123}
        result.hybrid = True
        result.truncated = True

        filtered = _apply_job_filter(
            result, {"job_filter": {"exclude": "Executive Jet Management"}}
        )

        assert filtered.filtered_count == 4
        assert filtered.metadata_updates == {"cursor": 123}
        assert filtered.hybrid is True
        assert filtered.truncated is True

    def test_preserves_url_only_part_of_hybrid_result(self):
        result = self._make_result()
        result.urls.add("https://example.com/jobs/url-only")
        result.hybrid = True

        filtered = _apply_job_filter(
            result, {"job_filter": {"exclude": "Executive Jet Management"}}
        )

        assert filtered.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/url-only",
        }


class TestApplyUrlAllowlist:
    def test_fullmatch_rejects_foreign_and_partial_matches_and_preserves_state(self):
        allowed = "https://careers.example.com/job/123"
        foreign = "https://evil.example/job/123"
        partial = f"{allowed}/unexpected"
        jobs = {url: DiscoveredJob(url=url, title=url) for url in (allowed, foreign, partial)}
        result = MonitorResult(
            urls=set(jobs),
            jobs_by_url=jobs,
            new_sitemap_url="https://careers.example.com/sitemap.xml",
            filtered_count=7,
            security_filtered_count=2,
            metadata_updates={"cursor": 123},
            hybrid=True,
            truncated=True,
        )

        filtered = _apply_url_allowlist(
            result,
            {"url_allowlist": r"https://careers[.]example[.]com/job/[0-9]+"},
        )

        assert filtered.urls == {allowed}
        assert filtered.jobs_by_url is not None
        assert set(filtered.jobs_by_url) == {allowed}
        assert filtered.filtered_count == 7
        assert filtered.security_filtered_count == 4
        assert filtered.new_sitemap_url == result.new_sitemap_url
        assert filtered.metadata_updates == {"cursor": 123}
        assert filtered.hybrid is True
        assert filtered.truncated is True

    @pytest.mark.parametrize("invalid", ["", None, 42, "("])
    def test_configured_invalid_allowlist_fails_closed(self, invalid):
        result = MonitorResult(
            urls={"https://careers.example.com/job/123"},
            jobs_by_url={
                "https://careers.example.com/job/123": DiscoveredJob(
                    url="https://careers.example.com/job/123",
                    title="Engineer",
                )
            },
        )

        filtered = _apply_url_allowlist(result, {"url_allowlist": invalid})

        assert filtered.urls == set()
        assert filtered.jobs_by_url == {}
        assert filtered.filtered_count == 0
        assert filtered.security_filtered_count == 1


class TestMonitorOne:
    async def test_greenhouse_integration(self):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "absolute_url": "https://boards.greenhouse.io/test/jobs/1",
                            "title": "Engineer",
                            "content": "<p>Description</p>",
                            "location": {"name": "NYC"},
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await monitor_one(
                "https://boards.greenhouse.io/testco",
                "greenhouse",
                {"token": "testco"},
                client,
            )
            assert len(result.urls) == 1
            assert result.jobs_by_url is not None
            job = next(iter(result.jobs_by_url.values()))
            assert job.title == "Engineer"

    async def test_unknown_monitor_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="Unknown monitor type"):
                await monitor_one("https://example.com", "nonexistent", {}, client)

    async def test_skip_ssl_routes_through_nossl_client(self, monkeypatch):
        # When monitor_config sets skip_ssl: true (DiDi Intl: missing
        # intermediate CA), the discoverer must receive a verify=False
        # client, not the default verifying client passed in. Swap
        # happens via create_nossl_http_client(); intercept that to
        # confirm it's called and its client is the one threaded through.
        import src.shared.http as http_mod
        from src.core.monitors import _REGISTRY, MonitorType, _make_chunked_stream

        captured: dict[str, object] = {}
        nossl_clients: list[httpx.AsyncClient] = []

        async def stub_discover(board, http, *, pw=None):
            captured["http"] = http
            return set()

        real_factory = http_mod.create_nossl_http_client

        def tracking_factory(*, use_proxy: bool = False) -> httpx.AsyncClient:
            client = real_factory(use_proxy=use_proxy)
            nossl_clients.append(client)
            return client

        # monitor_one imports create_nossl_http_client lazily inside the
        # function body, so the patch must target the source module.
        monkeypatch.setattr(http_mod, "create_nossl_http_client", tracking_factory)

        probe = MonitorType(
            name="__skip_ssl_probe__",
            cost=1,
            discover=stub_discover,
            can_handle=None,
            rich=False,
            stream=_make_chunked_stream(stub_discover),
        )
        _REGISTRY.append(probe)
        try:
            outer = httpx.AsyncClient()
            try:
                await monitor_one(
                    "https://example.com",
                    "__skip_ssl_probe__",
                    {"skip_ssl": True},
                    outer,
                )
            finally:
                await outer.aclose()
        finally:
            _REGISTRY.remove(probe)

        assert len(nossl_clients) == 1, "skip_ssl monitor must build a nossl client"
        assert captured["http"] is nossl_clients[0], (
            "discoverer must receive the nossl client, not the verifying default"
        )

    async def test_skip_ssl_absent_uses_passed_client(self):
        # No skip_ssl flag → monitor_one must not allocate a new client.
        from src.core.monitors import _REGISTRY, MonitorType, _make_chunked_stream

        captured: dict[str, object] = {}

        async def stub_discover(board, http, *, pw=None):
            captured["http"] = http
            return set()

        probe = MonitorType(
            name="__no_skip_ssl_probe__",
            cost=1,
            discover=stub_discover,
            can_handle=None,
            rich=False,
            stream=_make_chunked_stream(stub_discover),
        )
        _REGISTRY.append(probe)
        try:
            outer = httpx.AsyncClient()
            try:
                await monitor_one(
                    "https://example.com",
                    "__no_skip_ssl_probe__",
                    {},
                    outer,
                )
            finally:
                await outer.aclose()
        finally:
            _REGISTRY.remove(probe)
        assert captured["http"] is outer

    @pytest.mark.parametrize(
        "config,expected_use_proxy",
        [
            ({"skip_ssl": True, "proxy": True}, True),
            ({"skip_ssl": True, "proxy": False}, False),
            ({"skip_ssl": True}, False),
        ],
        ids=["proxy_on", "proxy_off", "proxy_unset"],
    )
    async def test_skip_ssl_threads_use_proxy(
        self, monkeypatch, config: dict, expected_use_proxy: bool
    ):
        """A board with `skip_ssl: true` AND `proxy: true` must route the
        nossl httpx client through the proxy too — otherwise the monitor
        silently downgrades to direct egress for the API request, defeating
        the WAF/IP-block rationale for setting `proxy: true` (regression
        guard for #2659)."""
        import src.shared.http as http_mod
        from src.core.monitors import _REGISTRY, MonitorType, _make_chunked_stream

        observed_use_proxy: list[bool] = []
        real_factory = http_mod.create_nossl_http_client

        def tracking_factory(*, use_proxy: bool = False) -> httpx.AsyncClient:
            observed_use_proxy.append(use_proxy)
            return real_factory(use_proxy=use_proxy)

        monkeypatch.setattr(http_mod, "create_nossl_http_client", tracking_factory)

        async def stub_discover(board, http, *, pw=None):
            return set()

        probe = MonitorType(
            name="__skip_ssl_proxy_probe__",
            cost=1,
            discover=stub_discover,
            can_handle=None,
            rich=False,
            stream=_make_chunked_stream(stub_discover),
        )
        _REGISTRY.append(probe)
        try:
            outer = httpx.AsyncClient()
            try:
                await monitor_one(
                    "https://example.com",
                    "__skip_ssl_proxy_probe__",
                    config,
                    outer,
                )
            finally:
                await outer.aclose()
        finally:
            _REGISTRY.remove(probe)

        assert observed_use_proxy == [expected_use_proxy]
