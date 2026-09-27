"""Exact CSV cells and SQL arguments produced by the Python registry sync."""

from __future__ import annotations

import argparse
import asyncio
import csv
import io
import json
from pathlib import Path

import polars as pl

from src import sync


def csv_text(columns, rows):
    stream = io.StringIO()
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return stream.getvalue()


def fixtures():
    return [
        {
            "name": "all_tables",
            "csv": {
                "occupation_domains": "slug,en,de,fr,it\ntech, Technology ,Technik,,Tecnologia\n",
                "occupations": csv_text(
                    ["slug", "parent", "domain", "en", "de", "fr", "it", "es", "aliases"],
                    [
                        [
                            "engineer",
                            "",
                            "tech",
                            "Engineer",
                            "Ingenieur",
                            "",
                            "",
                            "Ingeniero",
                            " Dev | Developer |",
                        ],
                        ["backend", " engineer ", " tech ", "Backend", "", "", "", "", "Server"],
                    ],
                ),
                "seniority": "slug,en,de,fr,it,aliases\nsenior,Senior,Senior,,,Experienced| Sr \n",
                "technologies": (
                    'slug,name,category\npython,Python,language\nblank,"",""\nmissing,,'
                ),
                "industries": "id,en,de,fr,it\n7,Software, Software ,Logiciel,\n",
                "companies": csv_text(
                    [
                        "slug",
                        "name",
                        "website",
                        "logo_url",
                        "icon_url",
                        "logo_type",
                        "industry",
                        "employee_count_range",
                        "founded_year",
                        "extras",
                    ],
                    [
                        [
                            "acme",
                            "Åcme",
                            "https://acme.test",
                            "",
                            "https://acme.test/icon",
                            "icon",
                            "7",
                            " +2 ",
                            "2_000",
                            '{"x":1}',
                        ],
                        ["other", "Other", "", "", "", "", "invalid", "", "١٩٩٩", "bad-json"],
                    ],
                ),
                "company_descriptions": csv_text(
                    ["slug", "en", "ja"], [["acme", " First\nline ", " 説明 "]]
                ),
            },
        },
        {"name": "clear_parents", "csv": {"occupations": "slug,en,parent\nengineer,Engineer,\n"}},
        {
            "name": "no_names",
            "csv": {
                "occupations": "slug,en,parent\nengineer,,\n",
                "technologies": "slug\ncustom\n",
            },
        },
        {"name": "empty", "csv": {"companies": "slug,name\n", "occupations": "slug,en\n"}},
        {
            "name": "bom_crlf",
            "csv": {"company_descriptions": '\ufeffslug,en,fr\r\nacme," multi\r\nline ",""\r\n'},
        },
    ]


async def generate(data_dir=None):
    contract = json.loads((Path(__file__).resolve().parents[1] / "registry_sql.json").read_text())
    reverse = {value: name for name, value in contract.items()}
    cases = fixtures()
    if data_dir is not None:
        names = [
            "occupation_domains",
            "occupations",
            "seniority",
            "technologies",
            "industries",
            "companies",
            "company_descriptions",
        ]
        cases.append(
            {
                "name": "repository_csv",
                "csv": {
                    name: (Path(data_dir) / f"{name}.csv").read_bytes().decode("utf-8")
                    for name in names
                },
            }
        )
    for case in cases:
        tables = {
            name: pl.read_csv(io.BytesIO(body.encode()), infer_schema_length=0)
            for name, body in case["csv"].items()
        }
        case["tables"] = {
            name: {"columns": df.columns, "rows": df.to_dicts()} for name, df in tables.items()
        }
        case["plan"] = []

        class Recorder:
            def __init__(self, plan):
                self.plan = plan

            async def execute(self, sql, *args):
                self.plan.append({"key": reverse[sql], "args": list(args)})
                return "UPDATE 0"

        connection = Recorder(case["plan"])
        empty = pl.DataFrame()
        await sync.sync_lookup_tables_local(
            connection,
            *(
                tables.get(name, empty)
                for name in [
                    "occupation_domains",
                    "occupations",
                    "seniority",
                    "technologies",
                    "industries",
                ]
            ),
            False,
        )
        await sync.sync_companies(connection, tables.get("companies", empty), False)
        await sync.sync_company_descriptions(
            connection, tables.get("company_descriptions", empty), False
        )
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir")
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).with_name("registry_fixture.json")
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = (
        json.dumps(
            asyncio.run(generate(args.data_dir)), indent=2, sort_keys=True, ensure_ascii=False
        )
        + "\n"
    )
    path = args.out
    if args.check:
        assert path.read_text() == output, "Go registry oracle changed"
    else:
        path.write_text(output)
