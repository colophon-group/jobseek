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


def optout_file() -> Path:
    """Path to the per-company opt-out list (one slug per line, ``#`` comments).

    Lives at ``apps/crawler/data/labeller_optout.txt`` (tracked, public). Read
    by :func:`upload.push_to_hub` to filter takedown-requested companies out
    of the published HuggingFace dataset.
    """
    from src.shared.constants import get_data_dir

    return get_data_dir() / "labeller_optout.txt"


def prompts_dir() -> Path:
    """Path to the Jinja prompt templates directory inside the source tree."""
    return Path(__file__).parent / "prompts"


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


class PathSandboxError(ValueError):
    """Raised when a path argument escapes ``LABELLER_DATA_ROOT``."""


def assert_under_data_root(path: Path) -> Path:
    """Resolve *path* and confirm it is inside the labeller data root.

    Defense-in-depth against a hijacked orchestrator (or a prompt-injected
    subagent) directing the labeller CLI to read or write outside its
    sandbox (e.g. ``~/.ssh/authorized_keys`` or ``apps/crawler/.env.local``).
    Subagents have an unrestricted ``Write`` tool independently of this
    check, so it is necessary-but-not-sufficient.

    Returns the resolved path on success; raises ``PathSandboxError`` if the
    resolved path is not inside ``LABELLER_DATA_ROOT``. Uses
    ``Path.resolve(strict=False)`` so paths that don't exist yet (output
    files) are accepted as long as their resolved location is in-tree.
    """
    resolved = path.resolve()
    root_resolved = data_root().resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise PathSandboxError(
            f"path {resolved} escapes LABELLER_DATA_ROOT={root_resolved}"
        ) from exc
    return resolved
