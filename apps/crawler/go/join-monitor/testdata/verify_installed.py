"""Exercise the installed native parser; no HTTP client is constructed."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

cases = json.loads(Path(__file__).with_name("python_pages.json").read_text())
for case in cases:
    body = case["fill"] * case["prefix"] + case["html"] + case["fill"] * case["suffix"]
    result = subprocess.run(
        [sys.argv[1], "--parse-page", "--slug", "acme", f"--first={str(case['first']).lower()}"],
        input=body.encode(),
        capture_output=True,
        timeout=15,
    )
    if case.get("expected_error"):
        assert result.returncode != 0, case["name"]
    else:
        assert result.returncode == 0, (case["name"], result.stderr.decode())
        actual = json.loads(result.stdout)
        actual["urls"] = sorted(set(actual["urls"]))
        assert actual == case["expected"], (case["name"], actual, case["expected"])
print(f"{len(cases)} installed JOIN parser cases match Python")
