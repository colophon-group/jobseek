"""Freeze configured JOIN detail and publisher metadata output, without HTTP."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import httpx

from src.core.scrapers.nextdata import parse_html
from src.shared.tdm import TDMReservedError, check_response

BASE = {
    "path": "props.pageProps.initialState.job",
    "fields": {
        "title": "title",
        "locations": "city.cityName",
        "date_posted": "createdAt",
        "description": "description",
        "employment_type": "employmentType.googleType",
        "job_location_type": "workplaceType",
    },
}
CONFIGS = [
    BASE,
    {**BASE, "fields": {**BASE["fields"], "description": "schemaDescription"}},
    {
        "path": BASE["path"],
        "fields": {
            "title": "title",
            "date_posted": "createdAt",
            "description": "schemaDescription",
            "locations": ["=Switzerland"],
        },
    },
    {
        **BASE,
        "fields": {
            **BASE["fields"],
            "employment_type": "employmentType.name",
            "description": "schemaDescription || unifiedDescription || description",
        },
    },
]
JOB = {
    "title": "Engineer Zürich",
    "city": {"cityName": "Zürich"},
    "createdAt": "2026-09-28",
    "description": "<p>HTTP &amp; Go</p>",
    "schemaDescription": "<p>Schema</p>",
    "unifiedDescription": "Unified",
    "employmentType": {"googleType": "FULL_TIME", "name": "Full time"},
    "workplaceType": "remote",
}


def page(job):
    return (
        '<script id="__NEXT_DATA__">'
        + json.dumps({"props": {"pageProps": {"initialState": {"job": job}}}}, ensure_ascii=False)
        + "</script>"
    )


cases = []


def add(name, html, config=BASE, *, prefix=0, fill="x"):
    body = fill * prefix + html
    cases.append(
        dict(
            name=name,
            html=html,
            config=config,
            prefix=prefix,
            fill=fill,
            expected=dataclasses.asdict(parse_html(body, config)),
        )
    )


for i, config in enumerate(CONFIGS):
    add(f"configuration-{i}", page(JOB), config)
for i, value in enumerate(
    [
        None,
        "",
        [],
        {},
        0,
        -0.0,
        1.0,
        1e-5,
        1e16,
        9007199254740993,
        False,
        True,
        [None, " a ", False, 0, {"z": "é", "a": [1, None]}],
        {"z": "one's", "a": "\n\t\x00", "b": "\u00a0\u200b😀"},
    ]
):
    job = {
        **JOB,
        "title": value,
        "description": value,
        "createdAt": value,
        "city": {"cityName": value},
        "employmentType": {"googleType": value},
        "workplaceType": value,
    }
    add(f"value-{i}", page(job))
for i, value in enumerate([None, False, "", [], {}, 0]):
    add(f"or-truthiness-{i}", page({**JOB, "schemaDescription": value}), CONFIGS[3])
body = page(JOB)
add(
    "attribute-order-and-case",
    body.replace(
        '<script id="__NEXT_DATA__">', '<SCRIPT TYPE="application/json" ID="__NEXT_DATA__">'
    ),
)
add("entity-id", body.replace("__NEXT_DATA__", "__NEXT&#95;DATA__"))
add(
    "duplicate-attrs-last-match",
    body.replace('id="__NEXT_DATA__"', 'id="wrong" id="__NEXT_DATA__"'),
)
add(
    "duplicate-attrs-last-wrong",
    body.replace('id="__NEXT_DATA__"', 'id="__NEXT_DATA__" id="wrong"'),
)
add("last-script", body + page({**JOB, "title": "Last"}))
add("last-script-malformed", body + '<script id="__NEXT_DATA__">{bad}</script>')
add("empty-last-script", body + '<script id="__NEXT_DATA__"></script>')
add("missing-close", body.replace("</script>", ""))
add("missing-script", "<html></html>")
add("malformed-json", '<script id="__NEXT_DATA__">{bad}</script>')
add("trailing-comma", body.replace("}}}}", ",}}}}"))
add(
    "duplicate-json-keys",
    body.replace('"title": "Engineer Zürich"', '"title": "first", "title": "last"'),
)
add("missing-job", '<script id="__NEXT_DATA__">{}</script>')
add("no-fields", body, {})
add("full-html-beyond-monitor-prefix", body, prefix=4_001_000)
add("unicode-full-html", body, prefix=4_001_000, fill="é")
Path(__file__).with_name("python_details.json").write_text(
    json.dumps(cases, indent=2, ensure_ascii=False) + "\n"
)

policies = []


def policy(name, html, headers=None, *, prefix=0, fill="x"):
    resp = httpx.Response(
        200,
        headers=headers or {},
        request=httpx.Request("GET", "https://join.com/companies/acme/1"),
    )
    expected = dict(reserved=False, source="", policy="")
    try:
        check_response(resp)  # The native transport rejects header reservation immediately too.
        check_response(resp, body_excerpt=fill * prefix + html)
    except TDMReservedError as exc:
        expected = dict(reserved=True, source=exc.source, policy=exc.policy_url or "")
    policies.append(
        dict(
            name=name, html=html, headers=headers or {}, prefix=prefix, fill=fill, expected=expected
        )
    )


meta = '<meta name="tdm-reservation" content="1">'
policy("header", "", {"tdm-reservation": "1", "tdm-policy": "https://policy/header"})
policy(
    "header-before-later-opt-in",
    '<meta name="tdm-reservation" content="0">',
    {"tdm-reservation": "1"},
)
policy("meta", meta)
policy(
    "meta-policy-entities", meta + '<meta name="tdm-policy" content="https://policy/?a=1&amp;b=2">'
)
policy(
    "meta-over-header-zero", meta, {"tdm-reservation": "0", "tdm-policy": "https://policy/header"}
)
policy("last-zero", meta + '<meta name="tdm-reservation" content="0">')
policy("last-one", '<meta name="tdm-reservation" content="0">' + meta)
policy("invalid-literal", '<meta name="tdm-reservation" content="true">')
policy("invalid-after-one", meta + '<meta name="tdm-reservation" content="2">')
policy(
    "duplicate-attributes", '<META NAME="wrong" NAME="tdm-reservation" CONTENT="0" CONTENT=" 1 ">'
)
policy("comments-ignored", "<!--" + meta + "-->")
policy("script-ignored", "<script>" + meta + "</script>")
policy("unicode-inside", meta, prefix=65_000, fill="é")
policy("unicode-outside", meta, prefix=65_536, fill="é")
Path(__file__).with_name("python_policies.json").write_text(
    json.dumps(policies, indent=2, ensure_ascii=False) + "\n"
)
print(f"froze {len(cases)} details and {len(policies)} policies")
