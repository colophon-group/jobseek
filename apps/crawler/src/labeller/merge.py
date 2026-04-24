"""Assemble the full labelled-posting record from per-subagent outputs.

Reads input.json + split-out.json + extract-<kind>-out.json (for every
kind the splitter identified) + globals-out.json, produces the merged
posting.json matching ``schemas/posting.schema.json``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .paths import runs_dir
from .validate import SECTION_EXTRACT_KINDS


def merge_posting(
    run_date: str,
    posting_id: str,
    *,
    qa_verdict: str = "accepted",
    qa_rationale: str | None = None,
    retries: dict[str, int] | None = None,
) -> dict:
    """Assemble the merged posting.json. Returns the dict (caller writes it).

    Expects the following files under ``runs_dir(run_date, posting_id)``:
      input.json
      split-out.json
      extract-<kind>-out.json   (for each kind in sections)
      globals-out.json
    """
    base = runs_dir(run_date, posting_id)
    input_data = json.loads((base / "input.json").read_text())
    sections_data = json.loads((base / "split-out.json").read_text())
    globals_data = json.loads((base / "globals-out.json").read_text())

    sections_with_extracts = []
    for sec in sections_data.get("sections", []):
        kind = sec["kind"]
        entry = {"kind": kind, "block_ids": list(sec["block_ids"])}
        if kind in SECTION_EXTRACT_KINDS:
            extract_path = base / f"extract-{kind}-out.json"
            if extract_path.exists():
                entry["extracted"] = json.loads(extract_path.read_text())
            else:
                entry["extracted"] = None
        else:
            entry["extracted"] = None
        sections_with_extracts.append(entry)

    merged: dict = {
        "id": input_data["id"],
        "schema_version": input_data.get("schema_version", 1),
        "crawler_version": input_data.get("crawler_version"),
        "normalizer_version": input_data["normalizer_version"],
        "sampled_at": input_data["sampled_at"],
        "labelled_at": datetime.now(tz=UTC).isoformat(),
        "source": input_data["source"],
        "input": input_data["input"],
        "labels": {
            "sections": sections_with_extracts,
            "globals": globals_data,
        },
        "labelling_meta": {
            "qa_verdict": qa_verdict,
            "qa_rationale": qa_rationale,
            "retries": retries or {},
        },
    }
    return merged


def write_merged(run_date: str, posting_id: str, merged: dict, *, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, indent=2, ensure_ascii=False, default=str))
