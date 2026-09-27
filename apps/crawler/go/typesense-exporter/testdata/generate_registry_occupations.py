"""Freeze actual Python occupation resolution for Go's transaction port."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import polars as pl

from src.core import occupation_resolve as resolver

parser = argparse.ArgumentParser()
parser.add_argument("--csv", type=Path)
parser.add_argument("--out", type=Path)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
synthetic = """slug,parent,domain,en,fr,de,sv,aliases
software-engineer,,,Software Engineer,Développeur,Softwareentwickler,Utvecklare,Software Developer
engineer,,,Engineer,Ingénieur,Ingenieur,,
technician,,,Technician,Technicien,Techniker,,Technicien/ne
supervisor,,,Supervisor,Superviseur,Leiter,,Superviseur/euse
manager,,,Health and Safety Manager,,,,Health Safety Environment Manager
data-manager,,,Data Center Manager,,,,
first,,,Collision,,,Σίσυφος,Shared Alias|a b c d
second,,,Collision,,,,Shared Alias|d c b a
boundary,,,Sales,,,Säljare,
"""
body = args.csv.read_text() if args.csv else synthetic
table = pl.read_csv(body.encode(), infer_schema_length=0)
with tempfile.TemporaryDirectory() as directory:
    (Path(directory) / "occupations.csv").write_text(body)
    resolver.get_data_dir = lambda: Path(directory)
    resolver._load_aliases.cache_clear()
    resolver._load_token_aliases.cache_clear()
    values = {"", "unmatched occupation", "Superviseur/euse", "Technicien/ne", "preSalespost"}
    for alias in resolver._load_aliases():
        values.update(
            [
                alias,
                f"Senior {alias} (m/f/d)",
                f"{alias.upper()} (H/F/X)",
                f"prefix({alias})suffix",
                " ".join(reversed(alias.split())),
                f"pre{alias}post",
            ]
        )
    output = {
        "table": {"columns": table.columns, "rows": table.to_dicts()},
        "samples": [
            {
                "raw": raw,
                "normalized": resolver._normalize(raw),
                "slug": resolver.match_occupation(raw),
            }
            for raw in sorted(values)
        ],
    }
target = args.out or Path(__file__).with_name("registry_occupation_fixture.json")
encoded = json.dumps(output, indent=2, ensure_ascii=False) + "\n"
if args.check:
    assert target.read_text() == encoded, "registry occupation fixture changed"
else:
    target.write_text(encoded)
