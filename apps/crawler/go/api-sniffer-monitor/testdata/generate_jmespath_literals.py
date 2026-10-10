"""Refresh frozen input/output pairs through the original Python JMESPath runtime."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import jmespath

warnings.simplefilter("ignore", DeprecationWarning)
path = Path(__file__).with_name("python_jmespath_literals.json")
rows = json.loads(path.read_text())
for row in rows:
    row.pop("result", None)
    try:
        row["result"] = jmespath.search(row["query"], row["data"])
        row["failed"] = False
    except Exception:
        row["failed"] = True
path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
