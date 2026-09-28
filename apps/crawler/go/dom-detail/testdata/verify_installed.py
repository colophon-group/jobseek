"""Verify the installed native extractor offline against frozen Python outputs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

cases = json.loads(Path(__file__).with_name("python_cases.json").read_text())
for index, case in enumerate(cases):
    result = subprocess.run(
        [sys.argv[1]],
        input=json.dumps(case["request"], ensure_ascii=False).encode(),
        capture_output=True,
        timeout=15,
    )
    if "error" in case:
        assert result.returncode, index
    else:
        assert result.returncode == 0, (index, result.stderr.decode())
        actual = json.loads(result.stdout)
        assert actual == case["expected"], (index, actual, case["expected"])
print(f"{len(cases)} installed DOM extractor cases match Python")
