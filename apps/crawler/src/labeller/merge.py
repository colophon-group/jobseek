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


def _granular_mode_sections(
    base: Path, split_sections: list[dict], *, allow_missing: bool = False
) -> tuple[list[dict], list[str]]:
    """Build the per-section extracted view from per-kind output files.

    When ``allow_missing`` is False (the accepted-merge default), any missing
    per-kind ``extract-<kind>-out.json`` raises ``FileNotFoundError`` — we
    refuse to silently merge with null extractions.

    When ``allow_missing`` is True (rejected-merge fallback), missing or
    malformed files leave ``extracted=None`` for that section and are
    reported back to the caller so the qa_rationale can name them. A
    truncated file from a crashed subagent is treated the same as an
    absent one — the partial-pipeline-failure escape valve has to handle
    both, otherwise it would surface the same crash it's meant to recover.
    """
    sections_with_extracts: list[dict] = []
    missing: list[str] = []
    for sec in split_sections:
        kind = sec["kind"]
        entry: dict = {"kind": kind, "block_ids": list(sec["block_ids"])}
        if kind in SECTION_EXTRACT_KINDS:
            extract_path = base / f"extract-{kind}-out.json"
            extract_data = _read_json_tolerant(extract_path, allow_missing=allow_missing)
            if extract_data is None:
                missing.append(f"extract-{kind}-out.json")
                entry["extracted"] = None
            else:
                entry["extracted"] = extract_data
        else:
            entry["extracted"] = None
        sections_with_extracts.append(entry)
    if missing and not allow_missing:
        raise FileNotFoundError(
            f"granular merge: missing required extract files: {', '.join(missing)}"
        )
    return sections_with_extracts, missing


def _read_json_tolerant(path: Path, *, allow_missing: bool) -> dict | None:
    """Read a JSON file. Strict mode raises on missing/malformed.

    Tolerant mode (``allow_missing=True``) returns ``None`` for both an
    absent file and a malformed one — a crashed subagent that left a
    truncated JSON file should be treated the same as one that didn't
    write the file at all on the rejected-merge fallback path.
    """
    if not path.exists():
        if allow_missing:
            return None
        raise FileNotFoundError(path)
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        if allow_missing:
            return None
        raise


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

    When ``qa_verdict='rejected'``, missing per-kind / globals / split files
    are tolerated: empty shells are synthesized and the names of the missing
    files are appended to ``qa_rationale``. This lets the orchestrator
    persist a posting record even after a partial pipeline failure (the
    record still passes posting.schema.json validation but won't pass QA).
    For any other verdict, missing files raise ``FileNotFoundError``.
    """
    base = runs_dir(run_date, posting_id)
    input_data = json.loads((base / "input.json").read_text())
    is_rejected = qa_verdict == "rejected"
    fallback_missing: list[str] = []

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
        sections_payload = _read_json_tolerant(base / "split-out.json", allow_missing=is_rejected)
        globals_payload = _read_json_tolerant(base / "globals-out.json", allow_missing=is_rejected)
        if sections_payload is None:
            fallback_missing.append("split-out.json")
            sections_payload = {"sections": []}
        if globals_payload is None:
            fallback_missing.append("globals-out.json")
            globals_payload = {}
        sections_with_extracts, missing_extracts = _granular_mode_sections(
            base,
            sections_payload.get("sections", []),
            allow_missing=is_rejected,
        )
        fallback_missing.extend(missing_extracts)
        globals_data = globals_payload

    if is_rejected and fallback_missing:
        note = "partial pipeline failure: missing " + ", ".join(fallback_missing)
        qa_rationale = f"{qa_rationale}; {note}" if qa_rationale else note

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
