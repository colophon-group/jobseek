"""Keep Go's local-registry SQL identical during the Python retirement window."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src import sync

NAMES = [
    "_LOCATION_LOOKUP_INDEX_STATE_SQL",
    "_LOCATION_LOOKUP_INDEX_DDL",
    "_UPSERT_OCCUPATION_DOMAINS",
    "_UPSERT_OCCUPATION_DOMAIN_NAMES",
    "_UPSERT_OCCUPATIONS",
    "_UPSERT_OCCUPATION_NAMES",
    "_DELETE_STALE_OCCUPATION_NAMES",
    "_SET_OCCUPATION_PARENTS",
    "_CLEAR_OCCUPATION_PARENTS",
    "_SET_OCCUPATION_DOMAINS",
    "_UPSERT_SENIORITY",
    "_UPSERT_SENIORITY_NAMES",
    "_UPSERT_TECHNOLOGIES",
    "_UPSERT_INDUSTRIES",
    "_UPSERT_INDUSTRY_NAMES",
    "_UPSERT_COMPANIES",
    "_UPSERT_COMPANY_DESCRIPTIONS",
    "_FETCH_BOARD_COMPANY_REHOMES_LOCAL",
    "_REALIGN_RENAMED_BOARD_URLS_LOCAL",
    "_UPSERT_BOARD_LOCAL",
    "_REALIGN_BOARD_POSTING_COMPANIES_LOCAL",
    "_DISABLE_REMOVED_BOARDS_LOCAL",
    "_FETCH_DISABLED_BOARDS_FOR_REDIS_CLEANUP",
]
contract = {name: getattr(sync, name) for name in NAMES}
contract["clear_all_occupation_parents"] = (
    "UPDATE occupation SET parent_id = NULL WHERE parent_id IS NOT NULL"
)
output = json.dumps(contract, indent=2, sort_keys=True) + "\n"
path = Path(__file__).resolve().parents[1] / "registry_sql.json"
if "--check" in sys.argv:
    assert path.read_text() == output, "Go registry SQL contract changed"
else:
    path.write_text(output)
