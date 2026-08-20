from __future__ import annotations

import json

import httpx
import pytest

import src.core.monitors._successfactors_legacy as legacy
from src.core.monitors.rss import can_handle, discover, discover_stream
from src.core.scrapers.dom import parse_html
from src.probe_boards import probe_row
from src.redis_queue import delay_for_domain
from src.shared.successfactors import (
    SuccessFactorsLegacyBoard,
    is_successfactors_host,
    successfactors_legacy_board_from_metadata,
    successfactors_legacy_board_from_url,
)
from src.workspace._compat import auto_scraper_type, detect_ats_from_url


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=True).replace("/", "\\/")


class _Graph:
    def __init__(self):
        self.declarations: list[str] = []
        self.assignments: list[str] = []
        self.index = 0

    def obj(self) -> str:
        name = f"s{self.index}"
        self.index += 1
        self.declarations.append(f"var {name}={{}};")
        return name

    def array(self) -> str:
        name = f"s{self.index}"
        self.index += 1
        self.declarations.append(f"var {name}=[];")
        return name

    def assign(self, owner: str, key: str, value: str) -> None:
        self.assignments.append(f"{owner}.{key}={value};")

    def item(self, owner: str, index: int, value: str) -> None:
        self.assignments.append(f"{owner}[{index}]={value};")

    def render(self, callback: str) -> str:
        return "".join(
            [
                "throw 'allowScriptTagRemoting is false.';\n//#DWR-INSERT\n//#DWR-REPLY\n",
                *self.declarations,
                *self.assignments,
                callback,
            ]
        )


def _filters(graph: _Graph, total: int) -> str:
    filters = graph.obj()
    configs = graph.obj()
    fields = graph.array()
    location = graph.obj()
    graph.assign(filters, "postingCount", _quote(str(total)))
    graph.assign(filters, "configs", configs)
    graph.assign(configs, "filters", fields)
    graph.item(fields, 0, location)
    graph.assign(location, "fieldName", _quote("customFilter_filter2"))
    graph.assign(location, "label", _quote("Duty Station"))
    return filters


def _pagination(graph: _Graph, *, total: int, page: int, page_size: int) -> str:
    options = graph.obj()
    pagination = graph.obj()
    graph.assign(options, "pagination", pagination)
    graph.assign(options, "sortByColumn", _quote("JOB_POSTING_DATE"))
    graph.assign(options, "sortOrder", _quote("DESC"))
    graph.assign(pagination, "currentPage", str(page))
    graph.assign(pagination, "pageSize", str(page_size))
    graph.assign(pagination, "startRow", str(((page - 1) * page_size) + 1))
    graph.assign(pagination, "endRow", str(page * page_size))
    graph.assign(pagination, "totalCount", str(total))
    return options


def _posting(graph: _Graph, job_id: int, *, with_location: bool = False) -> str:
    posting = graph.obj()
    other_values = graph.array()
    graph.assign(posting, "id", str(job_id))
    graph.assign(posting, "title", _quote(f"Engineer {job_id}"))
    graph.assign(posting, "defaultLocale", _quote("en_GB"))
    graph.assign(posting, "postingDate", _quote("03/08/2026"))
    graph.assign(posting, "otherValues", other_values)
    if with_location:
        group = graph.array()
        field = graph.obj()
        graph.item(other_values, 0, group)
        graph.item(group, 0, field)
        graph.assign(field, "fieldId", _quote("filter2"))
        graph.assign(field, "longVal", "null")
        graph.assign(field, "shortVal", _quote("Zurich - CH"))
    return posting


def _initial_response(total: int) -> str:
    graph = _Graph()
    root = graph.obj()
    filters = _filters(graph, total)
    results = graph.obj()
    postings = graph.array()
    options = _pagination(graph, total=total, page=1, page_size=10)
    graph.assign(root, "filters", filters)
    graph.assign(root, "results", results)
    graph.assign(results, "postingCount", str(total))
    graph.assign(
        results,
        "detailURLPrefix",
        _quote(
            "/career?career_ns=job_listing&company=Acme&navBarLevel=JOB_SEARCH&"
            "rcm_site_locale=en_GB&career_job_req_id="
        ),
    )
    graph.assign(results, "options", options)
    graph.assign(results, "postings", postings)
    for index in range(min(total, 10)):
        graph.item(postings, index, _posting(graph, index + 1))
    return graph.render("dwr.engine._remoteHandleCallback('0','0',{payload:s0});")


