"""Freeze offline reference item fields for the grouped native RSS migration."""

from __future__ import annotations

import dataclasses
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from src.core.monitors.rss import _parse_governmentjobs_item, _parse_zoho_recruit_item

NS = "http://www.neogov.com/namespaces/JobListing"
CASES = []
for preset, parser in [
    ("governmentjobs", _parse_governmentjobs_item),
    ("zoho_recruit", _parse_zoho_recruit_item),
]:
    for mode in [
        "full",
        "minimal",
        "missing_link",
        "nested_title",
        "wrong_namespace",
        "duplicate",
        "unicode_id",
        "non_provider_host",
    ]:
        link = (
            "https://fixture.zohorecruit.eu/jobs/Careers/123"
            if preset == "zoho_recruit"
            else "https://www.governmentjobs.com/careers/fixture/jobs/123"
        )
        if mode == "non_provider_host":
            link = "https://fixture.example.com/jobs/123"
        title = " Café &amp;amp; 東京 "
        if mode == "nested_title":
            title = " Leading <b>nested</b> trailing "
        ns = NS if mode != "wrong_namespace" else "https://example.com/foreign"
        guid = "１２３" if mode == "unicode_id" else "123"
        fields = f"<title>{title}</title><guid>{guid}</guid><pubDate>2026-10-07</pubDate>"
        if mode != "missing_link":
            fields += f"<link>{link}</link>"
        if mode != "minimal":
            fields += (
                "<description><![CDATA[<p>Build &amp; learn.</p>Lieu : <b>Zürich</b>"
                " &amp;amp; 東京<br />]]></description>"
            )
            fields += "<Location>ignored legacy location</Location><JobID>ignored legacy id</JobID>"
            fields += (
                "<g:location>Zürich, CH</g:location><g:jobType>Full Time</g:jobType>"
                "<g:jobId>456</g:jobId><g:department>Engineering</g:department>"
            )
            fields += (
                "<g:examplesofduties><![CDATA[<p>Serve &amp; build</p>]]>"
                "</g:examplesofduties><g:qualifications>"
                "Degree &amp;amp; experience</g:qualifications>"
                "<g:supplementalinformation>Remote</g:supplementalinformation>"
            )
        if mode == "duplicate":
            fields += (
                "<title>Second title</title><guid>999</guid><g:location>"
                "second location</g:location>"
            )
        item = f'<item xmlns:g="{ns}">{fields}</item>'
        job = parser(ET.fromstring(item))
        CASES.append(
            {
                "name": preset + "-" + mode,
                "preset": preset,
                "body": "<rss><channel>" + item + "</channel></rss>",
                "jobs": [dataclasses.asdict(job)] if job else [],
            }
        )
Path(__file__).with_name("python_rss_provider_items.json").write_text(
    json.dumps({"cases": CASES}, ensure_ascii=False, indent=2) + "\n"
)
print(f"Frozen {len(CASES)} actual Python RSS item cases")
