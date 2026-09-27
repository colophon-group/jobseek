"""Regenerate/check the Go migration oracle using the retained Python verifier.

Run from apps/crawler with its test dependencies installed. This script is
only an offline migration test; Go production execution never invokes Python.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import dataclasses
import json
import sys
from pathlib import Path

CRAWLER = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(CRAWLER), str(CRAWLER / "tests")]
from test_taxonomy_readiness import _Connection, _Pool, _Typesense  # noqa: E402

from src import taxonomy_readiness as verifier  # noqa: E402


def normalized_contract() -> dict:
    tree = ast.parse((CRAWLER / "src/taxonomy_readiness.py").read_text())
    queries = {
        item.targets[0].id.removeprefix("_").removesuffix("_SQL").lower(): ast.literal_eval(
            item.value
        )
        for item in tree.body
        if isinstance(item, ast.Assign)
        and isinstance(item.targets[0], ast.Name)
        and item.targets[0].id.endswith("_SQL")
    }
    return json.loads(
        json.dumps(
            {
                "collections": [dataclasses.asdict(spec) for spec in verifier._SPECS],
                "job_posting_schema_fields": [
                    dataclasses.asdict(field) for field in verifier._JOB_POSTING_SCHEMA_FIELDS
                ],
                "location_macro_aliases": verifier._LOCATION_MACRO_ALIASES,
                "queries": queries,
            },
            default=lambda value: sorted(value),
        )
    )


async def generate() -> dict:
    connection = _Connection()
    # Exercise Unicode, float spelling, wildcard duplicates, parent/domain
    # fallback, missing optional fields, and a company without an industry.
    connection.location_rows[2]["lat"] = 1.0
    connection.location_rows[2]["lng"] = -0.0
    connection.location_name_rows[0]["name"] = "Zürich <>&\u2028😀"
    connection.occupation_rows.extend(
        [
            {**connection.occupation_rows[0], "locale": "*", "name": "Shared", "is_display": False},
            {**connection.occupation_rows[0], "locale": "*", "name": "Shared", "is_display": False},
            {
                **connection.occupation_rows[0],
                "locale": "it",
                "name": "Italiano",
                "is_display": True,
            },
        ]
    )
    connection.technology_rows[-1]["name"] = None
    connection.company_rows[-1]["industry"] = None
    inputs = {
        "location_rows": connection.location_rows,
        "location_names": connection.location_name_rows,
        "location_macros": connection.location_macro_rows,
        "occupation_rows": connection.occupation_rows,
        "occupation_domain_names": connection.occupation_domain_name_rows,
        "seniority_rows": connection.seniority_rows,
        "technology_rows": connection.technology_rows,
        "company_rows": connection.company_rows,
        "industry_names": connection.industry_name_rows,
    }
    authoritative = await verifier._load_authoritative_snapshot(_Pool(connection))
    client = _Typesense(authoritative)
    cases = []
    for name in ("ready", "mismatches"):
        remote = copy.deepcopy(client)
        if name == "mismatches":
            remote.documents["location"][0]["slug"] = "wrong"
            remote.documents["company"][0]["industry_name_it"] = None
            remote.documents["technology"][-1]["id"] = "unexpected"
            remote.metadata["seniority"]["num_documents"] += 1
            remote.metadata["job_posting"]["fields"] = []
        evidence = await verifier.verify_taxonomy_readiness(_Pool(connection), remote, page_size=3)
        cases.append(
            {
                "name": name,
                "metadata": remote.metadata,
                "remote": remote.documents,
                "evidence": evidence,
            }
        )
    return {
        "input": inputs,
        "documents": {name: list(value.documents) for name, value in authoritative.items()},
        "cases": cases,
    }


if __name__ == "__main__":
    contract = json.loads((Path(__file__).parents[1] / "taxonomy_contract.json").read_text())
    assert contract == normalized_contract(), "embedded contract differs from Python source"
    result = (
        json.dumps(
            asyncio.run(generate()), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    )
    destination = Path(__file__).with_name("taxonomy_fixture.json")
    if "--check" in sys.argv:
        assert destination.read_text() == result, "Go fixture differs from retained Python verifier"
    else:
        destination.write_text(result)
