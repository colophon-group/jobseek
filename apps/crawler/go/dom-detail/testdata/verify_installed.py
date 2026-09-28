"""Verify the installed native extractor offline against frozen Python outputs."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

cases = json.loads(Path(__file__).with_name("python_cases.json").read_text())
cases += json.loads(Path(__file__).with_name("python_render_policy.json").read_text())
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

# The second installed binary owns direct HTTP. Literal private targets must
# fail before any origin response, even in an otherwise networkless container.
if len(sys.argv) > 2:
    for url in (
        "http://127.0.0.1/job",
        "http://10.0.0.5/job",
        "http://169.254.169.254/job",
        "http://[::1]/job",
    ):
        result = subprocess.run(
            [sys.argv[2]],
            input=json.dumps({"url": url, "options": {}}).encode(),
            capture_output=True,
            timeout=15,
        )
        assert result.returncode == 1, url
        payload = json.loads(result.stdout)
        assert payload["responses"] == 0 and payload["bytes"] == 0 and payload["error"], url
    print("4 installed DOM HTTP private-target cases rejected before origin response")