def _search_response(
    total: int,
    page: int,
    ids: list[int],
    *,
    reported_total: int | None = None,
) -> str:
    result_total = total if reported_total is None else reported_total
    graph = _Graph()
    filters = _filters(graph, result_total)
    results = graph.obj()
    postings = graph.array()
    options = _pagination(graph, total=result_total, page=page, page_size=100)
    graph.assign(results, "postingCount", str(result_total))
    graph.assign(
        results,
        "detailURLPrefix",
        _quote(
            "/career?career_ns=job_listing&company=Acme&navBarLevel=JOB_SEARCH&"
            "rcm_site_locale=en_GB&career_job_req_id="
        ),
    )
    graph.assign(results, "options", options)
    graph.assign(results, "postings", postings)
    for index, job_id in enumerate(ids):
        graph.item(postings, index, _posting(graph, job_id, with_location=index == 0))
    return graph.render(
        f"dwr.engine._remoteHandleCallback('{page}','0',{{filters:{filters},results:{results}}});"
    )


def _bootstrap_response(*, status: int = 200, headers: dict[str, str] | None = None):
    document = (
        '<html><script>var ajaxSecKey="abcDEF0123%2f456%3d";'
        "function getInitialJobSearchData(){}; careerJobSearchController; "
        'const x={companyId: "Acme"};</script></html>'
    )
    response_headers = {"x-event-id": "EVENT-RCM-CAREER-test-12345678"}
    response_headers.update(headers or {})
    return httpx.Response(status, text=document, headers=response_headers)


def _board() -> dict:
    return {
        "board_url": "https://career5.successfactors.eu/career?company=Acme",
        "metadata": {
            "preset": "successfactors",
            "variant": "legacy",
            "host": "career5.successfactors.eu",
            "company": "Acme",
        },
    }


class TestIdentity:
    @pytest.mark.parametrize(
        ("url", "host", "company"),
        [
            (
                "https://career012.successfactors.eu/career?company=banquepict",
                "career012.successfactors.eu",
                "banquepict",
            ),
            (
                "https://career15.sapsf.cn/career?company=volkswag09",
                "career15.sapsf.cn",
                "volkswag09",
            ),
            (
                "https://performancemanager4.successfactors.com/career?company=AMD",
                "performancemanager4.successfactors.com",
                "AMD",
            ),
        ],
    )
    def test_accepts_inventory_legacy_shapes(self, url, host, company):
        assert successfactors_legacy_board_from_url(url) == SuccessFactorsLegacyBoard(host, company)

    @pytest.mark.parametrize(
        "url",
        [
            "http://career5.successfactors.eu/career?company=Acme",
            "https://evil.example/career?company=Acme",
            "https://career5.successfactors.eu/career?company=Acme&keyword=engineer",
            "https://career5.successfactors.eu/career?company=Acme&career_job_req_id=1",
            "https://career5.successfactors.eu/career?company=Acme&company=Other",
        ],
    )
    def test_rejects_unsafe_or_scoped_urls(self, url):
        assert successfactors_legacy_board_from_url(url) is None

    def test_metadata_and_url_must_agree(self):
        assert (
            successfactors_legacy_board_from_metadata(
                {
                    "host": "career5.successfactors.eu",
                    "company": "Acme",
                    "listing_url": "https://career5.successfactors.eu/career?company=Other",
                }
            )
            is None
        )

    def test_modern_and_legacy_host_classification(self):
        assert is_successfactors_host("career55.sapsf.eu")
        assert is_successfactors_host("iter.jobs.hr.cloud.sap")
        assert detect_ats_from_url("https://career55.sapsf.eu/career?company=Midea") == "rss"
        assert detect_ats_from_url("https://iter.jobs.hr.cloud.sap") == "rss"


