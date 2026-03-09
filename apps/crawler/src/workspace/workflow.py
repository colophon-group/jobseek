"""Task-driven workflow engine.

Loads workflow.yaml, tracks the current step, verifies gate conditions,
manages the per-board loop, and renders step instructions with context
variables injected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.shared.constants import get_repo_root
from src.workspace.state import (
    Board,
    Workspace,
    list_boards,
    load_workspace,
    ws_dir,
)

# ── Workflow YAML loading ────────────────────────────────────────────

_WORKFLOW: dict[str, Any] | None = None


def _pkg_path() -> Path:
    """Return the path to the workspace package directory."""
    return Path(__file__).parent


def _load_workflow() -> dict[str, Any]:
    global _WORKFLOW
    if _WORKFLOW is None:
        yaml_path = _pkg_path() / "workflow.yaml"
        _WORKFLOW = yaml.safe_load(yaml_path.read_text())
    return _WORKFLOW


def _load_step_markdown(instructions: str) -> str:
    """Load a step markdown file relative to the package directory.

    This markdown is part of the crawler setup agent's runtime instruction stream.
    Keep behavior guidance in step files (and ws help / KB), not only in
    developer-facing docs.
    """
    path = _pkg_path() / instructions
    if not path.exists():
        return f"(Step instructions not found: {instructions})"
    return path.read_text()


# ── Step dataclass ──────────────────────────────────────────────────


@dataclass
class StepDef:
    """A single step definition from workflow.yaml."""

    id: str
    title: str
    instructions: str
    gate_type: str  # "state" or "manual"
    gate_check: str | None = None  # function name for state gates
    skip_when: str | None = None  # condition name for skipping
    phase: str = "global"  # "global", "per_board", "final"

    @classmethod
    def from_dict(cls, data: dict[str, Any], phase: str) -> StepDef:
        gate = data.get("gate", {})
        return cls(
            id=data["id"],
            title=data["title"],
            instructions=data["instructions"],
            gate_type=gate.get("type", "manual"),
            gate_check=gate.get("check"),
            skip_when=data.get("skip_when"),
            phase=phase,
        )


def _all_step_defs() -> list[StepDef]:
    """Return all step definitions in execution order (without board expansion)."""
    wf = _load_workflow()
    steps: list[StepDef] = []
    for s in wf.get("global_steps", []):
        steps.append(StepDef.from_dict(s, "global"))
    for s in wf.get("per_board_steps", []):
        steps.append(StepDef.from_dict(s, "per_board"))
    for s in wf.get("final_steps", []):
        steps.append(StepDef.from_dict(s, "final"))
    return steps


# ── Workflow state (persisted in workspace.yaml) ─────────────────────


@dataclass
class WorkflowState:
    """Tracks current position in the workflow.

    Persisted as ``workflow`` key in workspace.yaml.
    """

    current_step: str = "setup"
    current_board: str | None = None  # alias of the board being configured
    completed_boards: list[str] = field(default_factory=list)
    reflections: list[dict[str, str]] = field(default_factory=list)
    failed: bool = False
    fail_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "current_step": self.current_step,
            "current_board": self.current_board,
            "completed_boards": self.completed_boards,
            "reflections": self.reflections,
        }
        if self.failed:
            d["failed"] = True
            d["fail_reason"] = self.fail_reason
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> WorkflowState:
        if not data:
            return cls()
        return cls(
            current_step=data.get("current_step", "setup"),
            current_board=data.get("current_board"),
            completed_boards=data.get("completed_boards") or [],
            reflections=data.get("reflections") or [],
            failed=data.get("failed", False),
            fail_reason=data.get("fail_reason", ""),
        )


# ── Gate verification ────────────────────────────────────────────────

from src.workspace._compat import _RICH_MONITORS  # noqa: E402


def _config_tested(board: Board) -> bool:
    """Active config has been selected and tested with >0 jobs."""
    cfg = board._active_cfg()
    if not cfg:
        return False
    if cfg.get("status") not in ("tested",):
        return False
    run = cfg.get("run") or {}
    return (run.get("jobs") or 0) > 0


def _is_rich(board: Board) -> bool:
    """Active monitor is a rich API type (scraper skipped)."""
    cfg = board._active_cfg()
    if not cfg:
        return False
    mtype = cfg.get("monitor_type", "")
    # Check explicit rich flag from run
    if cfg.get("rich") or (cfg.get("run") or {}).get("has_rich_data"):
        return True
    return mtype in _RICH_MONITORS


def _scraper_done(board: Board) -> bool:
    """Scraper has been selected and tested (or skipped for rich monitors)."""
    if _is_rich(board):
        return True
    cfg = board._active_cfg()
    if not cfg:
        return False
    return bool(cfg.get("scraper_type")) and bool(cfg.get("scraper_run"))


def _has_feedback(board: Board) -> bool:
    """Feedback recorded with good or acceptable verdict."""
    cfg = board._active_cfg()
    if not cfg:
        return False
    fb = cfg.get("feedback")
    if not fb:
        return False
    return fb.get("verdict") in ("good", "acceptable")


GLOBAL_GATES: dict[str, Any] = {
    "company_complete": lambda ws, boards: bool(ws.name and ws.website and ws.branch),
    "all_boards_added": lambda ws, boards: len(boards) > 0,
    "submitted": lambda ws, boards: ws.submit_state.get("pr_ready", False),
}

PER_BOARD_GATES: dict[str, Any] = {
    "monitor_tested": _config_tested,
    "scraper_tested": _scraper_done,
    "feedback_recorded": _has_feedback,
}

SKIP_CONDITIONS: dict[str, Any] = {
    "rich_monitor": _is_rich,
}


def check_gate(
    step: StepDef, ws: Workspace, boards: list[Board], board: Board | None = None
) -> tuple[bool, str]:
    """Check if a step's gate passes.

    Returns (passed, reason).
    """
    if step.gate_type == "manual":
        return False, 'Confirm completion with: ws task next --notes "..."'

    check_name = step.gate_check
    if not check_name:
        return True, ""

    if step.phase == "per_board":
        if board is None:
            return False, "No active board"
        fn = PER_BOARD_GATES.get(check_name)
        if fn:
            if fn(board):
                return True, ""
            return False, f"Gate '{check_name}' not satisfied for board {board.slug}"
        return False, f"Unknown gate: {check_name}"

    # Global or final gate
    fn = GLOBAL_GATES.get(check_name)
    if fn:
        if fn(ws, boards):
            return True, ""
        return False, f"Gate '{check_name}' not satisfied"
    return False, f"Unknown gate: {check_name}"


def should_skip(step: StepDef, board: Board | None) -> bool:
    """Check if a step should be skipped based on skip_when condition."""
    if not step.skip_when or board is None:
        return False
    fn = SKIP_CONDITIONS.get(step.skip_when)
    if fn:
        return fn(board)
    return False


# ── Context injection ────────────────────────────────────────────────


def _rejected_configs_text(board: Board) -> str:
    """Format rejected configs for display in step instructions."""
    rejected = []
    for name, cfg in board.configs.items():
        if cfg.get("status") == "rejected":
            reason = cfg.get("rejection_reason", "no reason given")
            rejected.append(f"- **{name}**: {reason}")
    if not rejected:
        return ""
    return "## Previously rejected configs\n\n" + "\n".join(rejected)


def _format_reflections(reflections: list[dict[str, str]]) -> str:
    """Format all reflections for the reflect step."""
    if not reflections:
        return "  (no reflections recorded)"
    lines = []
    for r in reflections:
        step = r.get("step", "?")
        board = r.get("board", "")
        notes = r.get("notes", "none")
        label = f"[{step}"
        if board:
            label += f" / {board}"
        label += "]"
        lines.append(f"  {label}")
        lines.append(f"  {notes}")
        lines.append("")
    return "\n".join(lines)


def build_context(
    ws: Workspace, boards: list[Board], wf_state: WorkflowState, board: Board | None = None
) -> dict[str, str]:
    """Build template context variables for step instruction rendering."""
    ctx: dict[str, str] = {
        "slug": ws.slug,
        "issue": str(ws.issue or ""),
        "board_url": "",
        "board_progress": "",
        "artifact_path": "",
        "rejected_configs": "",
        "reflections": _format_reflections(wf_state.reflections),
    }

    if board:
        ctx["board_url"] = board.url
        total = len(boards)
        done = len(wf_state.completed_boards)
        # Current board index: completed + 1
        current_idx = done + 1
        ctx["board_progress"] = f"{current_idx}/{total}: {board.slug}"
        ctx["artifact_path"] = str(ws_dir(ws.slug) / "artifacts" / board.alias)
        ctx["rejected_configs"] = _rejected_configs_text(board)

    return ctx


def render_step(step: StepDef, ctx: dict[str, str]) -> str:
    """Load and render a step's markdown with context variables."""
    raw = _load_step_markdown(step.instructions)
    # Replace only known placeholders and keep literal braces unchanged.
    # Step docs include examples like '{...}' that should not be formatted.
    for k, v in ctx.items():
        raw = raw.replace("{" + k + "}", v)
    return raw


