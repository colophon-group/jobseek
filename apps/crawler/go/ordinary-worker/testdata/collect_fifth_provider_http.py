"""Freeze actual fifth-batch monitor and detail HTTP behavior."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import zipfile
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx
from collect_fourth_provider_http import no_wait, response

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, adp, cornerstone, paylocity
from src.core.scrapers import adp as adp_detail
from src.core.scrapers import paylocity as paylocity_detail
from src.shared.adp import AdpBoard
from src.shared.cornerstone import CornerstoneBoard
from src.shared.html_normalize import normalize_description_html

HERE = Path(__file__).parent
ADP = AdpBoard("01234567-89ab-cdef-0123-456789abcdef", "19000101_000001", "en_US")
CSOD = CornerstoneBoard("fixture", 4, "fixture")
PAY = "https://2000recruiting.paylocity.com/Recruiting/Jobs/All/fixture"
SEARCH = "https://eu-fra.api.csod.com/rec-job-search/external/jobs"
TOKEN = ".".join(["publicfixture" * 3] * 3)
BOOT = (
    "<script>csod.context="
    + json.dumps(
        {
            "corp": "fixture",
            "token": TOKEN,
            "cultureID": 1,
            "cultureName": "en-US",
            "endpoints": {"cloud": "https://eu-fra.api.csod.com/"},
        }
    )
    + ";</script>"
)
HEADERS = {
    "authorization",
    "csod-accept-language",
    "locale",
    "x-requested-with",
    "x-forwarded-host",
    "filepath",
    "isabsolutepath",
    "isattachmenttype",
}


async def collect(provider, name, source, pages, metadata=None, detail=False):
    requests, counts = [], {}
    expected = {
        "error": False,
        "gone": False,
        "truncated": False,
        "jobs": [],
        "urls": [],
        "content": None,
        "empty": False,
    }

    def transport(request):
        key = str(request.url)
        raw = pages[key]
        i = counts.get(key, 0)
        counts[key] = i + 1
        if isinstance(raw, list):
            raw = raw[min(i, len(raw) - 1)]
        requests.append(
            {
                "method": request.method,
                "url": key,
                "body": json.loads(request.content) if request.content else None,
                "headers": {k: v for k, v in request.headers.items() if k in HEADERS},
            }
        )
        body = (
            base64.b64decode(raw["body_base64"]) if "body_base64" in raw else raw["body"].encode()
        )
        return httpx.Response(
            raw["status"],
            content=body,
            headers={"Content-Type": "text/html; charset=utf-8", **raw["headers"]},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        try:
            if detail:
                content = await {"adp": adp_detail, "paylocity": paylocity_detail}[provider].scrape(
                    source, metadata or {}, client
                )
                expected["content"] = asdict(content)
                expected["canonical_description"] = (
                    normalize_description_html(content.description) if content.description else None
                )
                expected["empty"] = all(v is None for v in expected["content"].values())
            else:
                result = await {"adp": adp, "cornerstone": cornerstone, "paylocity": paylocity}[
                    provider
                ].discover({"board_url": source, "metadata": metadata or {}}, client)
                if isinstance(result, MonitorResult):
                    expected["truncated"] = result.truncated
                    jobs = list((result.jobs_by_url or {}).values())
                else:
                    jobs = result
                expected["urls"] = sorted({job.url for job in jobs})
                for job in jobs:
                    row = asdict(job)
                    if row["description"]:
                        row["description"] = normalize_description_html(row["description"])
                    expected["jobs"].append(row)
                expected["jobs"].sort(key=lambda row: row["url"])
        except BoardGoneError:
            expected["gone"] = True
        except Exception:
            expected["error"] = True
    return dict(
        provider=provider,
        name=name,
        source=source,
        pages=pages,
        metadata=metadata or {},
        detail=detail,
        requests=requests,
        expected=expected,
    )


def adp_page(rows, total=None, start=1):
    return {
        "jobRequisitions": rows,
        "meta": {"totalNumber": len(rows) if total is None else total, "startSequence": start},
    }


def csod_page(rows, total=None):
    return {
        "status": "Success",
        "data": {"totalCount": len(rows) if total is None else total, "requisitions": rows},
    }


async def main():
    cases = []
    ar = {
        "itemID": "123_1",
        "requisitionTitle": "Engineer",
        "requisitionLocations": [{"nameCode": {"shortName": "Zurich"}}],
        "workLevelCode": {"shortName": "Full Time"},
    }
    for name, value in [
        ("complete", adp_page([ar])),
        ("empty", adp_page([])),
        ("bad-page", {"meta": {}}),
        ("bad-count", adp_page([ar], total=True)),
        ("wrong-start", adp_page([ar], start=2)),
        ("invalid-row", adp_page([{**ar, "itemID": None}])),
    ]:
        cases.append(
            await collect("adp", name, ADP.listing_url(), {ADP.search_url(): response(value)})
        )
    for status in [201, 404, 410, 503]:
        cases.append(
            await collect(
                "adp",
                str(status),
                ADP.listing_url(),
                {ADP.search_url(): response(adp_page([ar]), status)},
            )
        )
    first = [{**ar, "itemID": f"{i}_1"} for i in range(20)]
    for name, total, rows in [
        ("two-pages", 21, [{**ar, "itemID": "20_1"}]),
        ("count-increase", 22, [{**ar, "itemID": "20_1"}, {**ar, "itemID": "21_1"}]),
        ("count-decrease", 20, []),
    ]:
        cases.append(
            await collect(
                "adp",
                name,
                ADP.listing_url(),
                {
                    ADP.search_url(): response(adp_page(first, total=21)),
                    ADP.search_url(start=21): response(adp_page(rows, total=total, start=21)),
                },
            )
        )
    cr = {
        "requisitionId": 1,
        "displayJobTitle": "Engineer",
        "externalDescription": "<p>Build</p>",
        "postingEffectiveDate": "10/06/2026",
    }
    for name, value in [
        ("complete", csod_page([cr])),
        ("empty", csod_page([])),
        ("invalid-row", csod_page([{**cr, "requisitionId": None}])),
        ("bad-count", csod_page([cr], total=True)),
        ("missing-data", {}),
        ("duplicates", csod_page([cr, cr])),
    ]:
        cases.append(
            await collect(
                "cornerstone",
                name,
                CSOD.listing_url(),
                {CSOD.listing_url(): response(BOOT), SEARCH: response(value)},
            )
        )
    for name, boot in [
        ("context-missing", "<html></html>"),
        ("context-malformed", "csod.context={"),
        ("bootstrap-404", response("", 404)),
    ]:
        cases.append(
            await collect(
                "cornerstone",
                name,
                CSOD.listing_url(),
                {CSOD.listing_url(): boot if isinstance(boot, dict) else response(boot)},
            )
        )
    cases.append(
        await collect(
            "cornerstone",
            "auth-refresh",
            CSOD.listing_url(),
            {
                CSOD.listing_url(): response(BOOT),
                SEARCH: [response({}, 401), response(csod_page([cr]))],
            },
        )
    )
    pr = {"JobId": 1, "JobTitle": "Engineer", "LocationName": "Remote US"}
    for name, page in [
        ("complete", "window.pageData=" + json.dumps({"Jobs": [pr]}) + ";"),
        ("empty", 'window.pageData={"Jobs":[]};'),
        ("bad-json", "window.pageData={"),
        ("missing", "<html></html>"),
        ("empty-body", ""),
    ]:
        cases.append(await collect("paylocity", name, PAY, {PAY: response(page)}))
    for status in [403, 404, 503]:
        cases.append(await collect("paylocity", str(status), PAY, {PAY: response("", status)}))
    url = ADP.job_url("123_1")
    parsed = adp_detail._parse_job_url(url)
    base, job_id, cid, cc, locale = parsed
    endpoint = str(
        httpx.URL(
            base + adp_detail._DETAIL_PATH.format(job_id=job_id),
            params={"cid": cid, "ccId": cc, "lang": locale, "locale": locale},
        )
    )
    document = str(
        httpx.URL(base + adp_detail._DOCUMENT_PATH, params={"cid": cid, "ccId": cc, "lang": locale})
    )
    row = {
        **ar,
        "requisitionDescription": "<p>Build &amp; ship</p>",
        "postDate": "2026-10-06",
        "payGradeRange": {"minimumRate": {"amountValue": 100000, "currencyCode": "USD"}},
        "customFieldGroup": {
            "codeFields": [{"nameCode": {"codeValue": "SalaryType"}, "shortName": "annual"}]
        },
    }
    for name, value, config in [
        ("complete", row, {}),
        (
            "title-location",
            {**row, "requisitionTitle": "Engineer - Zurich", "requisitionLocations": []},
            {"title_location_pattern": r"\s+-\s+(?P<location>.+)$"},
        ),
        ("empty", {}, {}),
        ("bad-json", "{", {}),
    ]:
        cases.append(
            await collect("adp", name, url, {endpoint: response(value)}, config, detail=True)
        )
    xml = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingm'
        'l/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><'
        "w:r><w:t>Role &amp; duties</w:t></w:r></w:p><w:p><w:pPr><w:numPr/></w:"
        "pPr><w:r><w:t>Build systems</w:t><w:br/><w:t>Ship them</w:t></w:r></w:"
        "p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>CHF 100</w:t></w:r></w:p></w:tc></"
        "w:tr></w:tbl></w:body></w:document>"
    )
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        archive.writestr(zipfile.ZipInfo("word/document.xml", (2026, 10, 6, 0, 0, 0)), xml)
    attachment_row = {
        **row,
        "requisitionDescription": "See the attached job description",
        "links": [
            {
                "targetSchema": "docx",
                "schema": "role.docx",
                "payLoadArguments": [{"argumentPath": "public/path"}],
            }
        ],
    }
    cases.append(
        await collect(
            "adp",
            "docx",
            url,
            {
                endpoint: response(attachment_row),
                document: {
                    "status": 200,
                    "body_base64": base64.b64encode(blob.getvalue()).decode(),
                    "headers": {},
                },
            },
            detail=True,
        )
    )
    for status in [404, 503]:
        cases.append(
            await collect(
                "adp",
                f"attachment-{status}",
                url,
                {endpoint: response(attachment_row), document: response("", status)},
                detail=True,
            )
        )
    cases.append(
        await collect(
            "adp",
            "declared-detail-too-large",
            url,
            {endpoint: response(row, headers={"Content-Length": str(3 * 1024 * 1024)})},
            detail=True,
        )
    )
    cases.append(
        await collect(
            "adp",
            "declared-document-too-large",
            url,
            {
                endpoint: response(attachment_row),
                document: response("small", headers={"Content-Length": str(11 * 1024 * 1024)}),
            },
            detail=True,
        )
    )
    pu = "https://2000recruiting.paylocity.com/Recruiting/Jobs/Details/1"
    for name, page in [
        (
            "complete",
            (
                '<div class="job-preview-title"><span>Engineer</span></div><div cl'
                'ass="job-listing-header">Description</div><div><p>Build</p></div>'
            ),
        ),
        ("empty", ""),
    ]:
        cases.append(await collect("paylocity", name, pu, {pu: response(page)}, detail=True))
    cases.append(await collect("paylocity", "404", pu, {pu: response("", 404)}, detail=True))
    (HERE / "python_fifth_provider_http.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    print("actual Python HTTP cases", len(cases))


if __name__ == "__main__":
    with patch("asyncio.sleep", no_wait):
        asyncio.run(main())
