"""JSON Schema + custom validation for labeller outputs.

Every subagent output is validated before the orchestrator uses it. Schema
checks come from ``schemas/*.json``; custom rules live here (block-ID
coverage, non-overlap, contiguity, skill-category closed set, QA rules).

CLI: ``labeller validate --kind <kind> --file <out.json> [--context <input.json>]``.

Kinds:
    sections | team | role | requirements | preferred | benefits
    globals | posting | qa
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import jsonschema

from .paths import schemas_dir

KNOWN_KINDS: frozenset[str] = frozenset(
    {
        "sections",
        "team",
        "role",
        "requirements",
        "preferred",
        "benefits",
        "globals",
        "posting",
        "qa",
        "extract_all",
    }
)

SECTION_EXTRACT_KINDS: frozenset[str] = frozenset(
    {"team", "role", "requirements", "preferred", "benefits"}
)


class ValidationError(Exception):
    """Raised when a labeller output fails validation."""

    def __init__(self, messages: list[str]) -> None:
        self.messages = messages
        super().__init__("\n".join(messages))


@lru_cache(maxsize=32)
def _load_schema(kind: str) -> dict:
    root = schemas_dir()
    if kind == "sections":
        path = root / "sections.schema.json"
    elif kind == "globals":
        path = root / "globals.schema.json"
    elif kind == "posting":
        path = root / "posting.schema.json"
    elif kind == "qa":
        path = root / "qa.schema.json"
    elif kind == "extract_all":
        path = root / "extract_all.schema.json"
    elif kind in SECTION_EXTRACT_KINDS:
        path = root / "section_extract" / f"{kind}.schema.json"
    else:
        raise ValueError(f"unknown validation kind: {kind}")
    return json.loads(path.read_text())


def validate_schema(kind: str, data: dict) -> list[str]:
    """Run JSON-Schema validation. Returns a list of human-readable errors."""
    schema = _load_schema(kind)
    validator = jsonschema.Draft202012Validator(schema)
    return [
        f"{'.'.join(str(p) for p in err.absolute_path) or '(root)'}: {err.message}"
        for err in validator.iter_errors(data)
    ]


def validate_sections_custom(data: dict, *, block_ids: set[int]) -> list[str]:
    """Custom checks for the section-splitter output.

    - Every block_id references an existing block
    - block_ids per section are contiguous ascending integers
    - No block_id appears in more than one section
    """
    errors: list[str] = []
    seen: set[int] = set()
    for i, sec in enumerate(data.get("sections", [])):
        kind = sec.get("kind", "?")
        ids = sec.get("block_ids", [])
        for bid in ids:
            if bid not in block_ids:
                errors.append(
                    f"sections[{i}] (kind={kind}): block_id {bid} does not exist in input.blocks"
                )
        if len(ids) >= 2 and any(ids[j + 1] != ids[j] + 1 for j in range(len(ids) - 1)):
            errors.append(
                f"sections[{i}] (kind={kind}): block_ids must be contiguous ascending, got {ids}"
            )
        for bid in ids:
            if bid in seen:
                errors.append(
                    f"sections[{i}] (kind={kind}): block_id {bid}"
                    f" already appears in a prior section"
                )
            seen.add(bid)
    return errors


def run_qa_rules(posting: dict) -> list[dict]:
    """Run concrete QA rules against a merged posting. Returns rule results.

    Accepted iff every returned rule has ``passed: true``. Rules are plain
    heuristics intended to catch obviously-broken labelling runs (empty
    globals, missing extractions, very low split coverage). They are NOT
    sophisticated — this is a gatekeeper, not a judge.
    """
    rules: list[dict] = []
    labels = posting.get("labels") or {}
    sections: list[dict] = labels.get("sections") or []
    globals_: dict = labels.get("globals") or {}
    blocks: list[dict] = (posting.get("input") or {}).get("blocks") or []

    # Rule 1: split coverage >= 40% of blocks (catches pathological splits)
    n_blocks = len(blocks)
    claimed = sum(len(s.get("block_ids", [])) for s in sections)
    coverage = claimed / n_blocks if n_blocks else 0.0
    rules.append(
        {
            "name": "split_coverage_min_40pct",
            "passed": n_blocks == 0 or coverage >= 0.40,
            "detail": f"{claimed}/{n_blocks} blocks claimed ({coverage:.0%})",
        }
    )

    # Rule 2: globals.profession non-null and non-empty
    prof = globals_.get("profession")
    rules.append(
        {
            "name": "profession_non_empty",
            "passed": bool(prof and str(prof).strip()),
            "detail": repr(prof),
        }
    )

    # Rule 3: globals.employment_type non-null
    et = globals_.get("employment_type")
    rules.append({"name": "employment_type_non_null", "passed": et is not None, "detail": repr(et)})

    # Rule 4: at least one extractable section
    kinds_present = {s.get("kind") for s in sections}
    extractable = SECTION_EXTRACT_KINDS & kinds_present
    rules.append(
        {
            "name": "has_extractable_section",
            "passed": bool(extractable),
            "detail": f"extractable sections: {sorted(extractable)}",
        }
    )

    # Rule 5: every extractable section present has non-null extracted
    for s in sections:
        k = s.get("kind")
        if k in SECTION_EXTRACT_KINDS:
            rules.append(
                {
                    "name": f"section_{k}_has_extraction",
                    "passed": s.get("extracted") is not None,
                    "detail": None if s.get("extracted") is not None else "extracted is null",
                }
            )

    # Rule 6: role sections (if any) have at least one responsibility in TOTAL.
    # The splitter often emits multiple non-contiguous role entries (intro +
    # main), and the intro entry naturally has 0 bullets. Summing across all
    # role sections is the sensible semantic: "this posting has some role
    # content labelled" regardless of how many sections were emitted.
    role_secs = [s for s in sections if s.get("kind") == "role" and s.get("extracted")]
    if role_secs:
        total_resp = sum(len(s["extracted"].get("responsibilities") or []) for s in role_secs)
        rules.append(
            {
                "name": "role_responsibilities_non_empty",
                "passed": total_resp >= 1,
                "detail": (
                    f"{total_resp} responsibility/ies across {len(role_secs)} role section(s)"
                ),
            }
        )

    # Rule 7: requirements section (if any) has at least one signal. Same
    # across-all-sections semantics: sum skills / OR any education / OR any
    # years_min across every requirements entry.
    req_secs = [s for s in sections if s.get("kind") == "requirements" and s.get("extracted")]
    if req_secs:
        has_signal = any(
            (s["extracted"].get("required_skills"))
            or s["extracted"].get("education_level")
            or s["extracted"].get("years_experience_min") is not None
            for s in req_secs
        )
        rules.append({"name": "requirements_has_signal", "passed": has_signal, "detail": None})

    return rules


def qa_report(posting: dict) -> dict:
    """Produce the qa-schema-shaped report for a merged posting."""
    rules = run_qa_rules(posting)
    verdict = "accepted" if all(r["passed"] for r in rules) else "rejected"
    return {"posting_id": posting.get("id", "?"), "verdict": verdict, "rules": rules}


def _validate_extract_all_custom(data: dict) -> list[str]:
    """Validate each section's ``extracted`` against its per-kind schema,
    plus the ``globals`` block against globals.schema.json."""
    errors: list[str] = []
    for i, sec in enumerate(data.get("sections") or []):
        kind = sec.get("kind")
        extracted = sec.get("extracted")
        if kind in SECTION_EXTRACT_KINDS:
            if not isinstance(extracted, dict):
                errors.append(
                    f"sections[{i}] (kind={kind}): `extracted` must be an object "
                    f"(extractable kind cannot be null)"
                )
                continue
            sub_errors = validate_schema(kind, extracted)
            for e in sub_errors:
                errors.append(f"sections[{i}].extracted.{e}")
        elif kind in {"company", "application"}:
            if extracted is not None:
                errors.append(
                    f"sections[{i}] (kind={kind}): `extracted` must be null for"
                    f" company/application sections"
                )
    globals_block = data.get("globals")
    if not isinstance(globals_block, dict):
        errors.append("globals: must be an object")
    else:
        for e in validate_schema("globals", globals_block):
            errors.append(f"globals.{e}")
    return errors


def validate_file(kind: str, file_path: Path, context_path: Path | None = None) -> list[str]:
    """Validate a subagent output file. Returns list of errors (empty = valid).

    For ``kind == "qa"``, the ``file_path`` is a *merged posting* (not a
    pre-built QA report). The QA rules are run and any failing rules are
    returned as error strings.
    """
    if not file_path.exists():
        return [f"output file does not exist: {file_path}"]
    try:
        data = json.loads(file_path.read_text())
    except json.JSONDecodeError as e:
        return [f"output is not valid JSON: {e}"]

    if isinstance(data, dict) and "error" in data and len(data) == 1:
        return [f"subagent reported an error: {data['error']}"]

    if kind == "qa":
        errors: list[str] = []
        for rule in run_qa_rules(data):
            if not rule["passed"]:
                detail = f" — {rule['detail']}" if rule.get("detail") else ""
                errors.append(f"qa.{rule['name']} failed{detail}")
        return errors

    errors = validate_schema(kind, data)

    if kind == "sections" and context_path and context_path.exists():
        try:
            ctx = json.loads(context_path.read_text())
            block_ids = {b["id"] for b in ctx.get("input", {}).get("blocks", [])}
        except (json.JSONDecodeError, KeyError):
            errors.append(f"could not load block context from {context_path}")
        else:
            errors.extend(validate_sections_custom(data, block_ids=block_ids))

    if kind == "extract_all":
        errors.extend(_validate_extract_all_custom(data))
        if context_path and context_path.exists():
            try:
                ctx = json.loads(context_path.read_text())
                block_ids = {b["id"] for b in ctx.get("input", {}).get("blocks", [])}
            except (json.JSONDecodeError, KeyError):
                errors.append(f"could not load block context from {context_path}")
            else:
                # Re-use the splitter's custom rules (contiguity, overlap, existence)
                errors.extend(validate_sections_custom(data, block_ids=block_ids))

    return errors
