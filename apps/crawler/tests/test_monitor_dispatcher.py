from __future__ import annotations

import httpx
import pytest

from src.core.monitor import MonitorResult, _apply_url_filter, _normalize_discovered, monitor_one
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
        result = self._make_result([
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/blog/hello",
        ])
        filtered = _apply_url_filter(result, {"url_filter": "/jobs/"})
        assert filtered.urls == {"https://example.com/jobs/1", "https://example.com/jobs/2"}
        assert filtered.filtered_count == 1

    def test_include_exclude_dict(self):
        result = self._make_result([
            "https://example.com/jobs/1",
            "https://example.com/jobs/intern",
            "https://example.com/blog/post",
        ])
        filtered = _apply_url_filter(result, {"url_filter": {"include": "/jobs/", "exclude": "/intern"}})
        assert filtered.urls == {"https://example.com/jobs/1"}
        assert filtered.filtered_count == 2

    def test_filters_jobs_by_url(self):
        jobs = {
            "https://example.com/jobs/1": DiscoveredJob(url="https://example.com/jobs/1", title="Job 1"),
            "https://example.com/blog/2": DiscoveredJob(url="https://example.com/blog/2", title="Blog"),
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