# ── Workflow navigation ──────────────────────────────────────────────


def get_workflow_state(ws: Workspace) -> WorkflowState:
    """Load workflow state from workspace."""
    raw = ws.to_dict().get("workflow")
    return WorkflowState.from_dict(raw)


def save_workflow_state(ws: Workspace, wf: WorkflowState) -> None:
    """Save workflow state to workspace and persist."""
    # Inject workflow into workspace dict by adding it as a field
    ws.submit_state.setdefault("__workflow", None)  # ensure dict exists
    # We store workflow state directly in the YAML via to_dict override
    _save_wf_to_disk(ws.slug, wf)


def _save_wf_to_disk(slug: str, wf: WorkflowState) -> None:
    """Persist workflow state to a dedicated file."""
    path = ws_dir(slug) / "workflow.state.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    from src.workspace.state import _atomic_write

    _atomic_write(path, yaml.dump(wf.to_dict(), default_flow_style=False, sort_keys=False))


def _load_wf_from_disk(slug: str) -> WorkflowState:
    """Load workflow state from dedicated file."""
    path = ws_dir(slug) / "workflow.state.yaml"
    if not path.exists():
        return WorkflowState()
    data = yaml.safe_load(path.read_text())
    return WorkflowState.from_dict(data)


def _resolve_active_board(
    wf: WorkflowState,
    boards: list[Board],
) -> tuple[Board | None, str | None]:
    """Resolve a valid current board for per-board steps.

    Self-heals stale state when ``wf.current_board`` points to a deleted board.
    Preference order:
    1) existing current_board (if still present)
    2) next uncompleted board
    3) first available board
    """
    if not boards:
        return None, None

    by_alias = {b.alias: b for b in boards}
    if wf.current_board and wf.current_board in by_alias:
        return by_alias[wf.current_board], wf.current_board

    next_board = _next_uncompleted_board(boards, wf.completed_boards)
    if next_board:
        return next_board, next_board.alias

    fallback = boards[0]
    return fallback, fallback.alias


