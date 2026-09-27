"""Freeze RSS parser boundaries using the retained Python implementation."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET

from src.core.monitors.rss import _parse_sf_item

fragments = [
    "",
    "<link/>",
    "<link> </link>",
    "<link>https://example.test/1</link>",
    "<link>https://example.test/1</link><link>https://example.test/2</link>",
    "<link/><link>https://example.test/2</link>",
    "<x:link>https://wrong.test/1</x:link><link>https://example.test/1</link>",
    "<link>https://example.test/1<nested/>ignored-tail</link>",
]
fields = [
    "<title/>",
    "<title> </title>",
    "<title> First <b>nested</b> tail</title>",
    "<title><!--comment-->Title</title>",
    "<title>First</title><title>Second</title>",
    "<title/><title>Second</title>",
    "<x:title>Wrong</x:title><title>Right</title>",
    "<description>&amp;nbsp;Build&amp;nbsp;</description>",
    "<description>&amp;#32;</description>",
    "<description>&amp;copy; &amp;notit; &amp;amp; &amp;#x1F600;</description>",
    "<description>First<b>nested</b>tail</description>",
    "<title>Straße</title><description>STRASSE</description>",
    "<title>ΟΣ</title><description>οσ</description>",
    "<title>Engineer\u00a0(Bern, CH)</title><g:location>Bern, CH</g:location>",
    "<title>Engineer (Bern, CH)</title><g:location> </g:location>",
    (
        "<title>Engineer (STRASSE, CH)</title><description"
        "><![CDATA[<b>Location:</b>Straße]]></description>"
    ),
    (
        "<title>Engineer (Bern, CH)</title><description>"
        "<![CDATA[<b>\xa0Location:\xa0</b>Bern]]></description>"
    ),
    (
        "<title>Engineer (Bern, CH)</title><description>"
        "<![CDATA[<b>Location</b>Zurich]]></description>"
    ),
    (
        "<title>Engineer (Bern, CH)</title><g:location>B"
        "ern</g:location><g:location>Zurich</g:location>"
    ),
    (
        "<guid> </guid><pubDate> </pubDate><g:job_functi"
        "on> </g:job_function><g:employer> </g:employer>"
    ),
    "<guid>ID</guid><pubDate>2026-09-27</pubDate><g:job_function>ATS_WEBFORM</g:job_function>",
    (
        "<g:employer>Employer</g:employer><g:job_function>Engineering</g"
        ":job_function><g:expiration_date>2027-01-01</g:expiration_date>"
    ),
    "<g:location/><g:location>Zurich</g:location>",
    "<location>Wrong</location><x:location>Wrong too</x:location>",
]
fragments += ["<link>https://example.test/1</link>" + field for field in fields]
rows = []
for fragment in fragments:
    feed = (
        '<rss xmlns:g="http://base.google.com/ns/1.0" xmlns:x="urn:other"><channel><item>'
        + fragment
        + "</item></channel></rss>"
    )
    item = ET.fromstring(feed).find("./channel/item")
    job = _parse_sf_item(item)
    rows.append(
        {
            "feed": feed,
            "items": 1,
            "jobs": []
            if job is None
            else [{k: v for k, v in asdict(job).items() if v is not None}],
        }
    )
Path(__file__).with_name("python_parser.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
)
print(f"Frozen {len(rows)} Python parser cases")
