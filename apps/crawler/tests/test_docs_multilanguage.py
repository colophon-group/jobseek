"""Regression checks for the archived multi-language posting docs."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_multilanguage_plan_is_archived_not_action_plan() -> None:
    doc = _read("docs/multi-language-job-postings.md")
    adr = _read("docs/adr/001-multi-language-job-postings.md")

    assert "Status:** Archived" in doc
    assert "ADR-001: Multi-Language Job Postings" in doc
    assert "Status: implemented" in adr

    for text in (doc, adr):
        assert "Action Plan:" not in text
        assert not re.search(r"^- \[[ x]\]", text, flags=re.MULTILINE)


def test_multilanguage_docs_reference_current_detector() -> None:
    docs = "\n".join(
        [
            _read("docs/multi-language-job-postings.md"),
            _read("docs/adr/001-multi-language-job-postings.md"),
            _read("docs/08-job-data-fields.md"),
        ]
    )

    assert "fast-langdetect" in docs
    assert "apps/crawler/src/shared/langdetect.py" in docs

    stale_terms = [
        "lingua-py",
        "lingua-language-detector",
        "LanguageDetectorBuilder",
        "from lingua import",
        "18 European",
        "job_posting.localizations",
    ]
    for term in stale_terms:
        assert term not in docs