def resolve_current_step(
    slug: str,
) -> tuple[StepDef, Workspace, list[Board], WorkflowState, Board | None]:
    """Resolve the current step, workspace, boards, and active board.

    Returns (step_def, workspace, boards, workflow_state, active_board_or_none).
    """
    ws = load_workspace(slug)
    boards = list_boards(slug)
    wf = _load_wf_from_disk(slug)

    all_steps = _all_step_defs()
    step_map = {s.id: s for s in all_steps}

    step = step_map.get(wf.current_step)
    if not step:
        # Default to first step
        step = all_steps[0]
        wf.current_step = step.id

    # Resolve the active board for per_board steps
    board: Board | None = None
    if step.phase == "per_board":
        board, resolved_alias = _resolve_active_board(wf, boards)
        if resolved_alias != wf.current_board:
            wf.current_board = resolved_alias
            _save_wf_to_disk(slug, wf)

    return step, ws, boards, wf, board


def advance(slug: str, notes: str) -> tuple[StepDef | None, str]:
    """Advance the workflow to the next step.

    Records the reflection note, checks the gate, and moves forward.

    Returns (next_step_or_none, message).
    """
    ws = load_workspace(slug)
    boards = list_boards(slug)
    wf = _load_wf_from_disk(slug)

    all_steps = _all_step_defs()
    step_map = {s.id: s for s in all_steps}

    current = step_map.get(wf.current_step)
    if not current:
        return None, "Unknown current step"

    # Resolve active board
    board: Board | None = None
    repaired_board_pointer = False
    if current.phase == "per_board":
        board, resolved_alias = _resolve_active_board(wf, boards)
        if resolved_alias != wf.current_board:
            wf.current_board = resolved_alias
            repaired_board_pointer = True

    # Check the gate
    if current.gate_type == "state":
        passed, reason = check_gate(current, ws, boards, board)
        if not passed:
            if repaired_board_pointer:
                _save_wf_to_disk(slug, wf)
            return current, f"Cannot advance: {reason}"

    # Record reflection
    if notes and notes.lower() != "none":
        reflection = {"step": current.id, "notes": notes}
        if board:
            reflection["board"] = board.slug
        wf.reflections.append(reflection)
    elif notes.lower() == "none":
        wf.reflections.append(
            {
                "step": current.id,
                "board": board.slug if board else "",
                "notes": "none",
            }
        )

    # Determine next step
    next_step, next_board = _find_next(current, wf, boards, all_steps)

    if next_step is None:
        wf.current_step = "done"
        wf.current_board = None
        _save_wf_to_disk(slug, wf)
        return None, "Workflow complete!"

    wf.current_step = next_step.id
    wf.current_board = next_board
    _save_wf_to_disk(slug, wf)

    return next_step, ""


