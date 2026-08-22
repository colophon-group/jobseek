from __future__ import annotations

from urllib.parse import quote

import httpx
import pytest

from src.core.scrapers.taleo import can_handle, parse_html, scrape


def _detail_html(*, title: str = "Pilot's Assistant") -> str:
    values = [""] * 40
    values[0] = "252963"
    values[9] = title
    values[10] = "17204"
    values[11] = "!*!" + quote(
        "<p>Help easyJet connect passengers across Europe.</p><p>#LI-hybrid</p>",
        safe="",
    )
    values[13] = "!*!" + quote(
        "<p>What we're looking for</p><ul><li>Careful planning</li></ul>",
        safe="",
    )
    values[15] = "Operations"
    values[17] = "Switzerland-Geneva-Geneva Airport"
    values[21] = "Flight Operations"
    values[23] = "Full-time"
    values[27] = "19/08/2026, 10:59:00 PM"

    def js_string(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    payload = ",".join(js_string(value) for value in values)
    return (
        "<html><body><script>"
        "api.fillList('requisitionDescriptionInterface', 'descRequisition', "
        f"[{payload}]);"
        "</script></body></html>"
    )


def _wipo_detail_html(*, title: str = "Roster - Administrative Assistant") -> str:
    values = [""] * 35
    values[0] = "61710"
    values[9] = title
    values[10] = "26255-FT_LT_ROS"
    values[11] = "World Intellectual Property Organization (WIPO)"
    values[12] = "G5"
    values[13] = "G5"
    values[14] = "2 years"
    values[15] = "2 years"
    values[17] = "CH-Geneva"
    values[18] = "10-Aug-2026"
    values[20] = "09-Sep-2026, 9:59:00 PM"
    values[22] = "!*!" + quote(
        "<h2>Organizational Context</h2><p>Support WIPO's global IP mission.</p>"
        "<h2>Duties</h2><ul><li>Coordinate administrative work.</li></ul>",
        safe="",
    )

    def js_string(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    payload = ",".join(js_string(value) for value in values)
    return (
        "<script>api.fillList('requisitionDescriptionInterface', 'descRequisition', "
        f"[{payload}]);</script>"
    )


def _wipo_internship_detail_html() -> str:
    values = [""] * 34
    values[0] = "61490"
    values[9] = "Internship Roster"
    values[10] = "26222-INT"
    values[11] = "various departments"
    values[12] = "variable - 8 weeks up to 12 months"
    values[15] = "Switzerland"
    values[16] = "18-Jul-2026, 5:08:47 PM"
    values[18] = "03-Jan-2027, 11:59:00 PM"
    values[20] = "!*!" + quote(
        "<h2>Internship Program</h2><p>Interns support WIPO teams.</p>"
        "<h2>Requirements</h2><ul><li>Current students or recent graduates.</li></ul>",
        safe="",
    )

    def js_string(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"

    payload = ",".join(js_string(value) for value in values)
    return (
        "<script>api.fillList('requisitionDescriptionInterface', 'descRequisition', "
        f"[{payload}]);</script>"
    )


def test_parse_taleo_enterprise_fill_list() -> None:
    content = parse_html(_detail_html())

    assert content.title == "Pilot's Assistant"
    assert content.locations == ["Switzerland-Geneva-Geneva Airport"]
    assert content.employment_type == "Full-time"
    assert content.job_location_type == "hybrid"
    assert content.description is not None
    assert "connect passengers" in content.description
    assert "Careful planning" in content.description
    assert content.extras == {
        "qualifications": "<p>What we're looking for</p><ul><li>Careful planning</li></ul>",
        "valid_through": "19/08/2026, 10:59:00 PM",
    }
    assert content.metadata == {
        "ats_job_id": "252963",
        "requisition_number": "17204",
        "business_area": "Operations",
        "organisation": "Flight Operations",
    }


def test_parse_wipo_taleo_enterprise_fill_list() -> None:
    content = parse_html(_wipo_detail_html())

    assert content.title == "Roster - Administrative Assistant"
    assert content.locations == ["CH-Geneva"]
    assert content.description is not None
    assert "Organizational Context" in content.description
    assert "Coordinate administrative work" in content.description
    assert content.date_posted == "10-Aug-2026"
    assert content.extras == {"valid_through": "09-Sep-2026, 9:59:00 PM"}
    assert content.metadata == {
        "ats_job_id": "61710",
        "requisition_number": "26255-FT_LT_ROS",
        "organisation": "World Intellectual Property Organization (WIPO)",
        "grade": "G5",
        "contract_duration": "2 years",
    }


def test_parse_wipo_taleo_decodes_percent_encoded_title() -> None:
    content = parse_html(
        _wipo_detail_html(title="Chief Financial and Performance Officer %26 Controller")
    )

    assert content.title == "Chief Financial and Performance Officer & Controller"


def test_parse_wipo_internship_taleo_enterprise_fill_list() -> None:
    content = parse_html(_wipo_internship_detail_html())

    assert content.title == "Internship Roster"
    assert content.locations == ["Switzerland"]
    assert content.description is not None
    assert "Internship Program" in content.description
    assert "Current students or recent graduates" in content.description
    assert content.date_posted == "18-Jul-2026, 5:08:47 PM"
    assert content.extras == {"valid_through": "03-Jan-2027, 11:59:00 PM"}
    assert content.metadata == {
        "ats_job_id": "61490",
        "requisition_number": "26222-INT",
        "organisation": "various departments",
        "contract_duration": "variable - 8 weeks up to 12 months",
    }


def test_can_handle_requires_usable_taleo_payload_on_half_the_pages() -> None:
    valid = _detail_html()

    assert can_handle([valid, valid, "<html>not Taleo</html>"]) == {}
    assert can_handle([valid, "<html>not Taleo</html>", "<html>also not Taleo</html>"]) is None


def test_parse_html_rejects_non_string_fill_list_values() -> None:
    html = (
        "<script>api.fillList('requisitionDescriptionInterface', 'descRequisition', [1]);</script>"
    )

    with pytest.raises(ValueError, match="non-string"):
        parse_html(html)


@pytest.mark.asyncio
async def test_scrape_fetches_public_taleo_enterprise_detail() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["job"] == "17204"
        return httpx.Response(200, text=_detail_html(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(
            "https://easyjet.taleo.net/careersection/2/jobdetail.ftl?job=17204&lang=en",
            {},
            client,
        )

    assert content.title == "Pilot's Assistant"
    assert content.description


@pytest.mark.asyncio
async def test_scrape_accepts_alphanumeric_taleo_requisition_key() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["job"] == "26255-FT_LT_ROS"
        return httpx.Response(200, text=_detail_html(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(
            "https://wipo.taleo.net/careersection/wp_2/jobdetail.ftl?job=26255-FT_LT_ROS&lang=en",
            {},
            client,
        )

    assert content.title == "Pilot's Assistant"
    assert content.description


@pytest.mark.asyncio
async def test_scrape_rejects_taleo_business_edition_url() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="invalid Taleo Enterprise"):
            await scrape(
                "https://phe.tbe.taleo.net/phe01/ats/careers/v2/viewRequisition?rid=1",
                {},
                client,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://easyjet.taleo.net/careersection/2/jobdetail.ftl?job=17204",
        "https://user@easyjet.taleo.net/careersection/2/jobdetail.ftl?job=17204",
        "https://easyjet.taleo.net:444/careersection/2/jobdetail.ftl?job=17204",
        "https://easyjet.taleo.net/careersection/../../jobdetail.ftl?job=17204",
        "https://easyjet.taleo.net/careersection/2/jobdetail.ftl?job=17204#other",
        "https://easyjet.taleo.net/careersection/2/jobdetail.ftl?job=../17204",
        "https://easyjet.taleo.net/careersection/2/jobdetail.ftl?job=17204%2Fother",
    ],
)
async def test_scrape_rejects_noncanonical_enterprise_url(url: str) -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="invalid Taleo Enterprise"):
            await scrape(url, {}, client)
