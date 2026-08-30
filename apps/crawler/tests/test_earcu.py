from __future__ import annotations

import httpx
import pytest

from src.core.monitors.earcu import (
    EArcuParseError,
    _candidate_feed_urls,
    _parse_feed,
    can_handle,
    discover,
)
from src.shared.http_retry import PaginationFetchError

FEED = """\
<?xml version="1.0" encoding="UTF-8"?>
<positions>
  <position>
    <LastPublishedDate>2026-08-11T13:06:50.987</LastPublishedDate>
    <Id>1426</Id>
    <VacancyRef>1408</VacancyRef>
    <JobTitle>Travel Specialist</JobTitle>
    <DisplaySalaryDescription>Competitive</DisplaySalaryDescription>
    <JobFunction>Sales</JobFunction>
    <Brand>Travelbag</Brand>
    <DescriptionURL>https://careers.example/jobs/vacancy/travel-specialist/1426/description/</DescriptionURL>
    <Locations><Location>Marlow</Location><Location>Hybrid</Location></Locations>
    <Description><![CDATA[<div><p>Create exceptional trips.</p></div>]]></Description>
  </position>
  <position>
    <LastPublishedDate>2026-08-12T09:30:00</LastPublishedDate>
    <Id>1425</Id>
    <VacancyRef>1407</VacancyRef>
    <JobTitle>Branch Manager</JobTitle>
    <DescriptionURL>/jobs/vacancy/branch-manager/1425/description/</DescriptionURL>
    <Locations><Location>Knutsford</Location></Locations>
    <Description><![CDATA[<p>Lead the branch.</p>]]></Description>
  </position>
</positions>
"""


def test_candidate_feed_url_preserves_portal_prefix():
    assert _candidate_feed_urls("https://careers.example/jobs/vacancy/find/results/") == [
        "https://careers.example/jobs/allvacancies/",
        "https://careers.example/allvacancies/",
    ]


def test_candidate_feed_url_supports_legacy_aspx_listing():
    assert _candidate_feed_urls(
        "https://careers.example/vacancies/vacancy-search-results.aspx"
    ) == [
        "https://careers.example/vacancies/allvacancies/",
        "https://careers.example/allvacancies/",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "http://careers.example/jobs/vacancy/find/results/",
        "https://user@careers.example/jobs/vacancy/find/results/",
        "https://careers.example:8443/jobs/vacancy/find/results/",
        "https://careers.example/jobs/vacancy/find/results/#fragment",
        "https://careers.example:bad/jobs/vacancy/find/results/",
    ],
)
def test_candidate_feed_urls_reject_unsafe_board_urls(url):
    assert _candidate_feed_urls(url) == []


def test_parse_feed_returns_rich_jobs():
    jobs = _parse_feed(FEED, "https://careers.example/jobs/allvacancies/")

    assert len(jobs) == 2
    assert jobs[0].title == "Travel Specialist"
    assert jobs[0].description == "<div><p>Create exceptional trips.</p></div>"
    assert jobs[0].locations == ["Marlow", "Hybrid"]
    assert jobs[0].date_posted == "2026-08-11T13:06:50.987"
    assert jobs[0].metadata == {
        "reference": "1408",
        "job_function": "Sales",
        "brand": "Travelbag",
        "salary_description": "Competitive",
    }
    assert jobs[1].url == "https://careers.example/jobs/vacancy/branch-manager/1425/description/"


def test_parse_feed_rejects_incomplete_position():
    with pytest.raises(EArcuParseError, match="missing DescriptionURL or JobTitle"):
        _parse_feed(
            "<positions><position><JobTitle>Missing URL</JobTitle></position></positions>",
            "https://careers.example/jobs/allvacancies/",
        )


@pytest.mark.parametrize(
    "description_url",
    [
        "http://careers.example/jobs/vacancy/role/1/description/",
        "https://other.example/jobs/vacancy/role/1/description/",
        "https://user@careers.example/jobs/vacancy/role/1/description/",
        "https://careers.example:8443/jobs/vacancy/role/1/description/",
        "https://careers.example/jobs/not-a-vacancy/1/",
        "https://careers.example/jobs/vacancy/role/1/description/?token=x",
        "https://careers.example/jobs/vacancy/role/1/description/#fragment",
    ],
)
def test_parse_feed_rejects_unsafe_job_urls(description_url):
    feed = (
        "<positions><position><JobTitle>Role</JobTitle>"
        f"<DescriptionURL>{description_url}</DescriptionURL>"
        "</position></positions>"
    )
    with pytest.raises(EArcuParseError, match="unsafe job URL"):
        _parse_feed(feed, "https://careers.example/jobs/allvacancies/")


