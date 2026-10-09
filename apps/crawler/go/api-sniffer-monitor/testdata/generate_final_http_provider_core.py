"""Freeze the original public-provider core before its Go migration."""

from __future__ import annotations

import copy
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.core.monitors import fenbi, nowhiring, paynet, wecruit  # noqa: E402
from src.shared.paynet import paynet_company_from_url  # noqa: E402

cases = []


def freeze(name, provider, operation, inputs, call):
    try:
        result = call()
        if is_dataclass(result):
            result = {k: v for k, v in asdict(result).items() if v is not None}
        elif isinstance(result, list):
            result = [
                {k: v for k, v in asdict(item).items() if v is not None}
                if is_dataclass(item)
                else item
                for item in result
            ]
        cases.append(
            dict(name=name, provider=provider, operation=operation, inputs=inputs, expected=result)
        )
    except (ValueError, TypeError):
        cases.append(
            dict(name=name, provider=provider, operation=operation, inputs=inputs, error=True)
        )


paynet_row = dict(
    ID="11111111-2222-3333-4444-555555555555",
    Position=dict(
        Title=" Engineer ",
        Description="Build & test\nPeople's systems",
        Requirements="<ul><li>Care</li></ul>",
        Location=" Sioux Falls, SD ",
        StartDate="2/3/2026",
        EndDate="2027-12-31",
    ),
    Company=dict(Name=" Company "),
)
for mode in (
    "rich",
    "uppercase-id",
    "invalid-id",
    "non-object",
    "missing-position",
    "missing-title",
    "text-fallback",
    "requirements-only",
    "missing-description",
    "bad-dates",
    "single-digit-date",
    "html-description",
):
    row = copy.deepcopy(paynet_row)
    if mode == "uppercase-id":
        row["ID"] = "ABCDEFAB-2222-3333-4444-555555555555"
    elif mode == "invalid-id":
        row["ID"] = "{11111111-2222-3333-4444-555555555555}"
    elif mode == "non-object":
        row = None
    elif mode == "missing-position":
        row["Position"] = []
    elif mode == "missing-title":
        row["Position"]["Title"] = " "
    elif mode == "text-fallback":
        row["Position"]["Description"] = None
        row["Position"]["Text"] = "Fallback <content>"
    elif mode == "requirements-only":
        row["Position"]["Description"] = None
    elif mode == "missing-description":
        row["Position"]["Description"] = row["Position"]["Requirements"] = None
    elif mode == "bad-dates":
        row["Position"]["StartDate"] = "2026-02-30"
        row["Position"]["EndDate"] = "not a date"
    elif mode == "single-digit-date":
        row["Position"]["StartDate"] = "2026-2-3"
    elif mode == "html-description":
        row["Position"]["Description"] = '<p>Build "systems" &amp; care</p>'
    freeze(
        mode,
        "paynet",
        "fields",
        dict(row=row, company="Example"),
        lambda row=row: paynet._parse_job(row, "Example"),
    )

for index, url in enumerate(
    (
        "https://www.pay-netonline.com/PayNet/Applicant/Postings.aspx?Co=Example",
        "https://PAY-NETONLINE.COM:443/paynet/applicant/postings.aspx?co=%20Example%20",
        "https://www.pay-netonline.com/PayNet/Applicant/Postings.aspx?Co=Example&Co=Other",
        "https://www.pay-netonline.com/PayNet/Applicant/Postings.aspx?Co=Example&filter=x",
        "https://user@www.pay-netonline.com/PayNet/Applicant/Postings.aspx?Co=Example",
        "http://www.pay-netonline.com/PayNet/Applicant/Postings.aspx?Co=Example",
        "https://www.pay-netonline.com/PayNet/Applicant/Postings.aspx?Co=x",
    )
):
    freeze(
        str(index), "paynet", "board", dict(url=url), lambda url=url: paynet_company_from_url(url)
    )

now_row = dict(
    id="job-1",
    billingAccountId="123",
    jobTitle=" Engineer ",
    jobDescription="<p>Build</p>",
    addressLine1=" Main Street ",
    addressLine2="Main Street",
    city="City",
    stateProvCode="State",
    postalCode="00001",
    categories=[None, " ", " Full-time ", "Other"],
    postedDate="2026-10-09",
    company="Employer",
    thirdPartyApplyUrl="",
    applicationURL="https://example.invalid/apply",
    brandId=0,
)
for mode in (
    "rich",
    "integer-id",
    "bool-id",
    "none-id",
    "missing-id",
    "bad-id",
    "missing-title",
    "float-account",
    "empty-account-fallback",
    "unicode-account",
    "empty-description",
    "unhashable-metadata",
):
    row = copy.deepcopy(now_row)
    if mode == "integer-id":
        row["id"] = 12
    elif mode == "bool-id":
        row["id"] = True
    elif mode == "none-id":
        row["id"] = None
    elif mode == "missing-id":
        del row["id"]
    elif mode == "bad-id":
        row["id"] = "path/escape"
    elif mode == "missing-title":
        row["jobTitle"] = " "
    elif mode == "float-account":
        row["billingAccountId"] = 1.5
    elif mode == "empty-account-fallback":
        row["billingAccountId"] = 0
    elif mode == "unicode-account":
        row["billingAccountId"] = "٢"
    elif mode == "empty-description":
        row["jobDescription"] = ""
    elif mode == "unhashable-metadata":
        row["brandId"] = {}
    freeze(
        mode,
        "nowhiring",
        "fields",
        dict(row=row, slug="example", customer="123"),
        lambda row=row: nowhiring._parse_job(row, slug="example", customer_id="123"),
    )

