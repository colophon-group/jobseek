"""Freeze offline Python production outputs; no publisher or database access."""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors import smartrecruiters as sr
from src.core.scrapers.smartrecruiters import _parse_detail

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "canonical_fixtures", ROOT / "tests/test_smartrecruiters_canonical_identity.py"
)
fixture_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture_module)

cases = []
TOKEN = "SwissMedicalNetwork1"
BASE = f"https://api.smartrecruiters.com/v1/companies/{TOKEN}/postings"
BOARD = f"https://careers.smartrecruiters.com/{TOKEN}"


def rich(id, **kwargs):
    d = fixture_module._detail(id, **kwargs)
    d["jobAd"] = {
        "sections": {
            "jobDescription": {"title": "Tasks & duties", "text": "<p>Build things.</p>"},
            "qualifications": {"text": "<ul><li>Experience</li></ul>"},
        }
    }
    d["location"]["fullLocation"] = "Biel, Switzerland"
    d["typeOfEmployment"]["label"] = "Full-time"
    d["department"]["label"] = "Research"
    d["function"]["label"] = "Engineering"
    d["experienceLevel"]["label"] = "Experienced"
    d["compensation"] = {
        "salary": {"min": 0, "max": 100_000, "currency": "CHF", "period": "annually"}
    }
    return d


def add(name, meta, responses, board=BOARD):
    cases.append({"name": name, "board_url": board, "metadata": meta, "responses": responses})


def detail_case(name, meta, details, listed=None):
    listed = listed if listed is not None else [fixture_module._listed(d) for d in details]
    responses = {f"{BASE}?limit=100&offset=0": [{"content": listed, "totalFound": len(listed)}]}
    responses.update({f"{BASE}/{d['id']}": [d] for d in details})
    add(name, {"token": TOKEN, **meta}, responses)


