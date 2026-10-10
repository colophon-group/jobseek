"""Freeze original Accenture fields, requests and bounded inventory oracles."""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import html
import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import accenture, brassring
from src.shared.api_sniff import _get_multipart_param, set_body_param
from src.shared.html_normalize import normalize_description_html

root = Path(__file__).resolve().parents[3]
tree = ast.parse((root / "tests/test_brassring.py").read_text())
namespace = {}
exec(
    compile(
        ast.Module(
            body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_row"],
            type_ignores=[],
        ),
        "<original-BrassRing-fixture>",
        "exec",
    ),
    namespace,
)
row = namespace["_row"]
out = {"fields": [], "identities": [], "normalizations": {"": None}, "requests": []}


def fields(j):
    return {k: v for k, v in asdict(j).items() if v is not None} if j else None


for endpoint in [accenture.FINDJOBS, accenture.JOBSEARCH]:
    base = {
        "guid": "abc-123",
        "jobDetailUrl": "/fr-fr/careers/jobdetails?id=abc-123",
        "title": "Engineer",
        "jobDescription": "<p>Build</p>",
        "location": ["Berlin", "Munich"],
        "jobCityState": "Paris",
        "remoteType": "Remote",
        "postedDate": "2026-10-01",
        "businessArea": "Technology",
        "careerLevel": "Senior",
    }
    cases = [("complete", base), ("empty", {})]
    for key, value in [
        ("guid", None),
        ("guid", False),
        ("guid", 17),
        ("guid", 1.0),
        ("title", None),
        ("title", 17),
        ("jobDescription", None),
        ("remoteType", None),
        ("postedDate", None),
        ("location", None),
        ("location", []),
        ("location", "  New York  "),
        ("jobCityState", ["Paris", "Lyon"]),
        ("jobCityState", None),
        ("jobDetailUrl", None),
        ("jobDetailUrl", "https://www.accenture.com/fr-fr/careers/jobdetails?id=1"),
        ("businessArea", {}),
        ("careerLevel", True),
    ]:
        v = copy.deepcopy(base)
        v[key] = value
        cases.append((key + "-" + str(len(cases)), v))
    for name, value in cases:
        j = (
            accenture._parse_findjobs_job(value, "us-en")
            if endpoint == accenture.FINDJOBS
            else accenture._parse_jobsearch_job(value)
        )
        out["fields"].append(
            dict(provider="accenture", endpoint=endpoint, name=name, row=value, expected=fields(j))
        )
for changes in [
    {},
    {"reqid": ""},
    {"reqid": 17},
    {"reqid": "bad"},
    {"jobtitle": ""},
    {"jobtitle": "  Engineer &amp; Support  "},
    {"jobdescription": None},
    {"jobdescription": "", "formtext3": "<p>Fallback</p>"},
    {"lastupdated": "bad"},
    {"lastupdated": "29-Feb-2024"},
    {"lastupdated": "29-Feb-2025"},
    {"department": ""},
    {"formtext8": "Zurich", "formtext9": "ZURICH", "formtext10": "Switzerland"},
    {"formtext8": "", "formtext9": "", "location": "  London "},
    {"formtext8": "", "formtext9": ""},
]:
    value = row(**changes)
    j = brassring._parse_job(value, "25416", "5998")
    q = brassring._questions(value["Questions"])
    raw = q.get("jobdescription") or q.get("formtext3")
    raw = raw if isinstance(raw, str) else ""
    out["normalizations"][raw] = normalize_description_html(raw)
    out["fields"].append(
        dict(provider="brassring", name=str(len(out["fields"])), row=value, expected=fields(j))
    )
for u in [
    row()["Link"],
    "https://jobs.example.com/TGnewUI/Search/Home?partnerId=1&siteId=2",
    "https://sjobs.brassring.com/jobs?partnerid=1&siteid=2",
    "https://sjobs.brassring.com/TGnewUI/Search/Home?partnerid=x&siteid=2",
]:
    value = brassring._board_ids(u)
    out["identities"].append(dict(url=u, expected=list(value) if value else None))
for country, lang, site in [("USA", "en", "us-en"), ("日本", "ja", "jp-ja")]:
    out["requests"].append(
        dict(
            country=country,
            language=lang,
            site=site,
            offset=500,
            body=accenture._build_body(500, country, lang, site),
        )
    )
namespace = {"json": json, "html": html}
exec(
    compile(
        ast.Module(
            body=[
                n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_detail_body"
            ],
            type_ignores=[],
        ),
        "<original-BrassRing-detail-fixture>",
        "exec",
    ),
    namespace,
)
out["details"] = []
for body in [namespace["_detail_body"](), namespace["_detail_body"]("999"), "<p>Missing</p>"]:
    try:
        expected = brassring._detail_location(body, "3383626")
    except ValueError:
        expected = None
    out["details"].append(dict(body=body, expected=expected))
out["captured_requests"] = []
for body in [
    json.dumps({"startIndex": 0, "maxResultSize": 20, "keyword": "untouched"}),
    "startIndex=0&maxResultSize=20&keyword=untouched",
    accenture._build_body(0, "France", "fr", "fr-fr"),
]:
    expected = set_body_param(set_body_param(body, "startIndex", 1000), "maxResultSize", 500)
    out["captured_requests"].append(dict(body=body, expected=expected))
out["inventories"] = []


async def freeze_inventories():
    for endpoint in [accenture.FINDJOBS, accenture.JOBSEARCH]:
        for actual, advertised in [(0, 0), (501, 501), (1500, 1500), (1200, 10000), (50000, 10000)]:
            offsets = []

            async def fetch(
                method, url, headers, body, rows_actual=actual, page_total=advertised, calls=offsets
            ):
                if body.startswith("----"):
                    offset = int(_get_multipart_param(body, "startIndex"))
                else:
                    offset = json.loads(body)["startIndex"]
                calls.append(offset)
                return {
                    "totalHits": {"total": page_total},
                    "data": [
                        {
                            "guid": str(i),
                            "jobDetailUrl": "https://www.accenture.com/fr-fr/careers/jobdetails?id="
                            + str(i),
                            "title": "Engineer",
                            "jobDescription": "Build",
                            "location": "Zurich",
                            "jobCityState": "Paris",
                        }
                        for i in range(offset + 1, min(offset + 500, rows_actual) + 1)
                    ],
                }

            if endpoint == accenture.FINDJOBS:
                rows, capped = await accenture._paginate(fetch, "USA", "en", "us-en")
            else:
                rows = await accenture._paginate_jobsearch(
                    fetch, json.dumps({"startIndex": 0, "maxResultSize": 20}), "application/json"
                )
                capped = len(rows) >= 50000
            ids = [r["guid"] for r in rows]
            out["inventories"].append(
                dict(
                    endpoint=endpoint,
                    actual=actual,
                    advertised=advertised,
                    rows=len(rows),
                    capped=capped,
                    offsets=sorted(offsets),
                    ids_sha256=hashlib.sha256("\n".join(ids).encode()).hexdigest(),
                )
            )


asyncio.run(freeze_inventories())
out["fields"] = [x for x in out["fields"] if x["provider"] == "accenture"]
for key in ("identities", "normalizations", "details"):
    out.pop(key, None)
Path(__file__).with_name("python_accenture.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps({k: len(v) for k, v in out.items()}))
