from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_reaper_script_and_python_oracle_are_current():
    directory = ROOT / "go/typesense-exporter"
    assert (directory / "lease_reaper.lua").read_bytes() == (
        ROOT / "src/lua/reap_expired.lua"
    ).read_bytes()
    subprocess.run(
        [sys.executable, str(directory / "testdata/generate_reaper_fixture.py"), "--check"],
        check=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )
