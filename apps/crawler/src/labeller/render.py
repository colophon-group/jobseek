"""Jinja-based renderer for subagent task-input files.

The orchestrator calls ``labeller render-task --task <name> ...`` once per
subagent invocation. The rendered markdown is what the subagent reads;
it contains rules + variables interpolated from the posting's data.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .paths import prompts_dir
from .validate import SECTION_EXTRACT_KINDS

TASKS: frozenset[str] = frozenset(
    {
        "split_sections",
        "extract_company",
        "extract_team",
        "extract_role",
        "extract_requirements",
        "extract_preferred",
        "extract_benefits",
        "extract_application",
        "extract_globals",
    }
)


@lru_cache(maxsize=1)
def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(prompts_dir()),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )


def render_task(
    task: str,
    *,
    input_data: dict,
    sections_data: dict | None = None,
    kind: str | None = None,
    output_path: str,
    previous_error: str | None = None,
) -> str:
    """Render a task template. Returns the markdown string."""
    if task not in TASKS:
        raise ValueError(f"unknown task: {task} (known: {sorted(TASKS)})")

    env = _env()
    template = env.get_template(f"tasks/{task}.md.j2")

    ctx = {
        "title_raw": input_data["input"]["title_raw"],
        "output_path": output_path,
        "previous_error": previous_error,
    }

    if task == "split_sections":
        ctx["blocks"] = input_data["input"]["blocks"]
    elif task.startswith("extract_") and task != "extract_globals":
        section_kind = kind or task.removeprefix("extract_")
        if section_kind not in SECTION_EXTRACT_KINDS:
            raise ValueError(f"task {task} requires a valid section kind, got {section_kind}")
        if sections_data is None:
            raise ValueError(f"task {task} requires --sections")
        blocks_by_id = {b["id"]: b for b in input_data["input"]["blocks"]}
        matched = _blocks_for_kind(sections_data, section_kind, blocks_by_id)
        ctx["section_blocks"] = matched
    elif task == "extract_globals":
        if sections_data is None:
            raise ValueError("extract_globals requires --sections")
        blocks_by_id = {b["id"]: b for b in input_data["input"]["blocks"]}
        header = _header_blocks(sections_data, blocks_by_id)
        ctx["header_blocks"] = header
        ctx["description_locale_detected"] = input_data["input"].get("description_locale_detected")
        # Per-section outputs are optional; if the orchestrator hasn't passed them,
        # use an empty object. In the steady-state, orchestrator injects them.
        ctx["section_outputs_json"] = json.dumps(
            sections_data.get("_section_outputs", {}), indent=2, ensure_ascii=False
        )

    return template.render(**ctx)


def _blocks_for_kind(sections_data: dict, kind: str, blocks_by_id: dict[int, dict]) -> list[dict]:
    matched_ids: list[int] = []
    for sec in sections_data.get("sections", []):
        if sec["kind"] == kind:
            matched_ids.extend(sec["block_ids"])
    return [blocks_by_id[bid] for bid in sorted(set(matched_ids)) if bid in blocks_by_id]


def _header_blocks(sections_data: dict, blocks_by_id: dict[int, dict]) -> list[dict]:
    """Return blocks that are NOT claimed by any section.

    These usually include the title area, decorative headers, separators —
    and are the most common place location/employment info appears.
    """
    claimed: set[int] = set()
    for sec in sections_data.get("sections", []):
        claimed.update(sec["block_ids"])
    return [b for bid, b in sorted(blocks_by_id.items()) if bid not in claimed]


def render_to_file(
    task: str,
    input_path: Path,
    out_path: Path,
    *,
    sections_path: Path | None = None,
    kind: str | None = None,
    output_path_hint: str | None = None,
    previous_error: str | None = None,
) -> None:
    """Render a task template to a file on disk (CLI helper)."""
    input_data = json.loads(input_path.read_text())
    sections_data = json.loads(sections_path.read_text()) if sections_path else None
    rendered = render_task(
        task,
        input_data=input_data,
        sections_data=sections_data,
        kind=kind,
        output_path=output_path_hint or str(out_path.with_suffix(".out.json")),
        previous_error=previous_error,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered)