def _find_next(
    current: StepDef,
    wf: WorkflowState,
    boards: list[Board],
    all_steps: list[StepDef],
) -> tuple[StepDef | None, str | None]:
    """Find the next step after the current one.

    Handles the per-board loop: after finishing all per_board steps for one
    board, moves to the next uncompleted board. After all boards, moves to
    final steps.
    """
    global_steps = [s for s in all_steps if s.phase == "global"]
    per_board_steps = [s for s in all_steps if s.phase == "per_board"]
    final_steps = [s for s in all_steps if s.phase == "final"]

    if current.phase == "global":
        idx = next((i for i, s in enumerate(global_steps) if s.id == current.id), -1)
        if idx + 1 < len(global_steps):
            return global_steps[idx + 1], None

        # Move to per_board phase: pick first board
        if boards and per_board_steps:
            first_board = boards[0]
            first_step = _first_applicable_per_board(per_board_steps, first_board)
            if first_step:
                return first_step, first_board.alias

        # No boards or no per-board steps, go to final
        if final_steps:
            return final_steps[0], None
        return None, None

    if current.phase == "per_board":
        # Try next per-board step for current board
        idx = next((i for i, s in enumerate(per_board_steps) if s.id == current.id), -1)
        by_alias = {b.alias: b for b in boards}
        current_alias = wf.current_board if wf.current_board in by_alias else None

        if current_alias is None:
            fallback_board = _next_uncompleted_board(boards, wf.completed_boards)
            if fallback_board is None:
                if final_steps:
                    return final_steps[0], None
                return None, None
            current_alias = fallback_board.alias

        current_board = by_alias.get(current_alias)
        if current_board is None:
            if final_steps:
                return final_steps[0], None
            return None, None

        # Look for next applicable step in this board
        for next_idx in range(idx + 1, len(per_board_steps)):
            candidate = per_board_steps[next_idx]
            if current_board and should_skip(candidate, current_board):
                continue
            return candidate, current_alias

        # Finished this board — mark it completed, find next board
        if current_alias and current_alias not in wf.completed_boards:
            wf.completed_boards.append(current_alias)

        next_board = _next_uncompleted_board(boards, wf.completed_boards)
        if next_board:
            first_step = _first_applicable_per_board(per_board_steps, next_board)
            if first_step:
                return first_step, next_board.alias

        # All boards done, move to final steps
        if final_steps:
            return final_steps[0], None
        return None, None

    if current.phase == "final":
        idx = next((i for i, s in enumerate(final_steps) if s.id == current.id), -1)
        if idx + 1 < len(final_steps):
            return final_steps[idx + 1], None
        return None, None

    return None, None


