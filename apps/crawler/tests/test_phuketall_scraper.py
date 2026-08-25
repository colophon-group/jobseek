from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitor import monitor_one
from src.core.scrapers.phuketall import can_handle, parse_html, scrape
from src.shared.http_retry import PaginationFetchError, ResponseBodyTooLargeError

_BOARDS_CSV = Path(__file__).parents[1] / "data" / "boards.csv"

HTML = """
<html><head>
  <meta property="og:url"
        content="https://www.phuketall.com/jobs/057289-213282-phuket/213282.html" />
</head>
<body>
  <div class="title-feedbox"><h2>ตำแหน่ง : <span>Medical Nurse</span></h2></div>
  <div class="jobs-derails-content">
    <div class="col-md-12"><strong>รายละเอียด</strong></div>
    <div class="col-md-12">Care for guests.<br>Maintain clinical records.</div>
    <div class="detailsbox">
      <div><strong>แผนก:</strong></div><div>MEDICAL</div>
      <div><strong>จำนวน:</strong></div><div>1 อัตรา</div>
      <div><strong>เวลาทำงาน:</strong></div><div>งานประจำ</div>
      <div><strong>ลงประกาศเมื่อ:</strong></div><div>21 ส.ค. 69</div>
    </div>
    <p>46/6 หมู่ 3 อำเภอถลาง ภูเก็ต 83110</p>
  </div>
</body></html>
"""


def test_can_handle_and_parse_thai_detail_page():
    assert can_handle([HTML]) == {}
    content = parse_html(HTML)

    assert content.title == "Medical Nurse"
    assert content.description == "<p>Care for guests.<br>Maintain clinical records.</p>"
    assert content.locations == ["46/6 หมู่ 3 อำเภอถลาง ภูเก็ต 83110"]
    assert content.employment_type == "full_time"
    assert content.date_posted == "2026-08-21"
    assert content.metadata == {"department": "MEDICAL", "quantity": "1 อัตรา"}


async def test_scrape_fetches_detail_page():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=HTML, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        content = await scrape(
            "https://www.phuketall.com/jobs/057289-213282-phuket/213282.html",
            {},
            client,
        )

    assert content.title == "Medical Nurse"
    assert content.description


@pytest.mark.parametrize(
    "marker",
    [
        "https://attacker.example/?next=https://www.phuketall.com/jobs/057289-213282-phuket/213282.html",
        "https://www.phuketall.com.attacker.example/jobs/057289-213282-phuket/213282.html",
        "https://www.phuketall.com:bad/jobs/057289-213282-phuket/213282.html",
        "http://www.phuketall.com/jobs/057289-213282-phuket/213282.html",
    ],
)
def test_can_handle_rejects_url_substrings_without_exact_provider_origin(marker: str):
    malicious = HTML.replace(
        "https://www.phuketall.com/jobs/057289-213282-phuket/213282.html",
        marker,
    )

    assert can_handle([malicious]) is None


async def test_scrape_streams_with_a_hard_body_cap():
    oversized = HTML + ("x" * (2 * 1024 * 1024))
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=oversized, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ResponseBodyTooLargeError):
            await scrape(
                "https://www.phuketall.com/jobs/057289-213282-phuket/213282.html",
                {},
                client,
            )


async def test_scrape_fails_whole_cycle_on_empty_detail_response():
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, text="", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(PaginationFetchError):
            await scrape(
                "https://www.phuketall.com/jobs/057289-213282-phuket/213282.html",
                {},
                client,
            )

    assert requests == 3


async def test_scrape_follows_only_same_provider_identity_redirects():
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path.endswith("/medical-nurse.html"):
            return httpx.Response(
                302,
                headers={
                    "location": "/jobs/057289-213282-phuket/213282.html",
                },
                request=request,
            )
        return httpx.Response(200, text=HTML, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(
            "https://www.phuketall.com/jobs/057289-213282-phuket/medical-nurse.html",
            {},
            client,
        )

    assert content.title == "Medical Nurse"
    assert requested == [
        "/jobs/057289-213282-phuket/medical-nurse.html",
        "/jobs/057289-213282-phuket/213282.html",
    ]


@pytest.mark.parametrize(
    "location",
    [
        "https://attacker.example/jobs/057289-213282-phuket/213282.html",
        "/jobs/057289-999999-phuket/999999.html",
    ],
)
async def test_scrape_rejects_redirects_outside_exact_origin_and_identity(location: str):
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(302, headers={"location": location}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="untrusted redirect"):
            await scrape(
                "https://www.phuketall.com/jobs/057289-213282-phuket/213282.html",
                {},
                client,
            )

    assert requests == 1


async def test_scrape_rejects_untrusted_input_origin_before_request():
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, text=HTML, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="untrusted origin"):
            await scrape(
                "https://www.phuketall.com.attacker.example/jobs/057289-213282-phuket/213282.html",
                {},
                client,
            )

    assert requests == 0


async def test_scrape_fails_whole_cycle_on_http_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(404, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(PaginationFetchError):
            await scrape(
                "https://www.phuketall.com/jobs/057289-213282-phuket/213282.html",
                {},
                client,
            )


async def test_scrape_rejects_mismatched_embedded_provider_identity():
    mismatched = HTML.replace("057289-213282", "057289-999999").replace(
        "/213282.html",
        "/999999.html",
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=mismatched, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="untrusted provider identity"):
            await scrape(
                "https://www.phuketall.com/jobs/057289-213282-phuket/213282.html",
                {},
                client,
            )


def _clinique_phuket_config() -> dict:
    with _BOARDS_CSV.open(newline="") as handle:
        row = next(
            row
            for row in csv.DictReader(handle)
            if row["board_slug"] == "clinique-la-prairie-phuket-phuketall"
        )
    return json.loads(row["monitor_config"])


async def test_monitor_canonicalizes_thai_english_and_title_churn_by_provider_id():
    # 213283 is the live marketing vacancy also syndicated as LinkedIn
    # 4457321546. Multiple locale/title routes must remain one PhuketAll job.
    profile = """
    <a href="/jobs/057289-213283-phuket/director-of-marketing.html">Marketing Director</a>
    <a href="/en/jobs/057289-213283-phuket/marketing-director-renamed.html">Renamed EN</a>
    <a href="/jobs/057289-213283-phuket/ชื่ออะไรก็ได้.html">Thai title</a>
    <a href="/jobs/057289-211035-phuket/sales-manager.html">Sales Manager</a>
    <a href="https://attacker.example/jobs/057289-999999-phuket/fake.html">Fake</a>
    """
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=profile, request=request)
    )

    async with httpx.AsyncClient(transport=transport) as client:
        result = await monitor_one(
            "https://www.phuketall.com/member/057289/profile.html",
            "dom",
            _clinique_phuket_config(),
            client,
        )

    assert result.urls == {
        "https://www.phuketall.com/jobs/057289-213283-phuket/213283.html",
        "https://www.phuketall.com/jobs/057289-211035-phuket/211035.html",
    }
