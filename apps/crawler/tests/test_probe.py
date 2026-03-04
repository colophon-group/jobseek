from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.monitors import (
    MonitorType,
    _build_comment,
    probe_all_monitors,
)
from src.core.scrapers import _PROBE_ORDER, probe_scrapers
from src.core.scrapers.dom import _heuristic_steps
from src.core.scrapers.dom import can_handle as dom_can_handle
from src.core.scrapers.jsonld import can_handle as jsonld_can_handle
from src.core.scrapers.nextdata import (
    _auto_map_fields,
    _find_job_object,
)
from src.core.scrapers.nextdata import (
    can_handle as nextdata_can_handle,
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

    mk = AsyncMock()
    fake_registry = [
        MonitorType(name="greenhouse", cost=10, discover=mk(), can_handle=_gh_can_handle),
        MonitorType(name="lever", cost=10, discover=mk(), can_handle=_lever_can_handle),
        MonitorType(
            name="nextdata",
            cost=20,
            discover=mk(),
            can_handle=_nextdata_can_handle,
        ),
        MonitorType(
            name="sitemap",
            cost=50,
            discover=mk(),
            can_handle=_sitemap_can_handle,
        ),
        MonitorType(name="dom", cost=100, discover=mk(), can_handle=_dom_can_handle),
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
        meta = {"path": "props.pageProps.positions", "count": 42, "render": True}
        comment = _build_comment("nextdata", meta)
        assert "__NEXT_DATA__" in comment
        assert "42" in comment
        assert "(render)" in comment

    def test_sitemap_with_urls(self):
        meta = {"sitemap_url": "https://example.com/sitemap.xml", "urls": 322}
        comment = _build_comment("sitemap", meta)
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


# ── Scraper probe tests ──────────────────────────────────────────────

_JSONLD_HTML = """\
<html><head><script type="application/ld+json">
{"@type": "JobPosting", "title": "Engineer", "description": "<p>Build stuff</p>",
 "jobLocation": {"@type": "Place", "name": "NYC"}}
</script></head><body><h1>Engineer</h1></body></html>"""

_JSONLD_HTML_2 = """\
<html><head><script type="application/ld+json">
{"@type": "JobPosting", "title": "Designer", "description": "<p>Design stuff</p>",
 "jobLocation": {"@type": "Place", "name": "SF"}}
</script></head><body><h1>Designer</h1></body></html>"""

_NO_JSONLD_HTML = "<html><body><h1>About Us</h1><p>We are a company.</p></body></html>"

_NEXTDATA_HTML = """\
<html><head></head><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"title":"Engineer","description":"<p>Build things</p>",
"location":"New York","employmentType":"Full-time"}}}
</script></body></html>"""

_NEXTDATA_HTML_2 = """\
<html><head></head><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"title":"Designer","description":"<p>Design things</p>",
"location":"San Francisco","employmentType":"Part-time"}}}
</script></body></html>"""

_NEXTDATA_HTML_NESTED = """\
<html><head></head><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"job":{"title":"Manager","body":"<p>Lead things</p>",
"offices":[{"name":"London"},{"name":"Berlin"}]}}}}
</script></body></html>"""

_NEXTDATA_NO_JOB = """\
<html><head></head><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"user":{"name":"test"}}}}
</script></body></html>"""

_DOM_HTML = """\
<html><body>
<h1>Software Engineer</h1>
<p>Location: San Francisco</p>
<p>We are looking for a talented engineer to join our team.</p>
<p>You will build amazing products.</p>
<h2>Requirements</h2>
<ul><li>5 years experience</li></ul>
<button>Apply Now</button>
</body></html>"""

_DOM_HTML_2 = """\
<html><body>
<h1>Product Manager</h1>
<p>Location: New York</p>
<p>We need a product manager to lead our team.</p>
<h2>Qualifications</h2>
<ul><li>3 years experience</li></ul>
</body></html>"""

_NO_H1_HTML = "<html><body><p>Just some text</p></body></html>"


class TestScraperCanHandle:
    def test_jsonld_detected(self):
        result = jsonld_can_handle([_JSONLD_HTML])
        assert result == {}

    def test_jsonld_detected_multiple_pages(self):
        result = jsonld_can_handle([_JSONLD_HTML, _JSONLD_HTML_2])
        assert result == {}

    def test_jsonld_not_detected(self):
        result = jsonld_can_handle([_NO_JSONLD_HTML])
        assert result is None

    def test_jsonld_majority_required(self):
        # 1 of 3 pages has JSON-LD — should not detect
        result = jsonld_can_handle([_JSONLD_HTML, _NO_JSONLD_HTML, _NO_H1_HTML])
        assert result is None

    def test_nextdata_detected(self):
        result = nextdata_can_handle([_NEXTDATA_HTML])
        assert result is not None
        assert "fields" in result
        assert "path" in result
        assert result["fields"]["title"] == "title"
        assert result["fields"]["description"] == "description"

    def test_nextdata_detected_multiple_pages(self):
        result = nextdata_can_handle([_NEXTDATA_HTML, _NEXTDATA_HTML_2])
        assert result is not None
        assert result["fields"]["title"] == "title"
        assert result["fields"]["description"] == "description"
        # employment_type should be found across both pages
        assert "employment_type" in result["fields"]

    def test_nextdata_nested(self):
        result = nextdata_can_handle([_NEXTDATA_HTML_NESTED])
        assert result is not None
        assert "job" in result["path"]
        assert result["fields"]["title"] == "title"

    def test_nextdata_no_job(self):
        result = nextdata_can_handle([_NEXTDATA_NO_JOB])
        assert result is None

    def test_nextdata_no_nextdata(self):
        result = nextdata_can_handle([_NO_JSONLD_HTML])
        assert result is None

    def test_dom_detected(self):
        result = dom_can_handle([_DOM_HTML])
        assert result is not None
        assert "steps" in result
        steps = result["steps"]
        # Should have at least title step
        title_step = next(s for s in steps if s.get("field") == "title")
        assert title_step["tag"] == "h1"

    def test_dom_detected_multiple_pages(self):
        result = dom_can_handle([_DOM_HTML, _DOM_HTML_2])
        assert result is not None
        assert "steps" in result

    def test_dom_no_h1(self):
        result = dom_can_handle([_NO_H1_HTML])
        assert result is None


class TestNextdataAutoMap:
    def test_simple_keys(self):
        obj = {"title": "Engineer", "description": "<p>Hello</p>"}
        fields = _auto_map_fields(obj)
        assert fields["title"] == "title"
        assert fields["description"] == "description"

    def test_array_of_dicts(self):
        obj = {
            "title": "Engineer",
            "description": "Hello",
            "locations": [{"name": "NYC"}, {"name": "SF"}],
        }
        fields = _auto_map_fields(obj)
        assert fields["locations"] == "locations[].name"

    def test_nested_keys(self):
        obj = {"name": "Engineer", "body": "Hello", "employmentType": "Full-time"}
        fields = _auto_map_fields(obj)
        assert fields["title"] == "name"
        assert fields["description"] == "body"
        assert fields["employment_type"] == "employmentType"

    def test_date_posted(self):
        obj = {"title": "Engineer", "description": "Hi", "datePosted": "2025-01-01"}
        fields = _auto_map_fields(obj)
        assert fields["date_posted"] == "datePosted"

    def test_find_job_object_at_root(self):
        data = {"title": "Engineer", "description": "Hello"}
        suffix, obj = _find_job_object(data, "props.pageProps")
        assert suffix is None
        assert obj is data

    def test_find_job_object_nested(self):
        data = {"job": {"title": "Engineer", "description": "Hello"}, "other": "stuff"}
        suffix, obj = _find_job_object(data, "props.pageProps")
        assert suffix == "job"
        assert obj == {"title": "Engineer", "description": "Hello"}

    def test_find_job_object_not_found(self):
        data = {"user": {"name": "test"}}
        suffix, obj = _find_job_object(data, "props.pageProps")
        assert suffix is None
        assert obj is None


class TestDomHeuristicSteps:
    def test_h1_with_content(self):
        from src.shared.extract import flatten

        elements = flatten(_DOM_HTML)
        steps = _heuristic_steps(elements)
        assert steps is not None
        assert len(steps) >= 2
        # Title step
        assert steps[0] == {"tag": "h1", "field": "title"}
        # Description step
        desc_step = steps[1]
        assert desc_step["field"] == "description"
        assert desc_step["html"] is True

    def test_h1_with_stop_marker(self):
        from src.shared.extract import flatten

        elements = flatten(_DOM_HTML)
        steps = _heuristic_steps(elements)
        desc_step = steps[1]
        assert "stop" in desc_step or "stop_count" in desc_step

    def test_location_detected(self):
        from src.shared.extract import flatten

        elements = flatten(_DOM_HTML)
        steps = _heuristic_steps(elements)
        location_steps = [s for s in steps if s.get("field") == "location"]
        assert len(location_steps) == 1
        assert location_steps[0]["optional"] is True

    def test_no_h1_returns_none(self):
        from src.shared.extract import flatten

        elements = flatten(_NO_H1_HTML)
        steps = _heuristic_steps(elements)
        assert steps is None


def _mock_http_client(responses: dict[str, tuple[int, str]]) -> AsyncMock:
    """Create a mock HTTP client that returns preset responses by URL."""
    client = AsyncMock()

    async def _get(url, **kwargs):
        if url in responses:
            status_code, text = responses[url]
            resp = AsyncMock()
            resp.status_code = status_code
            resp.text = text
            return resp
        resp = AsyncMock()
        resp.status_code = 404
        resp.text = ""
        return resp

    client.get = _get
    return client


_SPA_HTML = "<html><body><div id='app'></div></body></html>"


class TestProbeScrapers:
    async def test_all_scrapers_probed(self):
        """Mock HTTP to return test HTML, verify all scrapers probed."""
        http = _mock_http_client(
            {
                "https://example.com/job/1": (200, _JSONLD_HTML),
                "https://example.com/job/2": (200, _JSONLD_HTML),
            }
        )

        results, _spa_suspect = await probe_scrapers(
            ["https://example.com/job/1", "https://example.com/job/2"],
            http,
        )

        names = [r[0] for r in results]
        assert "json-ld" in names
        assert "nextdata" in names
        assert "dom" in names
        assert "api_sniffer" in names

    async def test_jsonld_detected_with_quality(self):
        """json-ld detected → metadata has quality stats."""
        http = _mock_http_client(
            {
                "https://example.com/job/1": (200, _JSONLD_HTML),
                "https://example.com/job/2": (200, _JSONLD_HTML_2),
            }
        )

        results, _ = await probe_scrapers(
            ["https://example.com/job/1", "https://example.com/job/2"],
            http,
        )

        jsonld = next(r for r in results if r[0] == "json-ld")
        assert jsonld[1] is not None
        assert jsonld[1]["titles"] == 2
        assert jsonld[1]["descriptions"] == 2
        assert jsonld[1]["locations"] == 2
        assert jsonld[1]["total"] == 2
        assert "2/2 titles" in jsonld[2]

    async def test_nextdata_detected_with_config(self):
        """nextdata detected → metadata has config + quality."""
        http = _mock_http_client(
            {
                "https://example.com/job/1": (200, _NEXTDATA_HTML),
                "https://example.com/job/2": (200, _NEXTDATA_HTML_2),
            }
        )

        results, _ = await probe_scrapers(
            ["https://example.com/job/1", "https://example.com/job/2"],
            http,
        )

        nd = next(r for r in results if r[0] == "nextdata")
        assert nd[1] is not None
        assert "config" in nd[1]
        assert nd[1]["titles"] == 2
        assert nd[1]["descriptions"] == 2

    async def test_fetch_failure_handled(self):
        """Fetch failures don't crash the probe."""
        http = _mock_http_client(
            {
                "https://example.com/job/1": (500, ""),
            }
        )

        results, spa_suspect = await probe_scrapers(
            ["https://example.com/job/1"],
            http,
        )

        # All scrapers should report failure (static ones "Fetch failed",
        # Playwright-based ones "Skipped" since pw=None)
        for _name, meta, comment in results:
            assert meta is None
            assert "Fetch failed" in comment or "Skipped" in comment
        assert spa_suspect is False

    async def test_probe_order(self):
        """Results should be in display order: json-ld, nextdata, embedded, dom, api_sniffer."""
        http = _mock_http_client(
            {
                "https://example.com/job/1": (200, _JSONLD_HTML),
            }
        )

        results, _ = await probe_scrapers(
            ["https://example.com/job/1"],
            http,
        )

        names = [r[0] for r in results]
        assert names == ["json-ld", "nextdata", "embedded", "dom", "api_sniffer"]

    async def test_spa_detection(self):
        """Pages with very little text content should set spa_suspect=True."""
        http = _mock_http_client(
            {
                "https://example.com/job/1": (200, _SPA_HTML),
            }
        )

        _results, spa_suspect = await probe_scrapers(
            ["https://example.com/job/1"],
            http,
        )

        assert spa_suspect is True

    async def test_spa_not_triggered_for_normal_pages(self):
        """Pages with substantial text (> 200 chars) should not trigger SPA warning."""
        rich_html = (
            "<html><body><h1>Software Engineer</h1>"
            "<p>Location: San Francisco, California, United States</p>"
            "<p>We are looking for a talented software engineer to join our "
            "growing team. You will work on cutting-edge technology and help "
            "build products that millions of people use every day.</p>"
            "<h2>Requirements</h2>"
            "<ul><li>5+ years of professional experience</li>"
            "<li>Strong knowledge of Python and JavaScript</li></ul>"
            "</body></html>"
        )
        http = _mock_http_client(
            {
                "https://example.com/job/1": (200, rich_html),
                "https://example.com/job/2": (200, rich_html),
            }
        )

        _results, spa_suspect = await probe_scrapers(
            ["https://example.com/job/1", "https://example.com/job/2"],
            http,
        )

        assert spa_suspect is False


class TestProbeScrapersPw:
    def test_api_sniffer_in_probe_order(self):
        """api_sniffer should appear in the probe order list."""
        assert "api_sniffer" in _PROBE_ORDER

    async def test_api_sniffer_skipped_without_pw(self):
        """When pw=None, api_sniffer should show 'Skipped' message."""
        http = _mock_http_client(
            {
                "https://example.com/job/1": (200, _JSONLD_HTML),
            }
        )

        results, _ = await probe_scrapers(
            ["https://example.com/job/1"],
            http,
            pw=None,
        )

        api_sniffer = next(r for r in results if r[0] == "api_sniffer")
        assert api_sniffer[1] is None
        assert "Skipped" in api_sniffer[2]
        assert "Playwright" in api_sniffer[2]

    async def test_api_sniffer_detected_with_pw(self):
        """When pw is provided and probe_pw detects data, shows quality stats."""
        http = _mock_http_client(
            {
                "https://example.com/job/1": (200, _JSONLD_HTML),
            }
        )

        # Mock the api_sniffer's probe_pw to return detected metadata
        fake_metadata = {
            "config": {"fields": {"title": "title"}},
            "total": 1,
            "titles": 1,
            "descriptions": 1,
            "locations": 0,
            "fields": {"title": 1, "description": 1},
        }

        async def fake_probe_pw(urls, pw):
            return fake_metadata, "1/1 titles, 1/1 desc, 0/1 locations"

        pw = MagicMock()

        with patch.object(
            __import__("src.core.scrapers", fromlist=["_REGISTRY"])._REGISTRY["api_sniffer"],
            "probe_pw",
            fake_probe_pw,
        ):
            results, _ = await probe_scrapers(
                ["https://example.com/job/1"],
                http,
                pw=pw,
            )

        api_sniffer = next(r for r in results if r[0] == "api_sniffer")
        assert api_sniffer[1] is not None
        assert api_sniffer[1]["titles"] == 1
        assert "titles" in api_sniffer[2]

    async def test_probe_order_includes_api_sniffer(self):
        """Results should include api_sniffer in the probe order."""
        http = _mock_http_client(
            {
                "https://example.com/job/1": (200, _JSONLD_HTML),
            }
        )

        results, _ = await probe_scrapers(
            ["https://example.com/job/1"],
            http,
        )

        names = [r[0] for r in results]
        assert names == ["json-ld", "nextdata", "embedded", "dom", "api_sniffer"]
