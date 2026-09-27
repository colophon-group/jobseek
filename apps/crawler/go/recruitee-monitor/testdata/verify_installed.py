"""Verify installed native parsing against frozen Python outputs, offline."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

rows = json.loads(Path(__file__).with_name("python_parser.json").read_text())
for row in rows:
    p = subprocess.run(
        [sys.argv[1], "--parse-stdin"],
        input=json.dumps(row["body"]),
        text=True,
        capture_output=True,
        timeout=10,
    )
    if row.get("error"):
        assert p.returncode != 0, row["name"]
    else:
        assert p.returncode == 0, (row["name"], p.stderr)
        assert json.loads(p.stdout) == row["expected"], row["name"]
print(f"Installed parser: {len(rows)} Python cases passed offline")