def _first_applicable_per_board(steps: list[StepDef], board: Board) -> StepDef | None:
    """Return the first per-board step that isn't skipped for this board."""
    for s in steps:
        if not should_skip(s, board):
            return s
    return None


def _next_uncompleted_board(boards: list[Board], completed: list[str]) -> Board | None:
    """Return the next board not in the completed list."""
    for b in boards:
        if b.alias not in completed:
            return b
    return None


# ── KB search ────────────────────────────────────────────────────────


def _kb_dir() -> Path:
    """Return KB directory in the active repo/worktree when available."""
    repo_root = get_repo_root()
    if repo_root is not None:
        candidate = repo_root / "apps" / "crawler" / "src" / "workspace" / "kb"
        if (repo_root / "apps" / "crawler").exists() or candidate.exists():
            return candidate
    return _pkg_path() / "kb"


def search_kb(query: str) -> list[dict[str, Any]]:
    """Search the troubleshooting knowledge base for matching entries.

    Returns list of dicts with keys: path, symptom, tags, body.
    """
    kb_dir = _kb_dir()
    if not kb_dir.exists():
        return []

    query_lower = query.lower()
    query_tokens = query_lower.split()
    results = []

    for md_path in sorted(kb_dir.glob("*.md")):
        content = md_path.read_text()

        # Parse YAML frontmatter
        frontmatter: dict[str, Any] = {}
        body = content
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = yaml.safe_load(parts[1]) or {}
                body = parts[2].strip()

        # Search in symptom, tags, and body
        symptom = frontmatter.get("symptom", "")
        tags = frontmatter.get("tags", [])
        tags_str = " ".join(tags) if isinstance(tags, list) else str(tags)
        searchable = f"{symptom} {tags_str} {body}".lower()

        # Match if full query is substring OR all tokens appear
        if query_lower in searchable or all(t in searchable for t in query_tokens):
            results.append(
                {
                    "path": md_path.name,
                    "symptom": symptom,
                    "tags": tags,
                    "step": frontmatter.get("step", ""),
                    "body": body,
                }
            )

    return results


def create_kb_entry(slug: str, step: str, symptom: str, solution: str, tags: str) -> Path:
    """Create a new KB entry file in the workspace kb directory.

    New entries are stored in the workspace's local kb/ dir and can be
    committed alongside CSV changes on submit.
    """
    kb_dir = _kb_dir()
    kb_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename from symptom
    filename = symptom.lower()
    filename = "".join(c if c.isalnum() or c == " " else "" for c in filename)
    filename = filename.strip().replace(" ", "-")[:60]
    if not filename:
        filename = "entry"

    # Avoid collisions
    base = filename
    counter = 1
    path = kb_dir / f"{filename}.md"
    while path.exists():
        filename = f"{base}-{counter}"
        path = kb_dir / f"{filename}.md"
        counter += 1

    tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    content = f"""---
step: {step}
symptom: {symptom}
tags: {tag_list}
---
# {symptom}

## Problem
{symptom}

## Solution
{solution}
"""
    path.write_text(content)
    return path
