"""CI gate: src/workspace/lib/ must not import CLI-only modules.

Mirrors apps/crawler/scripts/grep-lib-purity.sh as a pytest test so the
gate runs on every PR locally and in CI without a separate workflow step.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "src" / "workspace" / "lib"

FORBIDDEN_PATTERNS = (
    re.compile(r"^\s*from\s+src\.workspace\.commands"),
    re.compile(r"^\s*import\s+src\.workspace\.commands"),
    re.compile(r"^\s*from\s+src\.workspace\.cli"),
    re.compile(r"^\s*import\s+src\.workspace\.cli"),
    re.compile(r"^\s*from\s+src\.workspace\s+import\s+output"),
    re.compile(r"^\s*from\s+src\.workspace\.output"),
    re.compile(r"^\s*import\s+src\.workspace\.output"),
)


def test_lib_dir_exists():
    assert LIB_DIR.is_dir(), f"lib dir not found: {LIB_DIR}"


def test_no_forbidden_imports():
    """Inspect each .py file in lib/ for forbidden imports."""
    violations: list[str] = []
    for py in LIB_DIR.rglob("*.py"):
        for lineno, line in enumerate(py.read_text().splitlines(), 1):
            for pat in FORBIDDEN_PATTERNS:
                if pat.match(line):
                    violations.append(f"{py.relative_to(LIB_DIR)}:{lineno}: {line.rstrip()}")
    assert not violations, "Forbidden imports in lib/:\n" + "\n".join(violations)


def test_grep_script_passes():
    """Running the shell script directly must succeed."""
    # tests/test_lib_purity.py → apps/crawler/scripts/grep-lib-purity.sh
    crawler_root = Path(__file__).resolve().parent.parent
    script = crawler_root / "scripts" / "grep-lib-purity.sh"
    assert script.exists(), f"script not found: {script}"
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, (
        f"grep-lib-purity.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
