"""Keep Go's offline evidence fixture tied to the retained Python oracle."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_go_taxonomy_contract_and_fixture_match_python() -> None:
    crawler = Path(__file__).resolve().parents[1]
    script = crawler / "go/typesense-exporter/testdata/generate_taxonomy_fixture.py"
    subprocess.run([sys.executable, str(script), "--check"], cwd=crawler, check=True)
