from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_enrichment_docs_do_not_advertise_missing_enricher_script() -> None:
    pyproject = tomllib.loads((ROOT / "apps/crawler/pyproject.toml").read_text())
    scripts = pyproject["project"]["scripts"]
    if "enricher" in scripts:
        return

    docs = [
        ROOT / "docs/09-enrichment.md",
        ROOT / "docs/17-codex-migration-verification-runbook.md",
    ]
    offending_lines: list[str] = []
    for doc in docs:
        for line_number, line in enumerate(doc.read_text().splitlines(), start=1):
            if line.strip().startswith("uv run enricher"):
                offending_lines.append(f"{doc.relative_to(ROOT)}:{line_number}: {line.strip()}")

    assert offending_lines == []