async def main():
    list_url = f"{BASE}?limit=100&offset=0"
    for ids in [[], ["one", 2, "  three  ", 10**40], ["Ａ"]]:
        add(
            f"plain-{len(ids)}",
            {},
            {list_url: [{"content": [{"id": i} for i in ids], "totalFound": len(ids)}]},
        )
    for total in [-1, True, 1.5, None]:
        add(f"invalid-total-{total}", {}, {list_url: [{"content": [], "totalFound": total}]})
    for content in [None, {}, [None], [{"id": None}], [{"id": True}], [{"id": " "}]]:
        add(f"invalid-content-{content}", {}, {list_url: [{"content": content, "totalFound": 1}]})
    add("premature-page", {}, {list_url: [{"content": [{"id": "1"}], "totalFound": 101}]})
    first = {"content": [{"id": str(i)} for i in range(100)], "totalFound": 101}
    tail_url = f"{BASE}?limit=100&offset=100"
    last = {"content": [{"id": "100"}], "totalFound": 101}
    add("pagination", {}, {list_url: [first], tail_url: [last]})
    add("total-churn-retry", {}, {list_url: [first], tail_url: [{**last, "totalFound": 102}, last]})
    add(
        "duplicate-retry",
        {},
        {list_url: [first], tail_url: [{"content": [{"id": "0"}], "totalFound": 101}, last]},
    )
    add("repeated-snapshot-fails", {}, {list_url: [first], tail_url: [{**last, "totalFound": 102}]})
    add(
        "over-advertised-count",
        {},
        {list_url: [{"content": [{"id": "1"}, {"id": "2"}], "totalFound": 1}]},
    )

    de = rich("743999001001", default=True)
    en = rich("743999001002", language="en")
    fr = rich("743999001003", language="fr", latitude="48.0")
    unique = rich(
        "743999001004", job_id="819700b3-a847-46f1-9b08-cf23a9591f68", latitude=None, longitude=None
    )
    unique["location"]["postalCode"] = "  Straße-É  "
    detail_case(
        "canonical-bilingual-locations-fallback",
        {"canonical_identity": "job-location-v1"},
        [de, en, fr, unique],
    )
    detail_case("localized-job-id", {"canonical_identity": "job-v1"}, [de, en, fr])
    detail_case(
        "localized-template",
        {
            "canonical_job_id_url_template": "https://career.hm.com/job/{job_id}/",
            "language_preference": ["FR", "en", "fr"],
        },
        [de, en, fr],
    )
    for period in [None, "hr", "TWO_WEEKS", "monthly", "weird"]:
        item = deepcopy(en)
        item["compensation"]["salary"]["period"] = period
        detail_case(f"salary-{period}", {"canonical_identity": "job-v1"}, [item])
    for field in [
        "uuid",
        "jobAdId",
        "defaultJobAd",
        "refNumber",
        "releasedDate",
        "visibility",
        "ref",
        "company",
        "language",
        "location",
        "id",
    ]:
        listed = fixture_module._listed(de)
        listed.pop(field)
        detail_case(
            f"canonical-missing-{field}", {"canonical_identity": "job-location-v1"}, [de], [listed]
        )
    for value in [
        "91",
        "-90.0000000000000001",
        "047",
        "NaN",
        "1e1",
        None,
        47,
        "-0.000",
        "1.٢٣",
        "47.1234567890123456",
    ]:
        item = deepcopy(de)
        item["location"]["latitude"] = value
        detail_case(f"coordinate-{value}", {"canonical_identity": "job-location-v1"}, [item])
    for variant in [
        "duplicate-language",
        "double-default",
        "missing-description",
        "repeated-fallback",
        "inactive",
        "not-public",
    ]:
        pair = deepcopy([de, en])
        if variant == "duplicate-language":
            pair[1]["language"] = {"code": "de"}
        elif variant == "double-default":
            pair[1]["defaultJobAd"] = True
        elif variant == "missing-description":
            pair[1].pop("jobAd")
        elif variant == "repeated-fallback":
            for d in pair:
                d["location"].pop("latitude")
                d["location"].pop("longitude")
        elif variant == "inactive":
            pair[1]["active"] = False
        else:
            pair[1]["visibility"] = "PRIVATE"
        detail_case(variant, {"canonical_identity": "job-location-v1"}, pair)
    detail_case("detail-churn-retry", {"canonical_identity": "job-v1"}, [en])
    cases[-1]["responses"][f"{BASE}/{en['id']}"] = [{**en, "active": False}, en]
    detail_case(
        "detail-repeated-inactive", {"canonical_identity": "job-v1"}, [{**en, "active": False}]
    )
    for config in [
        {"canonical_identity": "other"},
        {
            "canonical_identity": "job-v1",
            "canonical_job_id_url_template": "https://x.test/{job_id}",
        },
        {"canonical_job_id_url_template": "http://x.test/{job_id}"},
        {"canonical_identity": "job-v1", "language_preference": ["bad"]},
    ]:
        add(f"invalid-options-{len(cases)}", config, {list_url: [{"content": [], "totalFound": 0}]})

    for case in cases:
        positions = {}

        def handler(request, case=case, positions=positions):
            key = str(request.url)
            values = case["responses"].get(key)
            if values is None:
                raise AssertionError(f"Unexpected offline request: {key}")
            position = positions.get(key, 0)
            positions[key] = position + 1
            return httpx.Response(200, json=values[min(position, len(values) - 1)])

        with patch.object(sr.asyncio, "sleep", AsyncMock()):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                try:
                    result = await sr.discover(
                        {"board_url": case["board_url"], "metadata": case["metadata"]}, client
                    )
                    if isinstance(result, MonitorResult):
                        jobs = list(result.jobs_by_url.values()) if result.jobs_by_url else []
                        normalized = {
                            "urls": sorted(result.urls),
                            "jobs": [dataclasses.asdict(j) for j in jobs],
                            "truncated": result.truncated,
                        }
                    elif isinstance(result, set):
                        normalized = {"urls": sorted(result), "jobs": None, "truncated": False}
                    else:
                        normalized = {
                            "urls": sorted(j.url for j in result),
                            "jobs": [dataclasses.asdict(j) for j in result],
                            "truncated": False,
                        }
                    case["expected"] = normalized
                except (ValueError, TypeError, KeyError, AttributeError) as e:
                    case["error"] = type(e).__name__
        case["python_requests"] = positions
    target = Path(__file__).with_name("python_inventory.json")
    target.write_text(json.dumps(cases, ensure_ascii=True, indent=2) + "\n")
    detail_cases = []
    seen = set()
    for case in cases:
        for endpoint, values in case["responses"].items():
            if "?" in endpoint:
                continue
            for value in values:
                key = json.dumps(value, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                fixture = {"name": f"detail-{len(detail_cases)}", "input": value}
                try:
                    fixture["expected"] = dataclasses.asdict(_parse_detail(value))
                except (ValueError, TypeError, KeyError, AttributeError) as error:
                    fixture["error"] = type(error).__name__
                detail_cases.append(fixture)
    Path(__file__).with_name("python_detail.json").write_text(
        json.dumps(detail_cases, ensure_ascii=True, indent=2) + "\n"
    )
    print(f"Wrote {len(cases)} monitor and {len(detail_cases)} detail offline oracle cases")


asyncio.run(main())
