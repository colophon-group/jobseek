"""Freeze JobStreet fields, tenant envelopes and requests from the original engines."""

from __future__ import annotations

import ast
import copy
import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import jobstreet
from src.core.scrapers import jobstreet as detail

root = Path(__file__).resolve().parents[3]
tree = ast.parse((root / "tests/test_jobstreet_monitor.py").read_text())
namespace = {
    "jobstreet": jobstreet,
    "ORGANISATION_ID": "744981",
    "COMPANY_ID": "175608148114568",
}
functions = [
    node
    for node in tree.body
    if isinstance(node, ast.FunctionDef)
    and node.name in {"_summary", "_search_payload", "_detail_payload"}
]
exec(compile(ast.Module(body=functions, type_ignores=[]), "<original-fixtures>", "exec"), namespace)


def output(value):
    return {key: child for key, child in asdict(value).items() if child is not None}


cases = []
for host in ("my.jobstreet.com", "sg.jobstreet.com"):
    base = namespace["_summary"]("12345678")
    rows = [("complete", base), ("zero", None)]
    for field, value in [
        ("id", 12345678),
        ("id", True),
        ("title", None),
        ("title", "  Role\u00a0  with  spaces \n"),
        ("salaryLabel", "$4,000 to $6,000 per month"),
        ("salaryLabel", "USD $4,000 to $6,000 per month"),
        ("salaryLabel", "RM 8,000 – RM 12,000 per month"),
        ("locations", [{"label": "Zurich"}, {"label": "Zurich"}, {"label": " "}, None]),
        ("locations", {}),
        ("locations", "bad"),
        ("workTypes", [None, "  Contract  ", "Full time"]),
        ("workArrangements", {"data": [{"label": {"text": "Hybrid"}}]}),
        ("employer", {"id": "other", "companyId": namespace["COMPANY_ID"]}),
        ("employer", {"id": namespace["ORGANISATION_ID"], "companyId": "other"}),
        ("classifications", [None, {"subClassification": {"description": "Science"}}]),
    ]:
        row = copy.deepcopy(base)
        row[field] = value
        rows.append((f"{field}-{len(rows)}", row))
    pages = [
        (name, namespace["_search_payload"]([row] if row is not None else [])) for name, row in rows
    ]
    for field, value in [
        ("totalCount", True),
        ("totalCount", 1.0),
        ("totalCount", -1),
        ("totalCount", 2),
        ("data", {}),
    ]:
        page = namespace["_search_payload"]([copy.deepcopy(base)])
        page[field] = value
        pages.append((f"envelope-{field}-{value}", page))
    page = namespace["_search_payload"]([copy.deepcopy(base), copy.deepcopy(base)])
    pages.append(("duplicate-id", page))
    for name, page in pages:
        try:
            parsed, total = jobstreet._parse_page(
                page,
                organisation_id=namespace["ORGANISATION_ID"],
                company_id=namespace["COMPANY_ID"],
                requested_page=1,
            )
            expected = [
                output(jobstreet._parse_summary(row, host=host, company_id=namespace["COMPANY_ID"]))
                for row in parsed
            ]
            error = False
        except (ValueError, TypeError, AttributeError):
            expected, total, error = None, None, True
        cases.append(
            dict(
                name=host + "-" + name,
                kind="page",
                host=host,
                page=page,
                expected=expected,
                total=total,
                error=error,
            )
        )
    base = namespace["_detail_payload"]("12345678")["data"]
    details = [("complete", base), ("missing", {"jobDetails": None})]
    for field, value in [
        ("id", "foreign"),
        ("id", 12345678),
        ("isExpired", True),
        ("isExpired", "true"),
        ("status", None),
        ("status", "Expired"),
        ("title", " "),
        ("content", " "),
        ("content", None),
        ("salary", {"label": "$4,000 to $6,000 per month"}),
    ]:
        data = copy.deepcopy(base)
        data["jobDetails"]["job"][field] = value
        details.append((f"{field}-{len(details)}", data))
    for name, data in details:
        try:
            expected = output(detail.parse_payload(data, host=host, job_id="12345678"))
            error = False
        except (ValueError, TypeError, AttributeError):
            expected, error = None, True
        cases.append(
            dict(
                name=host + "-detail-" + name,
                kind="detail",
                host=host,
                page={"data": data},
                expected=expected,
                error=error,
            )
        )

Path(__file__).with_name("python_jobstreet.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps({"original_cases": len(cases)}))