class TestSafeDwrParser:
    def test_parses_nested_assignments_without_execution(self):
        root = legacy._parse_dwr(_initial_response(2), batch=0, initial=True)
        assert root["results"]["postingCount"] == 2
        assert len(root["results"]["postings"]) == 2

    def test_string_contents_are_not_scanned_as_statements(self):
        response = _initial_response(1).replace(
            _quote("Engineer 1"),
            _quote("var s0={}; alert(1);"),
        )
        root = legacy._parse_dwr(response, batch=0, initial=True)
        assert root["results"]["postings"][0]["title"] == "var s0={}; alert(1);"

    def test_rejects_unsupported_standalone_statement(self):
        response = _initial_response(1).replace("var s0={};", "var s0={};alert(1);", 1)
        with pytest.raises(legacy.SuccessFactorsLegacyProtocolError, match="supported grammar"):
            legacy._parse_dwr(response, batch=0, initial=True)

    @pytest.mark.parametrize(
        "response",
        [
            "//#DWR-REPLY\ndwr.engine._remoteHandleException('0','0',{});",
            "//#DWR-REPLY\nvar s0={};s0.x=(function(){return 1})();"
            "dwr.engine._remoteHandleCallback('0','0',{payload:s0});",
            "//#DWR-REPLY\nvar s0={};var s0={};"
            "dwr.engine._remoteHandleCallback('0','0',{payload:s0});",
        ],
    )
    def test_rejects_exceptions_unsupported_values_and_redeclarations(self, response):
        with pytest.raises(legacy.SuccessFactorsLegacyProtocolError):
            legacy._parse_dwr(response, batch=0, initial=True)