def test_parse_feed_rejects_duplicate_urls_and_xml_entities():
    duplicate = FEED.replace(
        "/jobs/vacancy/branch-manager/1425/description/",
        "https://careers.example/jobs/vacancy/travel-specialist/1426/description/",
    )
    with pytest.raises(EArcuParseError, match="duplicate job URL"):
        _parse_feed(duplicate, "https://careers.example/jobs/allvacancies/")

    entity = """<!DOCTYPE positions [<!ENTITY x "expanded">]>
    <positions><position><JobTitle>&x;</JobTitle>
    <DescriptionURL>/jobs/vacancy/role/1/description/</DescriptionURL>
    </position></positions>"""
    with pytest.raises(EArcuParseError, match="Invalid eArcu XML"):
        _parse_feed(entity, "https://careers.example/jobs/allvacancies/")


def test_parse_feed_accepts_valid_empty_inventory():
    assert _parse_feed("<positions />", "https://careers.example/allvacancies/") == []


def test_parse_feed_rejects_malformed_or_unexpected_xml():
    with pytest.raises(EArcuParseError, match="Invalid eArcu XML"):
        _parse_feed("<positions>", "https://careers.example/allvacancies/")
    with pytest.raises(EArcuParseError, match="Unexpected eArcu root element"):
        _parse_feed("<urlset />", "https://careers.example/allvacancies/")


async def test_can_handle_bypasses_waf_listing_and_detects_feed():
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/jobs/allvacancies/":
            return httpx.Response(200, text=FEED)
        if request.url.path == "/jobs/vacancy/find/results/":
            return httpx.Response(202, headers={"x-amzn-waf-action": "challenge"})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await can_handle(
            "https://careers.example/jobs/vacancy/find/results/",
            client,
        )

    assert result == {
        "feed_url": "https://careers.example/jobs/allvacancies/",
        "jobs": 2,
    }
    assert "https://careers.example/jobs/vacancy/find/results/" not in requested


async def test_can_handle_retains_legacy_listing_when_feed_requires_proxy():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, text="challenge", request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await can_handle(
            "https://careers.example/vacancies/vacancy-search-results.aspx",
            client,
        )

    assert result == {
        "feed_url": "https://careers.example/vacancies/allvacancies/",
        "proxy": True,
    }


async def test_can_handle_does_not_guess_generic_blocked_page():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(403, text="challenge", request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PaginationFetchError, match="status=403"):
            await can_handle("https://careers.example/careers", client)


async def test_discover_uses_configured_feed():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=FEED))
    async with httpx.AsyncClient(transport=transport) as client:
        jobs = await discover(
            {
                "board_url": "https://careers.example/jobs/vacancy/find/results/",
                "metadata": {"feed_url": "https://careers.example/jobs/allvacancies/"},
            },
            client,
        )

    assert [job.title for job in jobs] == ["Travel Specialist", "Branch Manager"]


async def test_discover_fails_closed_when_configured_feed_is_missing():
    transport = httpx.MockTransport(lambda request: httpx.Response(404))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(EArcuParseError, match="feed not found"):
            await discover(
                {
                    "board_url": "https://careers.example/jobs/vacancy/find/results/",
                    "metadata": {"feed_url": "https://careers.example/jobs/allvacancies/"},
                },
                client,
            )


async def test_discover_rejects_a_configured_feed_outside_the_board_portal():
    transport = httpx.MockTransport(lambda request: pytest.fail("must not fetch unsafe feed"))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(EArcuParseError, match="Unsafe eArcu feed URL"):
            await discover(
                {
                    "board_url": "https://careers.example/jobs/vacancy/find/results/",
                    "metadata": {"feed_url": "https://other.example/jobs/allvacancies/"},
                },
                client,
            )


async def test_can_handle_rejects_non_earcu_xml():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="<urlset><url /></urlset>")
    )
    async with httpx.AsyncClient(transport=transport) as client:
        assert await can_handle("https://example.com/careers", client) is None


async def test_can_handle_does_not_fall_back_after_a_malformed_scoped_feed():
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/jobs/allvacancies/":
            return httpx.Response(200, text="<html>soft failure</html>")
        return httpx.Response(200, text=FEED)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await can_handle("https://careers.example/jobs/vacancy/find/results/", client) is None
        )
    assert requested == ["/jobs/allvacancies/"]
