"""Freeze existing Softgarden/UKG parser behavior for the next grouped port."""

from __future__ import annotations

import json
import runpy
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import bamboohr, recruiter_co_kr, softgarden, ukg
from src.shared.ukg import ukg_board_from_metadata, ukg_board_from_url

HERE = Path(__file__).parent
fixtures = runpy.run_path(str(HERE.parents[2] / "tests" / "test_ukg.py"))
board = fixtures["BOARD"]
row = fixtures["_row"]
out = {"softgarden": [], "ukg_routes": [], "ukg_fields": [], "ukg_pages": []}
for name, source in [
    ("ids", "var complete_job_id_list = jobs_selected = [123,456,123];"),
    ("direct", "var complete_job_id_list=[1, 2, -3, +4, 0005];"),
    ("empty", "var complete_job_id_list=[];"),
    ("missing", "var other_job_id_list=[1];"),
    ("invalid-tokens", "var complete_job_id_list=[bad, 1.5, false, null, _1, 1__2, 12_, 1_2, +3];"),
    ("unicode-digits", "var complete_job_id_list=[١٢٣,４５６];"),
    ("huge-integer", "var complete_job_id_list=[999999999999999999999999999999];"),
    ("second-marker", "var complete_job_id_list=[1];var complete_job_id_list=[2];"),
]:
    ids = softgarden._extract_job_ids(source)
    out["softgarden"].append({"name": name, "html": source, "ids": [str(v) for v in ids]})
for source in [
    board.listing_url(),
    board.listing_url() + "/",
    board.job_url(fixtures["JOB_ID"]),
    board.listing_url().replace("JobBoard", "jobboard"),
    board.listing_url().replace("https://", "http://"),
    board.listing_url() + "?filter=x",
    board.listing_url() + "#jobs",
    board.listing_url().replace("recruiting.", "recruiting2."),
    board.listing_url().replace("recruiting.ultipro.com", fixtures["MODERN_HOST"]),
    board.listing_url().replace("recruiting.ultipro.com", "wrong.ultipro.com"),
]:
    parsed = ukg_board_from_url(source)
    out["ukg_routes"].append({"url": source, "expected": asdict(parsed) if parsed else None})
for name, value in [
    ("complete", row(1)),
    ("empty", {}),
    ("bad-id", row(1, Id="bad")),
    ("missing-title", row(1, Title="  ")),
    ("part-time", row(1, FullTime=False)),
    ("unknown-employment", row(1, FullTime=1)),
    ("unescape-title", row(1, Title="  Research &amp;\nDevelopment ")),
    ("scalar-description", row(1, BriefDescription=1)),
    ("empty-description", row(1, BriefDescription="")),
    ("invalid-metadata", row(1, OpportunityType=True, RequisitionNumber={}, JobCategoryName="")),
    (
        "location-fallback",
        row(1, Locations=[{"LocalizedName": " Zürich "}, {"LocalizedDescription": "ZÜRICH"}]),
    ),
    *[
        (f"location-type-{i}", row(1, JobLocationType=v))
        for i, v in enumerate([None, 0, 1, 2, 3, True, "2", 1.5])
    ],
    *[
        (f"date-{i}", row(1, PostedDate=v))
        for i, v in enumerate(
            [None, "bad", "2026-10-06", "2026-10-06T01:02:03Z", "2026-10-06T01:02:03+09:00"]
        )
    ],
]:
    parsed = ukg._parse_job(value, board)
    expected = asdict(parsed) if parsed else None
    if expected:
        for key in ["localizations", "base_salary", "source_identity"]:
            expected.pop(key, None)
        # The Go monitor forwards raw description to the same canonical normalizer.
        expected["description"] = (
            value.get("BriefDescription")
            if isinstance(value.get("BriefDescription"), str)
            else None
        )
    out["ukg_fields"].append({"name": name, "raw": value, "expected": expected})
for name, payload in [
    ("complete", fixtures["_payload"]([row(1)])),
    ("empty", fixtures["_payload"]([])),
    ("missing-total", {"opportunities": []}),
    ("bool-total", {"totalCount": True, "opportunities": []}),
    ("negative-total", {"totalCount": -1, "opportunities": []}),
    ("fraction-total", {"totalCount": 1.5, "opportunities": []}),
    ("missing-rows", {"totalCount": 1}),
]:
    try:
        total, rows = ukg._page_rows(payload, board)
        expected = {"total": total, "rows": rows}
    except ValueError:
        expected = None
    out["ukg_pages"].append({"name": name, "raw": payload, "expected": expected})
bamboo_fixture = runpy.run_path(str(HERE.parents[2] / "tests" / "test_bamboohr.py"))
base = bamboo_fixture["JOBS"][0]
out["bamboo_fields"] = []
for name, value in [
    ("complete", base),
    (
        "fallback-city",
        {
            **base,
            "location": {"city": "Zurich", "state": "Zurich", "addressCountry": "Switzerland"},
        },
    ),
    ("scalar-location", {**base, "location": "  Paris  "}),
    ("scalar-ats-location", {**base, "atsLocation": "  Tokyo  "}),
    ("empty-location", {**base, "location": "", "atsLocation": "Ignored"}),
    ("bool-id", {**base, "id": True}),
    ("fraction-id", {**base, "id": 1.5}),
    ("negative-id", {**base, "id": -1}),
    ("arabic-id", {**base, "id": "٣٤٧"}),
    ("empty-id", {**base, "id": ""}),
    ("metadata-coercion", {**base, "departmentLabel": {"name": "Sales"}, "departmentId": False}),
    *[
        (f"location-type-{i}", {**base, "locationType": value, "isRemote": True})
        for i, value in enumerate([None, 0, 1, 2, True, "0", "1", "2", "other"])
    ],
]:
    parsed = bamboohr._parse_job(value, "acme")
    expected = asdict(parsed) if parsed else None
    if expected:
        for key in ["localizations", "base_salary", "source_identity"]:
            expected.pop(key, None)
    out["bamboo_fields"].append({"name": name, "raw": value, "expected": expected})