class TestDiscovery:
    async def test_complete_two_page_stream_is_hybrid_and_enrichment_safe(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request):
            requests.append(request)
            if request.method == "GET":
                return _bootstrap_response()
            assert request.headers["x-sap-page-info"] == "companyId=Acme"
            assert request.headers["x-event-id"] == "EVENT-RCM-CAREER-test-12345678"
            if request.url.path.endswith("getInitialJobSearchData.dwr"):
                return httpx.Response(200, text=_initial_response(101))
            body = request.content.decode()
            if "c0-e1=number:1" in body:
                return httpx.Response(200, text=_search_response(101, 1, list(range(1, 101))))
            return httpx.Response(200, text=_search_response(101, 2, [101]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            batches = [batch async for batch in discover_stream(_board(), client)]

        assert [len(batch.urls) for batch in batches] == [100, 1]
        assert all(batch.hybrid for batch in batches)
        assert sum(len(batch.urls) for batch in batches) == 101
        first = batches[0].jobs_by_url[
            "https://career5.successfactors.eu/career?career_ns=job_listing&company=Acme&"
            "navBarLevel=JOB_SEARCH&rcm_site_locale=en_GB&career_job_req_id=1"
        ]
        assert first.title == "Engineer 1"
        assert first.locations == ["Zurich - CH"]
        assert first.date_posted == "2026-08-03"
        assert first.language == "en"
        assert [request.method for request in requests] == ["GET", "POST", "POST", "POST"]

    async def test_materialized_result_preserves_hybrid_flag(self):
        def handler(request: httpx.Request):
            if request.method == "GET":
                return _bootstrap_response()
            if request.url.path.endswith("getInitialJobSearchData.dwr"):
                return httpx.Response(200, text=_initial_response(1))
            return httpx.Response(200, text=_search_response(1, 1, [1]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(_board(), client)
        assert result.hybrid is True
        assert len(result.urls) == 1

    async def test_authoritative_empty_board_yields_success_batch(self):
        def handler(request: httpx.Request):
            if request.method == "GET":
                return _bootstrap_response()
            return httpx.Response(200, text=_initial_response(0))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            batches = [batch async for batch in discover_stream(_board(), client)]
        assert len(batches) == 1
        assert batches[0].urls == set()
        assert batches[0].hybrid is True

    async def test_total_drift_fails_instead_of_returning_partial_jobs(self):
        def handler(request: httpx.Request):
            if request.method == "GET":
                return _bootstrap_response()
            if request.url.path.endswith("getInitialJobSearchData.dwr"):
                return httpx.Response(200, text=_initial_response(101))
            return httpx.Response(
                200,
                text=_search_response(101, 1, list(range(1, 101)), reported_total=100),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(legacy.SuccessFactorsLegacyProtocolError, match="total changed"):
                await discover(_board(), client)

    async def test_transient_dwr_failure_retries_and_recovers(self, monkeypatch):
        attempts = 0

        async def no_sleep(_delay):
            return None

        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", no_sleep)

        def handler(request: httpx.Request):
            nonlocal attempts
            if request.method == "GET":
                return _bootstrap_response()
            if request.url.path.endswith("getInitialJobSearchData.dwr"):
                return httpx.Response(200, text=_initial_response(1))
            attempts += 1
            if attempts == 1:
                return httpx.Response(503, text="temporary")
            return httpx.Response(200, text=_search_response(1, 1, [1]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(_board(), client)
        assert len(result.urls) == 1
        assert attempts == 2

    async def test_oversized_dwr_response_aborts_at_streamed_cap(self, monkeypatch):
        monkeypatch.setattr(legacy, "MAX_DWR_CHARS", 64)

        def handler(request: httpx.Request):
            if request.method == "GET":
                return _bootstrap_response()
            return httpx.Response(200, content=b"x" * 65)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(
                legacy.SuccessFactorsLegacyProtocolError,
                match="exceeded the safety cap",
            ):
                await discover(_board(), client)


class TestDetectionAndWorkspace:
    async def test_direct_legacy_probe_returns_canonical_config(self):
        def handler(request: httpx.Request):
            if request.method == "GET":
                return _bootstrap_response()
            return httpx.Response(200, text=_initial_response(1))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(_board()["board_url"], client)
        assert result == {
            "preset": "successfactors",
            "variant": "legacy",
            "host": "career5.successfactors.eu",
            "company": "Acme",
            "listing_url": (
                "https://career5.successfactors.eu/career?company=Acme&"
                "career_ns=job_listing_summary&navBarLevel=JOB_SEARCH"
            ),
            "jobs": 1,
        }

    async def test_direct_legacy_probe_retries_invalid_initial_session(self):
        requests = {"get": 0, "post": 0}

        def handler(request: httpx.Request):
            method = request.method.casefold()
            requests[method] += 1
            if request.method == "GET":
                return _bootstrap_response()
            if requests["post"] == 1:
                return httpx.Response(200, text="<html>session expired</html>")
            return httpx.Response(200, text=_initial_response(1))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(_board()["board_url"], client)

        assert result is not None
        assert result["variant"] == "legacy"
        assert result["jobs"] == 1
        assert requests == {"get": 2, "post": 2}

    async def test_scheduled_probe_reuses_legacy_runtime_parser(self):
        def handler(request: httpx.Request):
            if request.method == "GET":
                return _bootstrap_response()
            return httpx.Response(200, text=_initial_response(1))

        row = {
            "board_slug": "acme",
            "board_url": _board()["board_url"],
            "monitor_type": "rss",
            "monitor_config": json.dumps(_board()["metadata"]),
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await probe_row(row, client)
        assert result.status == "ok"
        assert result.message == "legacy SuccessFactors DWR: 1 jobs"

    async def test_scheduled_feed_probe_rejects_html_200(self):
        row = {
            "board_slug": "acme",
            "board_url": "https://jobs.acme.example/search",
            "monitor_type": "rss",
            "monitor_config": json.dumps(
                {
                    "preset": "successfactors",
                    "variant": "feed",
                    "feed_url": "https://jobs.acme.example/googlefeed.xml",
                }
            ),
        }

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, text="<html>retired</html>")
            )
        ) as client:
            result = await probe_row(row, client)

        assert result.status == "fail"
        assert result.message == "feed did not return valid RSS/XML"

    async def test_migrated_legacy_url_falls_forward_to_google_feed(self):
        feed = """<?xml version="1.0"?><rss><channel><item>
        <title>Engineer</title><link>https://migrated.example/job/1</link>
        </item></channel></rss>"""

        def handler(request: httpx.Request):
            if request.url.host == "career5.successfactors.eu":
                return httpx.Response(
                    302,
                    headers={"location": "https://migrated.example/search/"},
                )
            if request.url.path == "/googlefeed.xml":
                return httpx.Response(200, text=feed)
            return httpx.Response(200, text="<html></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(_board()["board_url"], client)
        assert result == {
            "preset": "successfactors",
            "variant": "feed",
            "feed_url": "https://migrated.example/googlefeed.xml",
            "jobs": 1,
        }

    async def test_wrapper_page_follows_embedded_legacy_board_to_google_feed(self):
        wrapper = (
            '<html><body><a href="https://career5.successfactors.eu/career?'
            "career_company=Acme&amp;lang=en_US&amp;company=Acme&amp;site=&amp;"
            'loginFlowRequired=true&amp;_s.crb=session">Apply</a></body></html>'
        )
        feed = """<?xml version="1.0"?><rss><channel><item>
        <title>Engineer</title><link>https://careers.acme.example/job/1</link>
        </item></channel></rss>"""

        def handler(request: httpx.Request):
            if request.url.host == "jobs.acme.example":
                return httpx.Response(200, text=wrapper)
            if request.url.host == "career5.successfactors.eu":
                return httpx.Response(
                    302,
                    headers={"location": "https://careers.acme.example/search/"},
                )
            if request.url == httpx.URL("https://careers.acme.example/googlefeed.xml"):
                return httpx.Response(200, text=feed)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.acme.example/careers", client)

        assert result == {
            "preset": "successfactors",
            "variant": "feed",
            "feed_url": "https://careers.acme.example/googlefeed.xml",
            "jobs": 1,
        }

    async def test_wrapper_page_rejects_job_specific_legacy_link(self):
        wrapper = (
            '<html><body><a href="https://career5.successfactors.eu/career?'
            'company=Acme&amp;career_job_req_id=123">Apply</a></body></html>'
        )
        requested_hosts: list[str] = []

        def handler(request: httpx.Request):
            requested_hosts.append(request.url.host)
            if request.url.host == "jobs.acme.example":
                return httpx.Response(200, text=wrapper)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.acme.example/careers", client)

        assert result is None
        assert "career5.successfactors.eu" not in requested_hosts

    @pytest.mark.parametrize(
        "links",
        [
            (
                "https://career5.successfactors.eu/career?company=Acme",
                "https://career5.successfactors.eu/career?company=Other",
            ),
            (
                "https://career5.successfactors.eu/career?company=Acme",
                "https://career6.successfactors.eu/career?company=Acme",
            ),
        ],
        ids=["mixed-companies", "mixed-tenants"],
    )
    async def test_wrapper_page_rejects_mixed_legacy_identities(self, links):
        wrapper = (
            "<html><body>"
            + "".join(f'<a href="{link}">Apply</a>' for link in links)
            + "</body></html>"
        )
        requested_hosts: list[str] = []

        def handler(request: httpx.Request):
            requested_hosts.append(request.url.host)
            if request.url.host == "jobs.acme.example":
                return httpx.Response(200, text=wrapper)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.acme.example/careers", client)

        assert result is None
        assert "career5.successfactors.eu" not in requested_hosts
        assert "career6.successfactors.eu" not in requested_hosts

    async def test_legacy_host_redirect_is_resolved_before_configuring(self):
        original = "https://performancemanager4.successfactors.com/career?company=Acme"

        def handler(request: httpx.Request):
            if request.url.host == "performancemanager4.successfactors.com":
                return httpx.Response(
                    302,
                    headers={
                        "location": (
                            "https://career4.successfactors.com/career?company=Acme&"
                            "career_ns=job_listing_summary&navBarLevel=JOB_SEARCH"
                        )
                    },
                )
            if request.method == "GET":
                return _bootstrap_response()
            return httpx.Response(200, text=_initial_response(1))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(original, client)
        assert result["variant"] == "legacy"
        assert result["host"] == "career4.successfactors.com"
        assert result["company"] == "Acme"

    def test_ws_auto_configures_static_dom_description_enrichment(self):
        scraper = auto_scraper_type("rss", {"preset": "successfactors", "variant": "legacy"})
        assert scraper is not None
        scraper_type, config = scraper
        assert scraper_type == "dom"
        assert config["scope"] == ".joqReqDescription"
        assert config.get("render", False) is False
        assert config["enrich"] == ["description"]
        content = parse_html(
            '<main><div class="joqReqDescription"><p>Real description</p></div>'
            "<p>Outside chrome</p></main>",
            config,
        )
        assert "Real description" in content.description
        assert "Outside chrome" not in content.description

    def test_feed_variant_keeps_skip_scraper(self):
        assert auto_scraper_type("rss", {"preset": "successfactors", "variant": "feed"}) == (
            "skip",
            None,
        )

    def test_shared_sap_hosts_use_ats_throttle(self):
        assert delay_for_domain("career5.successfactors.eu") == delay_for_domain(
            "career55.sapsf.eu"
        )
        assert delay_for_domain("iter.jobs.hr.cloud.sap") == delay_for_domain("career55.sapsf.eu")
