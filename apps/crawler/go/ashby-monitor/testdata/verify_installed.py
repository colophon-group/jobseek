"""Check installed Go request construction against frozen Python/httpx endpoints."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

cases = json.loads(Path(__file__).with_name("python_endpoints.json").read_text())
for case in cases:
    result = subprocess.run(
        [sys.argv[1], "--resolve-only", "--token", case["token"]],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.stdout.strip() == case["endpoint"], case["board_id"]
for token in ("../escape", "a/b", "a%2Fb", "a?query=1", "a#fragment", "acme "):
    result = subprocess.run(
        [sys.argv[1], "--resolve-only", "--token", token],
        capture_output=True,
        timeout=5,
    )
    assert result.returncode != 0 and not result.stdout, token
print(f"Verified {len(cases)} installed Ashby request endpoints and six rejected tokens offline")
