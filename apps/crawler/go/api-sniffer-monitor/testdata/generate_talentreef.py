"""Freeze actual Python brand scope, request, counters and rich fields."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from src.core.monitors import talentreef


async def main():
    scopes = []
    for name, payload, locale in (
        ("requested", [dict(published=True, locale="en", clientId=123, brands=[" One "])], "en"),
        (
            "default",
            [
                dict(
                    published=True,
                    defaultLocale=True,
                    locale="en-us",
                    clientId="123",
                    brands=["One", "Two"],
                )
            ],
            "fr",
        ),
        ("first", [dict(published=True, locale="en", clientId="123", brands=["One"])], "fr"),
        ("unpublished", [dict(published=False, locale="en", clientId="123", brands=["One"])], "en"),
        ("bad-client", [dict(published=True, locale="en", clientId=True, brands=["One"])], "en"),
        (
            "no-brands",
            [dict(published=True, locale="en", clientId="123", brands=[None, " "])],
            "en",
        ),
        ("wrong-shape", {}, "en"),
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _, payload=payload: httpx.Response(200, json=payload)
            )
        ) as client:
            try:
                value = await talentreef._fetch_career_page("sample", locale, client)
                error = None
            except ValueError:
                value, error = None, "ValueError"
        scopes.append(dict(name=name, payload=payload, locale=locale, value=value, error=error))
    metadata = dict(alias="sample", client_id="123", locale="en", brands=["One", "Two"])
    jobs = []
    for name, source in (
        (
            "rich",
            dict(
                jobId=17,
                positionType=" Engineer ",
                description="<p>Build Go.</p>",
                address=dict(street1="Main", city=" Zürich ", country="CH"),
                category="Full Time",
                createdDate="2026-10-01",
                postingUuid="uuid",
                brand="One",
                clientName="Employer",
            ),
        ),
        (
            "dedup-location",
            dict(
                jobId="18",
                positionType="Engineer",
                address=dict(street1="CH", city="CH", country="CH"),
            ),
        ),
        ("null-id", dict(jobId=None, positionType="Engineer")),
        ("boolean-id", dict(jobId=True, positionType="Engineer")),
        ("missing-id", dict(positionType="Engineer")),
        ("bad-title", dict(jobId="18", positionType=3)),
        (
            "optional-wrong-types",
            dict(jobId="18", positionType="Engineer", description=3, category=3, address=[]),
        ),
    ):
        hit = {"_source": source}
        job = talentreef._parse_job(hit, metadata)
        value = (
            None
            if job is None
            else {
                key: getattr(job, key)
                for key in (
                    "url",
                    "title",
                    "description",
                    "locations",
                    "employment_type",
                    "date_posted",
                    "language",
                    "metadata",
                    "source_identity",
                )
            }
        )
        jobs.append(dict(name=name, hit=hit, value=value))
    pages = []
    for name, payload in (
        ("empty", dict(hits=dict(total=0, hits=[]))),
        ("object-total", dict(hits=dict(total=dict(value=1), hits=[{}]))),
        ("bool-total", dict(hits=dict(total=True, hits=[{}]))),
        ("nonobject-filter", dict(hits=dict(total=1, hits=[None, {}]))),
        ("bad-total", dict(hits=dict(total="1", hits=[{}]))),
        ("negative", dict(hits=dict(total=-1, hits=[]))),
        ("float", dict(hits=dict(total=1.0, hits=[{}]))),
        ("bad-shape", dict(hits=[])),
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _, payload=payload: httpx.Response(200, json=payload)
            )
        ) as client:
            try:
                rows, total = await talentreef._fetch_search_page(metadata, 0, client)
                error = None
            except ValueError:
                rows, total, error = None, None, "ValueError"
        pages.append(dict(name=name, payload=payload, rows=rows, total=total, error=error))
    Path(__file__).with_name("python_talentreef.json").write_text(
        json.dumps(
            dict(
                scopes=scopes,
                jobs=jobs,
                pages=pages,
                request=talentreef._search_payload(metadata, 1000),
            ),
            indent=2,
        )
        + "\n"
    )


asyncio.run(main())
