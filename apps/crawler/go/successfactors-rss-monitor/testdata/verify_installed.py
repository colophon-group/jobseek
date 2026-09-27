"""Verify the installed native parser against frozen Python output, offline."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

rows = json.loads(Path(__file__).with_name("python_parser.json").read_text())
for index, row in enumerate(rows):
    result = subprocess.run(
        [sys.argv[1], "--parse-stdin"],
        input=row["feed"],
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    actual = json.loads(result.stdout)
    assert actual == {"jobs": row["jobs"], "items": row["items"]}, f"parser case {index}"
print(f"Installed SuccessFactors parser: {len(rows)} Python cases passed offline")
