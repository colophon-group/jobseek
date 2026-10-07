"""Freeze actual Python legacy XML and structured RSS summary behavior."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from src.core.monitors.rss import (
    _parse_generic_title_employment_location_item,
    _parse_sf_legacy_xml_item,
    _sf_legacy_xml_identity,
    discover_stream,
)

cases = []
for mode in [
    "full",
    "minimal",
    "missing_id",
    "missing_title",
    "nondigit",
    "unicode_id",
    "superscript_id",
    "nested_title",
    "duplicate",
    "wrong_namespace",
    "fallback_site",
    "unknown_location_type",
    "qualified_location_type",
    "invalid_xml",
]:
    fields = {
        "ReqId": "123",
        "JobTitle": "Café &amp; 東京",
        "Job-Description": "<![CDATA[<p>Build &amp; learn.</p>]]>",
        "filter8": "<value>Zürich</value>",
        "filter7": "<value>CH</value>",
        "filter6": "<value>Basel</value>",
        "filter4": "<value>Remote</value>",
        "filter1": "<value>Engineering</value>",
        "filter2": "<value>10%</value>",
        "filter3": "<value>Senior</value>",
        "mfield2": "<value>CH &amp; DE</value>",
    }
    if mode == "minimal":
        fields = {"ReqId": "123", "JobTitle": "Engineer"}
    if mode == "missing_id":
        fields.pop("ReqId")
    if mode == "missing_title":
        fields.pop("JobTitle")
    if mode == "nondigit":
        fields["ReqId"] = "12x"
    if mode == "unicode_id":
        fields["ReqId"] = "１２٣"
    if mode == "superscript_id":
        fields["ReqId"] = "²³"
    if mode == "nested_title":
        fields["JobTitle"] = "Leading <b>nested</b> tail"
    if mode == "fallback_site":
        fields.pop("filter8")
        fields.pop("filter7")
    if mode == "unknown_location_type":
        fields["filter4"] = "<value>Unexpected</value>"
    if mode == "qualified_location_type":
        fields["filter4"] = "<value>Onsite (5 Days per Week)</value>"
    item = "<Job>" + "".join(f"<{k}>{v}</{k}>" for k, v in fields.items())
    if mode == "duplicate":
        item += "<JobTitle>Second</JobTitle><filter8><value>Other</value></filter8>"
    item += "</Job>"
    if mode == "wrong_namespace":
        item = item.replace("<Job>", '<Job xmlns="https://foreign.example/">')
    body = '<?xml version="1.0"?><Jobs>' + item + "</Jobs>"
    error = mode == "invalid_xml"
    if error:
        body += "<broken"
    job = (
        None
        if error
        else _parse_sf_legacy_xml_item(
            ET.fromstring(item), origin="https://career.example.com", company="Fixture_Company"
        )
    )
    cases.append(
        {
            "name": "legacy-" + mode,
            "kind": "legacy_xml",
            "body": body,
            "jobs": [dataclasses.asdict(job)] if job else [],
            "error": error,
        }
    )

for mode, title, summary in [
    ("full", "Engineer", "Engineer | Full Time | Zürich"),
    ("location_only", "Engineer", "Engineer | Basel"),
    ("whitespace", "  Engineer\n II ", " Engineer  II | Full\n Time | Zürich  CH "),
    ("entities", "Caf&amp;eacute;", "Caf&amp;eacute; | Zürich &amp;amp; CH"),
    ("missing_title", "", "Engineer | Zürich"),
    ("wrong_prefix", "Engineer", "Designer | Zürich"),
    ("empty_location", "Engineer", "Engineer | "),
    ("three_fields", "Engineer", "Engineer | Full Time | Zürich | CH"),
    ("empty_type", "Engineer", "Engineer |  | Zürich"),
    ("missing_link", "Engineer", "Engineer | Zürich"),
]:
    item = f"<item><title>{title}</title><description>{summary}</description>"
    if mode != "missing_link":
        item += "<link>https://example.com/jobs/123</link>"
    item += "<guid>123</guid><Location>Original</Location></item>"
    try:
        job = _parse_generic_title_employment_location_item(ET.fromstring(item))
        error = False
    except ValueError:
        job, error = None, True
    cases.append(
        {
            "name": "summary-" + mode,
            "kind": "summary",
            "body": "<rss><channel>" + item + "</channel></rss>",
            "jobs": [dataclasses.asdict(job)] if job else [],
            "error": error,
        }
    )

identity_cases = []
for query in [
    "company=Fixture&career_ns=job_listing_summary&resultType=XML",
    "company=%20Fixture%20&career_ns=job_listing_summary&resultType=XML",
    "company=Fixture&company=Other&career_ns=job_listing_summary&resultType=XML",
    "company=Fixture&career_ns=wrong&resultType=XML",
    "company=Fixture&career_ns=job_listing_summary&resultType=xml",
    "company=Fixture&career_ns=job_listing_summary&resultType=XML&extra=1",
    "company=a.b&career_ns=job_listing_summary&resultType=XML",
]:
    source = "https://Career.example.com/career?" + query
    identity_cases.append({"source": source, "expected": _sf_legacy_xml_identity(source)})


async def collect_streams():
    streams = []
    for kind in ["legacy_xml", "summary"]:
        for mode in [
            "complete",
            "malformed_small",
            "malformed_large",
            "trailing_data",
            "late_parser",
        ]:
            legacy = kind == "legacy_xml"
            if legacy and mode == "late_parser":
                continue
            items = []
            for n in range(201):
                if legacy:
                    padding = "x" * (512 if mode == "malformed_large" else 0)
                    item = (
                        f"<Job><ReqId>{n + 1}</ReqId><JobTitle>Engineer</JobTitle>"
                        f"<Job-Description>{padding}Build.</Job-Description></Job>"
                    )
                else:
                    padding = " " * (512 if mode == "malformed_large" else 0)
                    summary = f"Engineer | {padding}Zürich"
                    if mode == "late_parser" and n == 200:
                        summary = "Different title | Zürich"
                    item = (
                        f"<item><link>https://example.com/jobs/{n + 1}</link>"
                        f"<title>Engineer</title><description>{summary}"
                        "</description></item>"
                    )
                items.append(item)
            body = (
                '<?xml version="1.0"?><Jobs>' + "".join(items) + "</Jobs>"
                if legacy
                else "<rss><channel>" + "".join(items) + "</channel></rss>"
            )
            if mode.startswith("malformed"):
                body += "<broken"
            if mode == "trailing_data":
                body += "junk"
            metadata = (
                {
                    "preset": "successfactors",
                    "variant": "legacy_xml",
                    "feed_url": "https://career.example.com/career?company=Fixture_Company"
                    "&career_ns=job_listing_summary&resultType=XML",
                }
                if legacy
                else {
                    "preset": "generic",
                    "feed_url": "https://example.com/feed",
                    "description_mode": "title_employment_location",
                }
            )
            batch_sizes, urls = [], []
            error = False
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request, body=body: httpx.Response(200, text=body, request=request)
                )
            ) as client:
                try:
                    async for batch in discover_stream(
                        {"board_url": "https://example.com/careers", "metadata": metadata}, client
                    ):
                        batch_sizes.append(len(batch))
                        urls.extend(job.url for job in batch)
                except (ET.ParseError, ValueError):
                    error = True
            streams.append(
                {
                    "name": kind + "-" + mode,
                    "kind": kind,
                    "body": body,
                    "error": error,
                    "batch_sizes": batch_sizes,
                    "urls": urls,
                }
            )
    return streams


streams = asyncio.run(collect_streams())
Path(__file__).with_name("python_rss_variants.json").write_text(
    json.dumps(
        {"cases": cases, "identities": identity_cases, "stream_cases": streams},
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(f"Frozen {len(cases)} parser, {len(identity_cases)} identity and {len(streams)} stream cases")
print([(c["name"], c["batch_sizes"], c["error"]) for c in streams])
