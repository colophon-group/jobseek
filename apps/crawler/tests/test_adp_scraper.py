from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from src.core.scrapers import get_scraper_type
from src.core.scrapers.adp import _docx_to_html, _parse_job_url, scrape

JOB_URL = (
    "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
    "recruitment.html?ccId=19000101_000001"
    "&cid=0b103883-5bcb-4c19-89f9-e2b305fc27b0"
    "&lang=en_US&jobId=9202920507783_1"
)


def _docx_bytes() -> bytes:
    document = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Role overview</w:t></w:r></w:p>
    <w:p><w:r><w:t>Build detection products &amp; services.</w:t></w:r></w:p>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr>
      <w:r><w:t>Own launches</w:t></w:r>
    </w:p>
    <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr>
      <w:r><w:t>Research markets</w:t></w:r>
    </w:p>
  </w:body>
</w:document>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", document)
    return output.getvalue()


def _detail(*, description: str, links: list[dict] | None = None) -> dict:
    return {
        "itemID": "9202920507783_1",
        "clientRequisitionID": "1041",
        "requisitionTitle": "Product Marketing Manager",
        "requisitionDescription": description,
        "postDate": "2026-06-09T12:18:00.000-04:00",
        "workLevelCode": {"shortName": "Full Time"},
        "requisitionLocations": [
            {"nameCode": {"shortName": " Naperville,  IL, US "}},
        ],
        "payGradeRange": {
            "minimumRate": {"amountValue": 110000, "currencyCode": "USD"},
            "maximumRate": {"amountValue": 125000, "currencyCode": "USD"},
        },
        "customFieldGroup": {
            "codeFields": [
                {
                    "shortName": "Annually",
                    "nameCode": {"codeValue": "SalaryType"},
                }
            ],
            "stringFields": [
                {
                    "stringValue": "594054",
                    "nameCode": {"codeValue": "ExternalJobID"},
                },
                {
                    "stringValue": "Professional",
                    "nameCode": {"codeValue": "JobClass"},
                },
            ],
        },
        "links": links or [],
    }


def test_registered():
    assert get_scraper_type("adp") is not None


def test_parse_job_url():
    assert _parse_job_url(JOB_URL) == (
        "https://workforcenow.adp.com/mascsr/default",
        "9202920507783_1",
        "0b103883-5bcb-4c19-89f9-e2b305fc27b0",
        "19000101_000001",
        "en_US",
    )


def test_docx_to_html_preserves_structure():
    result = _docx_to_html(_docx_bytes())

    assert result == (
        "<h3>Role overview</h3>\n"
        "<p>Build detection products &amp; services.</p>\n"
        "<ul><li>Own launches</li><li>Research markets</li></ul>"
    )


@pytest.mark.asyncio
async def test_inline_description_and_structured_fields():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json=_detail(description="<p>Lead product launches and market research.</p>"),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(JOB_URL, {}, client)

    assert len(calls) == 1
    assert calls[0].url.path.endswith("/job-requisitions/9202920507783_1")
    assert content.title == "Product Marketing Manager"
    assert content.description == "<p>Lead product launches and market research.</p>"
    assert content.locations == ["Naperville, IL, US"]
    assert content.employment_type == "full_time"
    assert content.date_posted == "2026-06-09T12:18:00.000-04:00"
    assert content.base_salary == {
        "currency": "USD",
        "min": 110000,
        "max": 125000,
        "unit": "year",
    }
    assert content.metadata == {
        "requisition_id": "1041",
        "item_id": "9202920507783_1",
        "external_job_id": "594054",
        "job_class": "Professional",
    }


@pytest.mark.asyncio
async def test_attached_docx_replaces_placeholder_description():
    requests: list[httpx.Request] = []
    links = [
        {
            "targetSchema": "docx",
            "schema": "ce42b3d3-b462-4bc9-89f0-8ed89f065682",
            "payLoadArguments": [
                {
                    "argumentPath": "0034/tenant/Client/Recruitment/RecruitmentDocs/",
                    "argumentValue": "Product Marketing Manager Job Description.docx",
                }
            ],
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/documents/123"):
            return httpx.Response(200, content=_docx_bytes())
        return httpx.Response(
            200,
            json=_detail(description="<p>See attached job description</p>", links=links),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(JOB_URL, {}, client)

    assert len(requests) == 2
    attachment_request = requests[1]
    assert attachment_request.url.path.endswith("/documents/123")
    assert attachment_request.headers["filepath"] == (
        "0034/tenant/Client/Recruitment/RecruitmentDocs/ce42b3d3-b462-4bc9-89f0-8ed89f065682"
    )
    assert attachment_request.headers["isattachmenttype"] == "true"
    assert content.description
    assert "Role overview" in content.description
    assert "Own launches" in content.description
    assert "See attached" not in content.description


@pytest.mark.asyncio
async def test_unparseable_url_returns_empty_content():
    async with httpx.AsyncClient() as client:
        content = await scrape("https://example.com/jobs/123", {}, client)

    assert content.title is None
    assert content.description is None
