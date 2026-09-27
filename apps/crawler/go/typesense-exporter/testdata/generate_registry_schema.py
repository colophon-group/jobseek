"""Build the isolated registry database fixture from authoritative migrations."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

root = Path(__file__).resolve().parents[5]
crawler = root / "apps/crawler"
statements = []
for name in [
    "0001_initial_schema",
    "0002_add_company_table",
    "0015_recover_disabled_boards",
    "0016_confirm_board_gone",
]:
    spec = importlib.util.spec_from_file_location(
        name, crawler / f"src/migrations/versions/{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = SimpleNamespace(execute=statements.append)
    module.upgrade()

# Local taxonomy was bootstrapped from these web tables. Include its actual
# table/column contracts, without unrelated web migrations or production data.
for name in [
    "0043_add_company_enrichment",
    "0053_add_occupation",
    "0054_add_seniority",
    "0055_add_industry_name",
    "0056_add_company_description",
    "0058_add_occupation_parent",
    "0060_add_taxonomy_miss",
    "0061_add_occupation_domain",
    "0064_add_technology_name_category",
]:
    raw = (root / f"apps/web/drizzle/{name}.sql").read_text()
    statements.extend(re.findall(r"CREATE TABLE\b.*?;", raw, flags=re.DOTALL))
    statements.extend(re.findall(r"ALTER TABLE (?:occupation|technology) ADD COLUMN\b.*?;", raw))
target = Path(__file__).with_name("registry_schema.json")
output = json.dumps(statements, indent=2) + "\n"
if "--check" in sys.argv:
    assert target.read_text() == output, "registry fixture migration source changed"
else:
    target.write_text(output)
