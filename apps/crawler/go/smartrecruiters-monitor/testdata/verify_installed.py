"""Verify the installed production binary offline against frozen Python outputs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

binary = sys.argv[1]
root = Path(__file__).parent
count = 0
for filename, mode in [("python_inventory.json", "replay"), ("python_detail.json", "parse-detail")]:
    for case in json.loads((root / filename).read_text()):
        if mode == "replay":
            request = {k: case[k] for k in ("board_url", "metadata", "responses")}
        else:
            request = {"posting": case["input"]}
        run = subprocess.run(
            [binary],
            input=json.dumps({"mode": mode, **request}),
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        if case.get("error"):
            assert run.returncode != 0, case["name"]
        else:
            assert run.returncode == 0, (case["name"], run.stderr)
            result = json.loads(run.stdout)
            actual = {k: result[k] for k in case["expected"]}
            assert actual == case["expected"], (case["name"], actual, case["expected"])
        count += 1
print(f"Installed SmartRecruiters binary matches {count} Python monitor/detail cases")
