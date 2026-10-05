"""Freeze actual Jobylon rich fields, failures and embed request semantics."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.monitors.jobylon import discover


def page(jobs):
    return "<script>JBL.embed_v2['jobs'] = " + jobs + ";</script>"


cases = [
    (
        "rich-fields",
        page(
            "[{id:90071992547409931234,url:'/jobs/123-engineer/',title:'S"
            "enior Engineer',company_id:'123',company:'Fixture',function:"
            "'Engineering',experience:'Senior',employment_type:'Full-time"
            "',workspace:'Remote (Stockholm)',to_date:'None',published_da"
            "te:'21 april 2026',locations:[' Zurich ','',42,' Stockholm '"
            "],departments:[' Platform ',null],klass:{'job-lang-sv-first'"
            ":true,'job-lang-en':true},},]"
        ),
        {},
    ),
    ("group-precedence", page("[]"), {"company_id": "123", "company_group_id": "456"}),
    (
        "duplicate-first",
        page("[{id:1,url:'jobs/one/',title:'First'},{id:2,url:'jobs/one/',title:'Last'}]"),
        {},
    ),
    (
        "string-brackets",
        page(r"[{id:1,url:'/jobs/one/',title:'[text] ; {foo: x}',klass:{'job-lang-en':true}}]"),
        {},
    ),
    (
        "string-escapes",
        page(
            "[{id:1,url:'/jobs/one/',title:'Line\\nUnicode \\u00e4 quote \"x"
            "\"'},{id:2,url:'/jobs/two/',title:'Unsupported \\x41 and apost"
            "rophe \\' retained'}]"
        ),
        {},
    ),
    (
        "double-quotes",
        page(
            '[{"id":1,"url":"/jobs/one/","title":"A \\"quoted\\" title", "k'
            'lass":{"job-lang-da":true}}]'
        ),
        {},
    ),
    ("comma-string-quirk", page("[{id:1,url:'/jobs/one/',title:'Text, ]'}]"), {}),
    (
        "falsy-and-nonstrings",
        page(
            '[{"id":0,"url":"/jobs/no/"},{"id":1,"url":false},{"id":2,"ur'
            'l":123,"title":42,"locations":[null,42],"locations_text":" Z'
            'urich "},{"id":true,"url":"/jobs/bool/","title":""}]'
        ),
        {},
    ),
    (
        "absolute-and-fallback",
        page(
            "[{id:1,url:'https://other.example/jobs/one/',locations_text:"
            "' Helsinki ',workspace:'Hybrid',language:'Finnish',published"
            "_date:'21. huhtikuuta 2026'}]"
        ),
        {},
    ),
    (
        "language-order",
        page(
            "[{id:1,url:'/jobs/one/',klass:{'job-lang-ZZ':true,'job-lang-"
            "no-other':true,'job-lang-en':true},workspace:'Arbete distans"
            "'}]"
        ),
        {},
    ),
    (
        "months",
        page(
            json.dumps(
                [
                    {"id": i + 1, "url": f"/jobs/{i + 1}/", "published_date": date}
                    for i, date in enumerate(
                        [
                            "April 21, 2026",
                            "21. marts 2026",
                            "21 desember 2026",
                            "21 heinäkuuta 2026",
                            "29 February 2024",
                            "29 February 2025",
                            "١٢ april ٢٠٢٦",
                            "12\u00a0april\u00a02026",
                            "31 april 2026",
                            "not a date",
                        ]
                    )
                ]
            )
        ),
        {},
    ),
    ("missing-marker", "<html>Career page</html>", {}),
    ("double-quoted-marker", 'JBL.embed_v2["jobs"] = []', {}),
    ("unterminated", "JBL.embed_v2['jobs'] = [{id:1", {}),
    ("invalid-literal", page("[{id:1,url:undefined}]"), {}),
    ("empty", page("[]"), {}),
    ("nondict-item", page("[null]"), {}),
    ("bad-date", page('[{"id":1,"url":"/jobs/one/","published_date":42}]'), {}),
    (
        "bad-duplicate-date",
        page('[{"id":1,"url":"/jobs/one/"},{"id":2,"url":"/jobs/one/","published_date":42}]'),
        {},
    ),
]


async def main():
    output = []
    for name, body, metadata in cases:
        requests = []

        def respond(request, requests=requests, body=body):
            requests.append({"method": request.method, "url": str(request.url)})
            return httpx.Response(200, text=body)

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            try:
                result = await discover(
                    {
                        "board_url": "https://cdn.jobylon.com/jobs/companies/123/embed/v2/",
                        "metadata": metadata,
                    },
                    client,
                )
                jobs = getattr(result, "jobs", result)
                expected = {
                    "error": False,
                    "jobs": [asdict(x) for x in jobs],
                    "truncated": getattr(result, "truncated", False),
                }
            except Exception as exc:
                expected = {"error": True, "kind": type(exc).__name__}
        output.append(
            {
                "name": name,
                "body": body,
                "metadata": metadata,
                "requests": requests,
                "expected": expected,
            }
        )
    Path(__file__).with_name("python_jobylon.json").write_text(
        json.dumps({"cases": output}, indent=2, ensure_ascii=False) + "\n"
    )


asyncio.run(main())
