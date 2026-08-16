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
    ],
)
async def test_scrape_rejects_noncanonical_enterprise_url(url: str) -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="invalid Taleo Enterprise"):
            await scrape(url, {}, client)
