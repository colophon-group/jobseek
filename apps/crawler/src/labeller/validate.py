"""JSON Schema + custom validation for labeller outputs.

Every subagent output is validated before the orchestrator uses it. Schema
checks come from ``schemas/*.json``; custom rules live here (block-ID
coverage, non-overlap, contiguity, skill-category closed set, etc.).

CLI: ``labeller validate --kind <kind> --file <out.json> [--context <input.json>]``.
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
        "company",
        "team",
        "role",
        "requirements",
        "preferred",
        "benefits",
        "application",
        "globals",
        "posting",
    }
)

SECTION_EXTRACT_KINDS: frozenset[str] = frozenset(
    {"company", "team", "role", "requirements", "preferred", "benefits", "application"}
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


def validate_file(kind: str, file_path: Path, context_path: Path | None = None) -> list[str]:
    """Validate a subagent output file. Returns list of errors (empty = valid)."""
    if not file_path.exists():
        return [f"output file does not exist: {file_path}"]
    try:
        data = json.loads(file_path.read_text())
    except json.JSONDecodeError as e:
        return [f"output is not valid JSON: {e}"]

    if isinstance(data, dict) and "error" in data and len(data) == 1:
        return [f"subagent reported an error: {data['error']}"]

    errors = validate_schema(kind, data)

    if kind == "sections" and context_path and context_path.exists():
        try:
            ctx = json.loads(context_path.read_text())
            block_ids = {b["id"] for b in ctx.get("input", {}).get("blocks", [])}
        except (json.JSONDecodeError, KeyError):
            errors.append(f"could not load block context from {context_path}")
        else:
            errors.extend(validate_sections_custom(data, block_ids=block_ids))

    return errors
