from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors.practicematch import (
    _canonical_job_url,
    _parse_landing_html,
    can_handle,
    discover,
)

BOARD_URL = "https://employer.practicematch.com/employer/acme-physician-jobs"


def _job(job_id: str, slug: str = "family-medicine/missouri/st-louis/acme") -> str:
    return (
        "https://www.practicematch.com/physicians/job-details.cfm/"
        f"{job_id}/{slug}?utm_source=employerlandingpage"
    )


def _landing(*urls: str) -> str:
    links = "".join(f'<a href="{url}">Job</a>' for url in urls)
    return f"""
    <html><body>
      <input id="facilityID" value="35789">
      <input id="facilityLandingURL" value="">
      <input id="contactID" value="0">
      <input id="siteID" value="101,202">
      <input id="oppIDs" value="0">
      <input id="hasMap" value="1">
      <input id="oppProf" value="1">
      <table><tbody id="oppListings">{links}</tbody></table>
    </body></html>
    """


def _page(*urls: str) -> dict[str, str]:
    links = "".join(f'<a href="{url}">Job</a>' for url in urls)
    return {"OPPLISTINGSHTML": f"<tr><td>{links}</td></tr>"}


class TestParsing:
    def test_canonicalizes_job_url_to_stable_numeric_route(self):
        assert _canonical_job_url(_job("1107971")) == (
            "https://www.practicematch.com/physicians/job-details.cfm/1107971/"
        )

    def test_rejects_apply_and_other_hosts(self):
        assert _canonical_job_url("https://www.practicematch.com/physicians/apply.cfm/1") is None
        assert _canonical_job_url("https://example.com/physicians/job-details.cfm/1/") is None

    def test_extracts_hidden_form_state_and_only_job_links(self):
        hidden, urls = _parse_landing_html(
            _landing(_job("1107971"))
            + '<a href="https://www.practicematch.com/physicians/apply.cfm/1107971">Apply</a>'
        )

        assert hidden == {
            "facilityID": "35789",
            "facilityLandingURL": "",
            "contactID": "0",
            "siteID": "101,202",
            "oppIDs": "0",
            "hasMap": "1",
            "oppProf": "1",
        }
        assert urls == {"https://www.practicematch.com/physicians/job-details.cfm/1107971/"}


class TestCanHandle:
    async def test_detects_employer_landing_page_without_network_request(self):
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(BOARD_URL, client)

        assert result == {"proxy": True}
        assert calls == 0

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/employer/acme-physician-jobs",
            "https://employer.practicematch.com/employer/HospitalListings.cfm",
            "https://employer.practicematch.com/physicians/jobs/",
        ],
    )
    async def test_rejects_unrelated_routes(self, url: str):
        async with httpx.AsyncClient() as client:
            assert await can_handle(url, client) is None


class TestDiscover:
    async def test_paginates_physician_and_advanced_practitioner_results(self):
        posts: list[dict[str, list[str]]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_landing(_job("1001")))

            form = parse_qs(request.content.decode(), keep_blank_values=True)
            posts.append(form)
            profession = form["professionID"][0]
            page = int(form["pageNum"][0])
            assert form["facilityID"] == ["35789"]
            assert form["siteID"] == ["101,202"]
            assert form["hasMap"] == ["0"]
            assert request.headers["x-requested-with"] == "XMLHttpRequest"

            if (profession, page) == ("1", 2):
                return httpx.Response(200, json=_page(_job("1002")))
            if (profession, page) == ("-1", 1):
                return httpx.Response(200, json=_page(_job("2001")))
            return httpx.Response(200, json=_page())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

        assert result == {
            "https://www.practicematch.com/physicians/job-details.cfm/1001/",
            "https://www.practicematch.com/physicians/job-details.cfm/1002/",
            "https://www.practicematch.com/physicians/job-details.cfm/2001/",
        }
        assert [(p["professionID"][0], p["pageNum"][0]) for p in posts] == [
            ("1", "2"),
            ("1", "3"),
            ("-1", "1"),
            ("-1", "2"),
        ]

    async def test_stops_when_provider_repeats_the_last_page(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_landing(_job("1001")))
            form = parse_qs(request.content.decode())
            if form["professionID"] == ["1"]:
                return httpx.Response(200, json=_page(_job("1001")))
            return httpx.Response(200, json=_page())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

        assert result == {"https://www.practicematch.com/physicians/job-details.cfm/1001/"}

    async def test_missing_facility_id_fails_closed(self):
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><body>No form state</body></html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="missing facilityID"):
                await discover({"board_url": BOARD_URL, "metadata": {}}, client)

    async def test_max_pages_marks_result_truncated(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, text=_landing())
            form = parse_qs(request.content.decode())
            job_id = f"{form['professionID'][0].replace('-', '9')}{form['pageNum'][0]}"
            return httpx.Response(200, json=_page(_job(job_id)))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": BOARD_URL, "metadata": {"max_pages": 1}},
                client,
            )

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 2
