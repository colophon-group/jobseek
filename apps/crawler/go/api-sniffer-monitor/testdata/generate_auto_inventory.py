"""Freeze automatic field mapping through original complete HTTP discovery."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import generate_inventory as oracle


async def main():
    def config(**extra):
        return {"api_url": oracle.API, "json_path": "jobs", "url_field": "url", **extra}

    cases = [
        (
            "title-description",
            [{"url": "/jobs/1", "title": "Engineer", "description": "<p>Build</p>"}],
        ),
        (
            "alternate-names",
            [
                {
                    "url": "/jobs/1",
                    "jobTitle": "Engineer",
                    "bodyHtml": "<p>Build</p>",
                    "employmentType": "full-time",
                    "remote": True,
                    "postedAt": "2026-10-08",
                }
            ],
        ),
        ("plain-locations", [{"url": "/jobs/1", "title": "Engineer", "location": "Zurich"}]),
        (
            "location-list",
            [{"url": "/jobs/1", "title": "Engineer", "locations": ["Paris", "London"]}],
        ),
        (
            "location-objects",
            [
                {
                    "url": "/jobs/1",
                    "title": "Engineer",
                    "locations": [{"label": "Paris"}, {"label": "London"}],
                }
            ],
        ),
        (
            "location-dictionary-order",
            [
                {
                    "url": "/jobs/1",
                    "title": "Engineer",
                    "location": {"code": "CH", "other": "Zurich"},
                }
            ],
        ),
        (
            "metadata-object",
            [
                {
                    "url": "/jobs/1",
                    "title": "Engineer",
                    "department": {"other": "Systems", "name": "Engineering"},
                }
            ],
        ),
        ("metadata-string", [{"url": "/jobs/1", "title": "Engineer", "team": "Systems"}]),
        (
            "adp-address",
            [
                {
                    "url": "/jobs/1",
                    "title": "Engineer",
                    "workLevelCode": "Full Time",
                    "requisitionLocations": [
                        {
                            "address": {
                                "cityName": "Paris",
                                "countrySubdivisionLevel1": {"codeValue": "IDF"},
                                "country": {"longName": "France"},
                            }
                        }
                    ],
                }
            ],
        ),
        (
            "adp-namecode",
            [
                {
                    "url": "/jobs/1",
                    "title": "Engineer",
                    "locations": [{"nameCode": {"shortName": "Zurich", "longName": "Switzerland"}}],
                }
            ],
        ),
        ("no-recognized-fields", [{"url": "/jobs/1", "identifier": "1"}]),
        ("empty", []),
        (
            "sample-null-before-valid",
            [
                {"url": "/jobs/1", "location": None},
                {"url": "/jobs/2", "location": ["Paris"], "title": "Engineer"},
            ],
        ),
        (
            "only-first-five",
            [{"url": f"/jobs/{i}", "id": i} for i in range(5)]
            + [{"url": "/jobs/5", "title": "Later"}],
        ),
    ]
    for name, jobs in cases:
        await oracle.run(name, config(), {(0, None): (200, {"jobs": jobs})})
    await oracle.run(
        "mapping-after-filter",
        config(item_filter={"include": {"scope": ["main"]}}),
        {
            (0, None): (
                200,
                {
                    "jobs": [
                        {
                            "url": "/jobs/0",
                            "title": "Excluded",
                            "name": "Ambiguous",
                            "scope": "other",
                        },
                        {"url": "/jobs/1", "title": "Engineer", "scope": "main"},
                    ]
                },
            )
        },
    )
    pg = {"param_name": "page", "start_value": 1, "increment": 1, "max_pages": 3}
    await oracle.run(
        "mapping-after-pagination",
        config(pagination=pg, params={"page": 1}),
        {
            (1, None): (200, {"jobs": [{"url": "/jobs/1", "id": 1}], "total": 2}),
            (2, None): (200, {"jobs": [{"url": "/jobs/2", "title": "Engineer"}], "total": 2}),
        },
    )
    await oracle.run(
        "later-invalid-discards-mapping",
        config(pagination=pg, params={"page": 1}),
        {
            (1, None): (200, {"jobs": [{"url": "/jobs/1", "title": "Engineer"}], "total": 2}),
            (2, None): (200, {"unexpected": []}),
        },
    )
    Path(__file__).with_name("python_auto_inventory.json").write_text(
        json.dumps(oracle.cases, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(oracle.cases)} actual Python automatic HTTP cases")


if __name__ == "__main__":
    asyncio.run(main())
