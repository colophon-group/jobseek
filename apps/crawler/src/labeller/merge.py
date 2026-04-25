"""Assemble the full labelled-posting record from per-subagent outputs.

Two input paths are supported:

- **Merged mode** (preferred, fewer Sonnet calls): reads ``extract-all-out.json``,
  which carries sections + per-section extracted + globals in one document.
- **Granular mode** (legacy fallback): reads ``extract-<kind>-out.json`` for
  every extractable kind the splitter identified plus ``globals-out.json``.

If both exist, the merged file wins. Either way raises ``FileNotFoundError`` if
the expected inputs are missing — never merge silently with null extractions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .paths import runs_dir
from .validate import SECTION_EXTRACT_KINDS


def _merged_mode(base: Path) -> dict | None:
    path = base / "extract-all-out.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _granular_mode_sections(base: Path, split_sections: list[dict]) -> list[dict]:
    sections_with_extracts: list[dict] = []
    missing: list[str] = []
    for sec in split_sections:
        kind = sec["kind"]
        entry = {"kind": kind, "block_ids": list(sec["block_ids"])}
        if kind in SECTION_EXTRACT_KINDS:
            extract_path = base / f"extract-{kind}-out.json"
            if not extract_path.exists():
                missing.append(f"extract-{kind}-out.json")
            else:
                entry["extracted"] = json.loads(extract_path.read_text())
        else:
            entry["extracted"] = None
        sections_with_extracts.append(entry)
    if missing:
        raise FileNotFoundError(
            f"granular merge: missing required extract files: {', '.join(missing)}"
        )
    return sections_with_extracts


def merge_posting(
    run_date: str,
    posting_id: str,
    *,
    qa_verdict: str = "accepted",
    qa_rationale: str | None = None,
    retries: dict[str, int] | None = None,
) -> dict:
    """Assemble the merged posting.json. Returns the dict (caller writes it).

    Prefers ``extract-all-out.json`` (merged mode); falls back to the per-kind
    + globals file layout (granular mode) when the merged file is absent.
    """
    base = runs_dir(run_date, posting_id)
    input_data = json.loads((base / "input.json").read_text())

    merged_payload = _merged_mode(base)
    if merged_payload is not None:
        sections_with_extracts = [
            {
                "kind": sec["kind"],
                "block_ids": list(sec["block_ids"]),
                "extracted": sec.get("extracted"),
            }
            for sec in merged_payload.get("sections", [])
        ]
        globals_data = merged_payload.get("globals", {})
    else:
        sections_data = json.loads((base / "split-out.json").read_text())
        globals_data = json.loads((base / "globals-out.json").read_text())
        sections_with_extracts = _granular_mode_sections(base, sections_data.get("sections", []))

    return {
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


def write_merged(target: Path, merged: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, indent=2, ensure_ascii=False, default=str))
