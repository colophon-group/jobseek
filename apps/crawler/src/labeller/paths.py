"""File-system conventions for the labeller pipeline.

All paths are rooted at the crawler CWD (``apps/crawler`` when invoked via
``labeller``). The default data root is ``data/postings-labelled`` but can
be overridden via the ``LABELLER_DATA_ROOT`` env var for tests / scratch
runs.

Layout is intentionally flat:

    data/postings-labelled/
      _runs/{{date}}/{{id}}/    # per-posting intermediates (Jinja renders + subagent outputs)
      postings/{{date}}/{{id}}.json   # final gold; `labelling_meta.qa_verdict` is the status
      schemas/                        # staged copies uploaded to HF
      README.md
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ROOT = Path("data/postings-labelled")


def data_root() -> Path:
    override = os.environ.get("LABELLER_DATA_ROOT")
    if override:
        return Path(override)
    return _DEFAULT_ROOT


def runs_dir(run_date: str, posting_id: str) -> Path:
    return data_root() / "_runs" / run_date / posting_id


def postings_dir(run_date: str) -> Path:
    """Directory for final per-posting gold records. Verdict is a field on each file."""
    return data_root() / "postings" / run_date


def schemas_dir() -> Path:
    """Path to the canonical JSON Schemas directory inside the source tree."""
    return Path(__file__).parent / "schemas"


def prompts_dir() -> Path:
    """Path to the Jinja prompt templates directory inside the source tree."""
    return Path(__file__).parent / "prompts"


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path