out["bamboo_listing"] = []
for name, value in [
    ("complete", bamboo_fixture["_payload"](bamboo_fixture["JOBS"])),
    ("empty", bamboo_fixture["_payload"]([])),
    ("duplicate", bamboo_fixture["_payload"]([base, base])),
    ("invalid-prefix", bamboo_fixture["_payload"]([True, base])),
    ("all-invalid", bamboo_fixture["_payload"]([True, {}])),
    ("incomplete", bamboo_fixture["_payload"]([base], total=10)),
    ("count-short", bamboo_fixture["_payload"]([base], total=0)),
    ("bool-count", {"result": [], "meta": {"totalCount": True}}),
    ("missing-count", {"result": [], "meta": {}}),
    ("missing-rows", {"result": None, "meta": {"totalCount": 0}}),
]:
    try:
        jobs, truncated = bamboohr._parse_listing(value, "acme")
        expected = {"jobs": [], "truncated": truncated}
        for job in jobs:
            fields = asdict(job)
            for key in ["localizations", "base_salary", "source_identity"]:
                fields.pop(key, None)
            expected["jobs"].append(fields)
    except ValueError:
        expected = None
    out["bamboo_listing"].append({"name": name, "raw": value, "expected": expected})
out["ukg_metadata"] = []
for metadata in [
    {},
    {"host": board.host, "tenant": board.tenant, "board_id": board.board_id},
    {
        "host": board.host.upper() + ".",
        "tenant": " " + board.tenant + " ",
        "board_id": board.board_id.upper(),
    },
    {"listing_url": board.listing_url()},
    {
        "listing_url": board.listing_url().replace(board.host, fixtures["MODERN_HOST"]),
        "host": board.host,
        "tenant": board.tenant,
        "board_id": board.board_id,
    },
    {"listing_url": "bad", "host": board.host, "tenant": board.tenant, "board_id": board.board_id},
    {"host": "bad", "tenant": "x", "board_id": "bad"},
]:
    parsed = ukg_board_from_metadata(metadata) or ukg_board_from_url(board.listing_url())
    out["ukg_metadata"].append(
        {
            "url": board.listing_url(),
            "metadata": metadata,
            "expected": asdict(parsed) if parsed else None,
        }
    )
kr_fixture = runpy.run_path(str(HERE.parents[2] / "tests" / "test_recruiter_co_kr.py"))[
    "TestParseDetail"
]()
summary = kr_fixture._summary()
out["recruiter_fields"] = []
for name, detail in [
    (
        "complete",
        {
            "title": "연구 개발자",
            "jobDescription": "<p>Build services.</p>",
            "careerType": "CAREER",
            "startDateTime": "2026-04-22T00:00:00",
            "classificationCode": "HQ",
            "tagList": [{"tagName": "Engineering"}],
            "regionName": "서울",
            "endDateTime": "2026-05-22T00:00:00",
        },
    ),
    ("summary-only", {}),
    ("text-description", {"jobDescriptionType": "TEXT", "jobDescription": "<p>Raw text</p>"}),
    ("number-description", {"jobDescriptionType": "TEXT", "jobDescription": 123}),
    ("list-description", {"jobDescriptionType": "TEXT", "jobDescription": ["one", "two"]}),
    ("map-description", {"jobDescriptionType": "TEXT", "jobDescription": {"b": 1, "a": 2}}),
    (
        "region-list",
        {"regionList": [{"regionName": "서울"}, {"name": " 부산 "}], "siteList": ["서울", "대전"]},
    ),
    (
        "table-location",
        {"jobDescription": "<table><tr><td>근무 지역</td><td>서울 본사</td></tr></table>"},
    ),
    (
        "table-joined-location",
        {
            "jobDescription": (
                "<table><tr><td><b>근무</b><i>지역</i></td>"
                "<td><b>서울</b><span>본사</span></td></tr></table>"
            )
        },
    ),
    (
        "table-duplicate-location",
        {
            "regionName": "서울",
            "jobDescription": "<table><tr><td>근무지</td><td>서울</td></tr></table>",
        },
    ),
    ("tag-type-guard", {"tagList": {"tagName": "ignore"}}),
    (
        "structured-metadata",
        {
            "announcementType": "OPEN",
            "recruitmentType": "REGULAR",
            "tagList": [{"tagName": 1}, None, False],
        },
    ),
]:
    parsed = recruiter_co_kr._parse_detail(detail, summary, "fixture")
    expected = asdict(parsed) if parsed else None
    if expected:
        for key in ["localizations", "base_salary", "source_identity"]:
            expected.pop(key, None)
    out["recruiter_fields"].append(
        {"name": name, "detail": detail, "summary": summary, "expected": expected}
    )
out["recruiter_dates"] = []
for value in [
    None,
    "",
    123,
    "2026-04-22",
    "2026-04-22T00:00:00",
    "2026-04-22T12:00:00",
    "2026-04-22T00:00:00+09:00",
    "2026-04-22T00:00+09:00",
    "2026-04-22T00:00:00Z",
    "badTvalue",
    "Tvalue",
    "2026-04-22 00:00:00",
]:
    out["recruiter_dates"].append({"value": value, "expected": recruiter_co_kr._dt_date(value)})
(HERE / "python_secondary_api_providers.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps({k: len(v) for k, v in out.items()}))