criteria = [
    dict(fieldName="billingAccountId", fieldValue=" 123 "),
    dict(fieldName="billingAccountId", fieldValue=456),
    dict(fieldName="brandId", fieldValue=False),
    dict(fieldName="brandTemplateId", fieldValue=0),
    dict(fieldName="ignored", fieldValue=1.5),
    dict(fieldName=None, fieldValue="x"),
    None,
]
freeze(
    "criteria",
    "nowhiring",
    "criteria",
    dict(criteria=criteria),
    lambda: nowhiring._values_by_field(criteria),
)

fenbi_row = dict(
    id=123,
    title=" 研发工程师 ",
    location=" 北京（优先）、全国、居家 ",
    description=[" 构建 & 测试 ", "支持 '团队'"],
    requirements=[" 经验 "],
    publicDateShow="2026年2月3日",
    department="研发",
)
for mode in (
    "fulltime",
    "internship",
    "parttime",
    "remote",
    "bad-title",
    "bad-date",
    "bool-id",
    "float-id",
    "duplicate",
    "empty",
    "empty-qualifications",
    "only-home",
):
    row = copy.deepcopy(fenbi_row)
    kind = "fulltime"
    if mode == "internship":
        kind, row["title"] = "parttime", "研发实习生"
    elif mode == "parttime":
        kind, row["title"] = "parttime", "兼职讲师"
    elif mode == "remote":
        row["location"] = "网络办公"
    elif mode == "bad-title":
        kind = "parttime"
    elif mode == "bad-date":
        row["publicDateShow"] = "2026年2月30日"
    elif mode == "bool-id":
        row["id"] = True
    elif mode == "float-id":
        row["id"] = 123.0
    elif mode == "empty-qualifications":
        row["requirements"] = []
    elif mode == "only-home":
        row["location"] = "居家"
    rows = [] if mode == "empty" else [row, copy.deepcopy(row)] if mode == "duplicate" else [row]
    payload = {kind: rows}
    freeze(
        mode,
        "fenbi",
        "fields",
        dict(payload=payload, kind=kind, board="https://www.fenbi.com/page/joinus"),
        lambda payload=payload, kind=kind: fenbi._parse_jobs(
            payload, kind=kind, board_url="https://www.fenbi.com/page/joinus"
        ),
    )
for index, bundle in enumerate(
    (
        'prefix;this.joinUsArr={fulltime:[{id:1,title:"Engineer",'
        "date:new Date(2026,1,3).getTime()}],parttime:[]};suffix",
        'this.joinUsArr={fulltime:[],note:"new Date(2026,1,3).getTime() } braces"}',
        'this.joinUsArr={fulltime:[],note:"escaped \\" quote"}',
        "this.joinUsArr={fulltime:[]};this.joinUsArr={parttime:[]}",
        "this.joinUsArr={fulltime:[],note:'single'}",
        "this.joinUsArr={fulltime:[],date:arbitraryCall()}",
        "this.joinUsArr={fulltime:[]",
    )
):
    freeze(
        str(index),
        "fenbi",
        "literal",
        dict(bundle=bundle),
        lambda bundle=bundle: fenbi._json_from_javascript_literal(
            fenbi._extract_inventory_literal(bundle)
        ),
    )

suite = "1234567890abcdef12345678"
listing = dict(
    postId="abcdef1234567890abcdef12", recruitType=1, postName="Fallback", publishDate="2026-10-09"
)
wecruit_detail = dict(
    postName=" Engineer ",
    workContent="Build & test\nPeople's systems",
    serviceCondition=" Learn ",
    workPlaceList=[dict(name=" 北京 "), dict(name="北京"), dict(name="上海"), None],
    publishDate="2026-10-08 12:34:56",
    endDate="2027-01-31T00:00:00",
    company="Company",
    recruitNumStr=0,
    postCode="code",
)
for mode in (
    "rich",
    "fallback-title",
    "missing-title",
    "bad-date",
    "missing-description",
    "location-fallback",
    "missing-location",
    "empty-metadata",
    "numeric-metadata",
    "unicode-lines",
    "missing-dates",
):
    row = copy.deepcopy(wecruit_detail)
    current = copy.deepcopy(listing)
    if mode == "fallback-title":
        row["postName"] = ""
    elif mode == "missing-title":
        row["postName"] = current["postName"] = ""
    elif mode == "bad-date":
        row["endDate"] = "2026-02-30"
    elif mode == "missing-description":
        row["workContent"] = row["serviceCondition"] = ""
    elif mode == "location-fallback":
        row["workPlaceList"] = []
        row["workPlaceStr"] = " 广州 "
    elif mode == "missing-location":
        row["workPlaceList"] = []
    elif mode == "empty-metadata":
        row["company"] = row["postCode"] = row["recruitNumStr"] = ""
    elif mode == "numeric-metadata":
        row["education"] = False
        row["department"] = dict(name="Department")
    elif mode == "unicode-lines":
        row["workContent"] = "一\u2028二\r\n三\u0085四\v五"
    elif mode == "missing-dates":
        row["publishDate"] = row["endDate"] = current["publishDate"] = None
    freeze(
        mode,
        "wecruit",
        "fields",
        dict(listing=current, detail=row, origin="https://wecruit.hotjob.cn", suite=suite),
        lambda current=current, row=row: wecruit._parse_job(
            wecruit._Tenant("https://wecruit.hotjob.cn", suite), current, row
        ),
    )

Path(__file__).with_name("python_final_http_provider_core.json").write_text(
    json.dumps(cases, indent=2, ensure_ascii=False) + "\n"
)
