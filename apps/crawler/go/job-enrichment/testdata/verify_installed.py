"""Verify the resident installed binary against offline Python taxonomy fixtures."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

cases = json.loads(Path(__file__).with_name("python_taxonomy.json").read_text())
proc = subprocess.Popen(
    [sys.argv[1], "--data-dir", sys.argv[2]],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)
assert proc.stdout and proc.stdin
assert json.loads(proc.stdout.readline()) == {"ready": True, "protocol": 1}
sequence = 0


def request(operation, **fields):
    global sequence
    sequence += 1
    proc.stdin.write(json.dumps({"id": sequence, "operation": operation, **fields}) + "\n")
    proc.stdin.flush()
    result = json.loads(proc.stdout.readline())
    assert result["id"] == sequence and not result.get("error"), result
    return result


try:
    titles = cases["titles"]
    for start in range(0, len(titles), 1000):
        batch = titles[start : start + 1000]
        result = request("occupation_seniority", titles=[c["text"] for c in batch])
        assert result["titles"] == [{k: c[k] for k in ("occupation", "seniority")} for c in batch]
    for case in cases["technologies"]:
        result = request("technology", description=case["text"])
        assert result["technologies"] == case["slugs"], case["text"]
    for signal in cases["intern_signals"]:
        assert request("occupation_seniority", titles=[], employment_type=signal)["intern"]
finally:
    proc.stdin.close()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    proc.stdout.close()
assert proc.returncode == 0
print(
    f"Resident Go enrichment matches {len(titles)} title "
    f"and {len(cases['technologies'])} technology cases"
)
