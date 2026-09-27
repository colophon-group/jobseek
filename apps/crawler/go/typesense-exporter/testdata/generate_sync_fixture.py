"""Offline Python producer oracle for the Go taxonomy/company sync port."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

CRAWLER = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(CRAWLER), str(CRAWLER / "tests")]
from src import sync  # noqa: E402


async def generate() -> dict:
    base = json.loads(Path(__file__).with_name("taxonomy_fixture.json").read_text())
    inputs = base["input"]
    companies = []
    descriptions = []
    for index, row in enumerate(inputs["company_rows"], 1):
        companies.append(
            {
                **row,
                "name": f"Company {index}",
                "slug": f"company-{index}",
                "icon": "icon.svg" if index == 1 else None,
                "logo": "logo.svg" if index == 2 else None,
                "website": "https://example.test" if index == 3 else "",
                "employee_count_range": 0 if index == 1 else None,
                "founded_year": 1998 if index == 2 else None,
            }
        )
        for locale in ("en", "de", "fr", "it"):
            descriptions.append(
                {
                    "company_id": row["id"],
                    "locale": locale,
                    "description": f"{locale} <p>Zürich 😀 {index}</p>",
                }
            )
    counts = {
        name: {str(index): index * 3 % 7 for index in range(1, 11)}
        for name in ("location", "occupation", "seniority", "technology")
    }
    counts["company"] = {row["id"]: index for index, row in enumerate(companies)}
    year = {row["id"]: index + 10 for index, row in enumerate(companies)}
    english = {
        row["location_id"]: row["name"]
        for row in inputs["location_names"]
        if row["locale"] == "en" and row["is_display"]
    }

    class Connection:
        async def fetch(self, sql: str) -> list[dict]:
            if "FROM job_posting" in sql:
                for field, collection in (
                    ("loc_id", "location"),
                    ("occupation_id", "occupation"),
                    ("seniority_id", "seniority"),
                    ("tech_id", "technology"),
                ):
                    if field in sql:
                        return [
                            {field: int(key), "cnt": count}
                            for key, count in counts[collection].items()
                        ]
                raise AssertionError(sql)
            if "FROM location_macro_member" in sql:
                return copy.deepcopy(inputs["location_macros"])
            if "FROM location_name" in sql and "FROM location l" not in sql:
                return copy.deepcopy(inputs["location_names"])
            if "FROM location l" in sql:
                return [
                    {**row, "parent_name": english.get(row["parent_id"])}
                    for row in inputs["location_rows"]
                ]
            if "FROM occupation_domain_name" in sql:
                return copy.deepcopy(inputs["occupation_domain_names"])
            if "FROM occupation o" in sql:
                return copy.deepcopy(inputs["occupation_rows"])
            if "FROM seniority s" in sql:
                return copy.deepcopy(inputs["seniority_rows"])
            if "FROM technology" in sql:
                return copy.deepcopy(inputs["technology_rows"])
            if "FROM company_description" in sql:
                return copy.deepcopy(descriptions)
            if "FROM company c" in sql:
                return copy.deepcopy(companies)
            if "FROM industry_name" in sql:
                return copy.deepcopy(inputs["industry_names"])
            raise AssertionError(sql)

    docs = {}

    def capture(_client, collection, documents, *_args, **_kwargs):
        docs[collection] = documents

    fields = {"location_ids": "location", "occupation_ids": "occupation"}
    with (
        patch("src.sync._ts_bulk_upsert", side_effect=capture),
        patch(
            "src.sync._fetch_facet_counts", side_effect=lambda _client, field: counts[fields[field]]
        ),
        patch("src.sync._fetch_company_posting_counts", return_value=(counts["company"], year)),
        patch(
            "src.sync._fetch_typesense_company_ids", return_value={row["id"] for row in companies}
        ),
    ):
        connection, client = Connection(), MagicMock()
        await sync.sync_locations_typesense(connection, client)
        await sync.sync_occupations_typesense(connection, client)
        await sync.sync_seniority_typesense(connection, client)
        await sync.sync_technologies_typesense(connection, client)
        await sync.sync_companies_typesense(connection, client)
    for documents in docs.values():
        documents.sort(key=lambda document: document["id"])
        for document in documents:
            if "aliases" in document:
                document["aliases"].sort()
    return {
        "input": inputs,
        "companies": companies,
        "descriptions": descriptions,
        "counts": counts,
        "year": year,
        "documents": docs,
    }


if __name__ == "__main__":
    result = (
        json.dumps(
            asyncio.run(generate()), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    )
    destination = Path(__file__).with_name("sync_fixture.json")
    if "--check" in sys.argv:
        assert destination.read_text() == result, "Go sync fixture differs from Python producer"
    else:
        destination.write_text(result)
