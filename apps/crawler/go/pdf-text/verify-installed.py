"""Verify the installed native extractor without HTTP or runtime credentials."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

command = ["/usr/local/bin/go-pdf-text"]
if len(sys.argv) == 4 and sys.argv[2] == "--container-image":
    # CI runs this verifier outside the Python-free image. Only the native
    # extractor and its document bytes enter the container.
    command = [
        "docker",
        "run",
        "--rm",
        "--read-only",
        "--network",
        "none",
        "--interactive",
        "--entrypoint",
        command[0],
        sys.argv[3],
    ]
elif len(sys.argv) != 2:
    raise SystemExit("usage: verify-installed.py FIXTURE [--container-image IMAGE]")

cases = json.loads(Path(sys.argv[1]).read_text())
expected = {
    "text": "Software Engineer in Zurich",
    "url": "Build reliable software",
    "blank": "",
    "required-failure": "No matching title",
    "ocr": "",
    "ocr-page-limit": "",
    "ocr-pixel-limit": "",
}
assert {case["name"] for case in cases} == set(expected)
for case in cases:
    result = subprocess.run(
        command,
        input=base64.b64decode(case["body"], validate=True),
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.decode() == expected[case["name"]], case["name"]
for body in (b"not PDF", b"%PDF broken"):
    result = subprocess.run(command, input=body, capture_output=True, timeout=30)
    assert result.returncode != 0 and not result.stdout
print("Installed native PDF text: seven reference documents and malformed inputs pass")
