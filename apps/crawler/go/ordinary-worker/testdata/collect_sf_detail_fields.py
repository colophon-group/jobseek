"""Freeze actual Python SuccessFactors field mapping and enrichment semantics."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

import httpx
from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import DiscoveredJob, rss


async def main() -> None:
    cases = []
    for name, metadata, source, initial in [
        (
            "company",
            {"fetch_company": True},
            '<div data-careersite-propertyid="customfield1"> Main </div>',
            {},
        ),
        ("optional-missing", {"fetch_company": True}, "<p>Optional</p>", {"company": "Careers"}),
        (
            "required",
            {"detail_fields": {"service": "dept", "adcode": "adcode"}},
            (
                '<div data-careersite-propertyid="dept">Roads &amp; <b>Bridges</b></div>'
                '<span data-careersite-propertyid="adcode"> A <b> B </b> C </span>'
            ),
            {},
        ),
        ("required-missing", {"detail_fields": {"service": "dept"}}, "<p>No property</p>", {}),
        (
            "override-company",
            {"fetch_company": True, "detail_fields": {"company": "dept"}},
            (
                '<span data-careersite-propertyid="customfield1">Ignored</span>'
                '<span data-careersite-propertyid="dept">Roads</span>'
            ),
            {},
        ),
        (
            "first-property",
            {"detail_fields": {"service": "dept"}},
            (
                '<span data-careersite-propertyid="dept">東京 Zürich</span>'
                '<span data-careersite-propertyid="dept">Ignored</span>'
            ),
            {},
        ),
        (
            "url-prefilter",
            {"fetch_company": True, "url_filter": "/us/job/"},
            '<span data-careersite-propertyid="customfield1">Main</span>',
            {},
        ),
    ]:
        calls = []

        async def document(url, client, *, requests=calls, fixture_source=source):
            requests.append(url)
            return LexborHTMLParser(fixture_source)

        original = rss._fetch_sf_detail_document
        rss._fetch_sf_detail_document = document
        fields, required = rss._sf_detail_fields(metadata)
        jobs = [DiscoveredJob(url="https://example.com/us/job/1", metadata=copy.deepcopy(initial))]
        if name == "url-prefilter":
            jobs.append(DiscoveredJob(url="https://foreign.example/eu/job/2", metadata={}))
        inputs = [{"url": job.url, "metadata": copy.deepcopy(job.metadata)} for job in jobs]
        error = False
        try:
            async with httpx.AsyncClient() as client:
                await rss._enrich_sf_detail_fields(
                    jobs,
                    client,
                    feed_url="https://example.com/googlefeed.xml",
                    fields=fields,
                    required_fields=required,
                    url_filter=metadata.get("url_filter"),
                )
        except RuntimeError:
            error = True
        finally:
            rss._fetch_sf_detail_document = original
        cases.append(
            {
                "name": name,
                "config": metadata,
                "html": '<h1 data-careersite-propertyid="title">Engineer</h1>' + source,
                "input": inputs,
                "expected": [job.metadata for job in jobs],
                "calls": calls,
                "fields": fields,
                "required": sorted(required),
                "error": error,
            }
        )
    output = {
        "reference": (
            "actual Python RSS field mapping/enrichment; "
            "HTTP document boundary replaced with fixture HTML"
        ),
        "python": sys.version.split()[0],
        "cases": cases,
    }
    target = Path(__file__).with_name("python_sf_detail_fields.json")
    target.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n")


asyncio.run(main())
