"""Run the installed parser on frozen HTML, without opening any origin connection."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

cases = json.loads(Path(__file__).with_name("python_cases.json").read_text())
for case in cases:
    result = subprocess.run(
        [sys.argv[1], "--parse"],
        input=json.dumps(case["request"], ensure_ascii=False).encode(),
        capture_output=True,
        timeout=15,
    )
    if case.get("expected_error"):
        assert result.returncode, case["name"]
    else:
        assert result.returncode == 0, (case["name"], result.stderr.decode())
        actual = json.loads(result.stdout)
        assert actual == case["expected"], (case["name"], actual, case["expected"])
print(f"{len(cases)} installed JSON-LD parser cases match Python")
