"""Freeze the original browser-provider identities, fields and page requests."""

from __future__ import annotations

import ast
import copy
import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import bytedance, darwinbox
from src.shared.darwinbox import DarwinboxBoard, darwinbox_board_from_url
from src.shared.html_normalize import normalize_description_html

root = Path(__file__).resolve().parents[3]


def fixture(filename, name, namespace):
    tree = ast.parse((root / "tests" / filename).read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "<original-fixtures>", "exec"), namespace)
    return namespace[name]


row = fixture("test_darwinbox.py", "_row", {})
item = fixture("test_bytedance_monitor.py", "_item", {})
out = {"fields": [], "identities": [], "requests": [], "normalizations": {}}
board = DarwinboxBoard("airtel.darwinbox.in")


def fields(job):
    return {key: value for key, value in asdict(job).items() if value is not None}


base = row(1)
cases = [("complete", base), ("non-object", None)]
for key, value in [
    ("id", 17),
    ("id", True),
    ("id", 1.0),
    ("id", "bad.id"),
    ("id", "  JOB_x-9  "),
    ("title", "  Data\u00a0 Engineer \n"),
    ("title", None),
    ("title", ""),
    ("jd", None),
    ("jd", 17),
    ("posted_on", "1700000000000"),
    ("posted_on", 1700000000000),
    ("posted_on", True),
    ("posted_on", -1700000000),
    ("posted_on", 9999999999999999),
    ("posted_on", "bad"),
    ("created_on", "2024-03-06T08:00:00+05:30"),
    ("tool_tip_locations", ["Pune", "PUNE", None, "  Zurich  ", ""]),
    ("tool_tip_locations", [None]),
    ("tool_tip_locations", []),
    ("is_remote", " yes "),
    ("is_remote", 1),
    ("is_remote", 1.0),
    ("is_remote", "false"),
    ("emp_type_name", "  Full   time "),
    ("internal_job_code", "Code 17"),
    ("department_name_only", "Engineering"),
    ("experience", "3 years"),
    ("salary_range", "INR 10-20L"),
]:
    value_row = copy.deepcopy(base)
    value_row[key] = value
    if key == "created_on":
        value_row["posted_on"] = None
    if key == "tool_tip_locations" and not value:
        value_row["officelocations_without_area"] = ["  Singapore "]
    cases.append((key + "-" + str(len(cases)), value_row))
value_row = row(2, title=None, designation_display_name="  Alternative Role  ")
cases.append(("alternate-title", value_row))
value_row = row(3, tool_tip_locations=[], locations="Multiple locations")
cases.append(("multiple-locations", value_row))
for name, value in cases:
    job = darwinbox._parse_job(value, board)
    raw = value.get("jd") if isinstance(value, dict) else None
    if isinstance(raw, str):
        out["normalizations"][raw] = normalize_description_html(raw)
    out["fields"].append(
        dict(provider="darwinbox", name=name, row=value, expected=fields(job) if job else None)
    )

for portal in (bytedance._GLOBAL, bytedance._EXPERIENCED, bytedance._CAMPUS):
    base = item("123")
    cases = [("complete", base)]
    for key, value in [
        ("id", 123),
        ("title", None),
        ("description", None),
        ("requirement", None),
        ("city_info", None),
        ("city_info", {"en_name": "Zurich", "parent": {"en_name": "Zurich"}}),
        ("recruit_type", {"name": "Contract"}),
        ("job_category", None),
        ("create_time", None),
        ("create_time", "2024-01-03"),
        ("create_time", True),
    ]:
        value_row = copy.deepcopy(base)
        value_row[key] = value
        if key == "job_category":
            value_row["job_type"] = {"name": "Science"}
        cases.append((key + "-" + str(len(cases)), value_row))
    for name, value in cases:
        out["fields"].append(
            dict(
                provider="bytedance",
                portal=portal.website_path,
                name=name,
                row=value,
                expected=fields(bytedance._to_job(value, portal)),
            )
        )
    out["requests"].append(
        dict(
            provider="bytedance",
            portal=portal.website_path,
            body=json.loads(bytedance._body(portal, offset=2000, category_ids=["rd"])),
            headers=bytedance._headers(portal),
        )
    )
out["requests"].append(
    dict(
        provider="darwinbox",
        body=json.loads(darwinbox._request_body(board, 2)),
        headers=darwinbox._REQUEST_HEADERS,
    )
)

for raw in [
    "https://airtel.darwinbox.in/ms/candidate/careers",
    board.listing_url(),
    board.job_url("job1"),
    "https://acme.darwinbox.com/ms/candidate/graduate/careers",
    "http://airtel.darwinbox.in/ms/candidate/careers",
    "https://api.darwinbox.in/ms/candidate/careers",
    "https://airtel.darwinbox.in.evil.test/ms/candidate/careers",
    board.listing_url() + "?country=in",
    board.listing_url() + "/jobDetails/bad.id",
]:
    value = darwinbox_board_from_url(raw)
    out["identities"].append(dict(url=raw, expected=asdict(value) if value else None))
path = Path(__file__).with_name("python_darwinbox_bytedance.json")
path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
print(
    json.dumps(
        {
            "fields": len(out["fields"]),
            "identities": len(out["identities"]),
            "requests": len(out["requests"]),
        }
    )
)
