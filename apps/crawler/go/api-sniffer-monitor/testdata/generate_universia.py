"""Freeze actual Universia identity, pagination and rich-field contracts."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import httpx

from src.core.monitors import universia

BOARD = "11111111-1111-4111-8111-111111111111"
JOB = "22222222-2222-4222-8222-222222222222"
ROW = dict(
    identifier=JOB,
    boards=[BOARD],
    status="published",
    title="  Go\nEngineer ",
    description=" <p>Build Go.</p> ",
    requirements=" <ul><li>Go</li></ul> ",
    incentiveCompensation="<p>Degree</p>",
    url=f"https://www.universia.net/es/empleo/{JOB}/engineer.html?referer={BOARD}",
    jobLocation=dict(
        address=dict(addressLocality=" Bogotá ", addressRegion="Bogotá", addressCountry="CO")
    ),
    employmentType="FULL_TIME",
    jobLocationType="TELECOMMUTE",
    postingType="job",
    educationalLevel="degree",
    totalJobOpenings=2,
    role=dict(name=" Engineering "),
    contractType=dict(name=" Permanent "),
    datePosted="2026-10-01",
    validThrough="2026-12-01",
)


async def main():
    scopes = []
    for name, payload in (
        (
            "company",
            dict(slug="sample", entity=dict(id=BOARD, entityType="company"), languages=["es-CO"]),
        ),
        (
            "university",
            dict(
                slug="sample",
                entity=dict(id=BOARD.upper(), entityType="university"),
                languages=["de"],
            ),
        ),
        (
            "bad-language",
            dict(slug="sample", entity=dict(id=BOARD, entityType="company"), languages=["ES"]),
        ),
        ("foreign-slug", dict(slug="foreign", entity=dict(id=BOARD, entityType="company"))),
        ("bad-type", dict(slug="sample", entity=dict(id=BOARD, entityType="other"))),
        ("bad-id", dict(slug="sample", entity=dict(id="bad", entityType="company"))),
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _, payload=payload: httpx.Response(200, json=payload)
            )
        ) as client:
            try:
                board_id, language = await universia._resolve_board(client, "sample")
                value, error = dict(board_id=board_id, language=language), None
            except ValueError:
                value, error = None, "ValueError"
        scopes.append(dict(name=name, payload=payload, value=value, error=error))
    pages = []
    base = dict(offset=0, limit=100, size=1, total=1, totalPages=1, results=[ROW])
    for name, patch in (
        ("one", {}),
        ("empty", dict(size=0, total=0, totalPages=0, results=[])),
        ("gap", dict(total=2)),
        ("wrong-offset", dict(offset=100)),
        ("bad-pages", dict(totalPages=2)),
        ("bool-total", dict(total=True)),
        ("string-total", dict(total="1")),
        ("float-total", dict(total=1.0)),
        ("foreign-board", dict(results=[{**ROW, "boards": [JOB]}])),
        ("unpublished", dict(results=[{**ROW, "status": "draft"}])),
        ("duplicate", dict(size=2, total=2, results=[ROW, ROW])),
    ):
        payload = {**base, **patch}
        try:
            rows, total = universia._parse_page(payload, board_id=BOARD, requested_offset=0)
            error = None
        except ValueError:
            rows, total, error = None, None, "ValueError"
        pages.append(dict(name=name, payload=payload, rows=rows, total=total, error=error))
    jobs = []
    for name, patch in (
        ("rich", {}),
        ("intern", dict(employmentType="internship")),
        ("street", dict(jobLocation=dict(address=dict(streetAddress=" Main\nRoad ")))),
        ("missing-title", dict(title=" ")),
        ("missing-description", dict(description=" ")),
        ("foreign-url", dict(url=ROW["url"].replace("www.universia.net", "foreign.example"))),
        ("duplicate-query", dict(url=ROW["url"] + f"&referer={BOARD}")),
        ("wrong-entity", dict(url=ROW["url"] + f"&entityid={JOB}")),
        ("query-entity", dict(url=ROW["url"] + f"&entityid={BOARD}")),
    ):
        row = {**copy.deepcopy(ROW), **patch}
        try:
            job = universia._parse_job(row, board_id=BOARD, language="es")
            value = {
                key: getattr(job, key)
                for key in (
                    "url",
                    "title",
                    "description",
                    "locations",
                    "employment_type",
                    "job_location_type",
                    "date_posted",
                    "language",
                    "extras",
                    "metadata",
                    "source_identity",
                )
            }
            error = None
        except ValueError:
            value, error = None, "ValueError"
        jobs.append(dict(name=name, row=row, value=value, error=error))
    Path(__file__).with_name("python_universia.json").write_text(
        json.dumps(dict(scopes=scopes, pages=pages, jobs=jobs), indent=2) + "\n"
    )


asyncio.run(main())
