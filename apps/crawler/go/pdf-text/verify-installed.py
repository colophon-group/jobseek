"""Verify the installed native extractor without HTTP or runtime credentials."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

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
        ["/usr/local/bin/go-pdf-text"],
        input=base64.b64decode(case["body"], validate=True),
        capture_output=True,
        timeout=30,
        check=True,
    )
    assert result.stdout.decode() == expected[case["name"]], case["name"]
for body in (b"not PDF", b"%PDF broken"):
    result = subprocess.run(
        ["/usr/local/bin/go-pdf-text"], input=body, capture_output=True, timeout=30
    )
    assert result.returncode != 0 and not result.stdout
print("Installed native PDF text: seven reference documents and malformed inputs pass")
