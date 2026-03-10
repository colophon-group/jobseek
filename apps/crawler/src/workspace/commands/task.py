"""Task-driven workflow commands.

Entry point: ``ws task --issue <N>`` fetches the issue and starts the workflow.

Crawler setup agents interact with the workflow exclusively through these commands:
- ``ws task --issue <N>``         — fetch issue, pre-verify, start workflow
- ``ws task --pick``              — auto-select oldest open company-request issue
- ``ws task``                     — show current task instructions
- ``ws task next --notes ...``    — reflect, verify gate, advance
- ``ws task status``              — show workflow progress
- ``ws task complete``            — mark workflow as done (final step only)
- ``ws task fail --reason ...``   — mark step as failed, unlock exploration mode
- ``ws task troubleshoot``        — search the troubleshooting KB
- ``ws task learn``               — add a KB entry from experience

Instruction sources available to crawler setup agents:
- Step markdown rendered from ``apps/crawler/src/workspace/steps/`` via ``workflow.yaml``
- ``ws help`` topic text defined in ``apps/crawler/src/workspace/commands/help.py``
- KB entries in ``apps/crawler/src/workspace/kb/`` accessed via ``ws task troubleshoot``

Developer docs (for example AGENTS.md and docs/) are not part of the runtime
instruction stream unless explicitly copied into the sources above.
"""

from __future__ import annotations

import click

from src.workspace import output as out
from src.workspace.errors import GitError
from src.workspace.state import (
    get_active_slug,
    list_boards,
    list_workspaces,
    load_workspace,
    resolve_slug,
    set_active_slug,
)
from src.workspace.workflow import (
    WorkflowState,
    _all_step_defs,
    _load_wf_from_disk,
    _save_wf_to_disk,
    advance,
    build_context,
    check_gate,
    create_kb_entry,
    render_step,
    resolve_current_step,
    search_kb,
    should_skip,
)


@click.group(invoke_without_command=True)
@click.option("--issue", type=int, default=None, help="GitHub issue number")
@click.option(
    "--pick",
    "pick_next",
    is_flag=True,
    default=False,
    help="Auto-select the oldest open company-request issue",
)
@click.pass_context
def task(ctx, issue: int | None, pick_next: bool):
    """Show current task instructions or start a new workflow.

    With --issue and no active workspace: fetches the issue from GitHub
    and prints pre-verification instructions. The agent decides whether
    to proceed (ws new <slug> --issue <N>) or reject (ws reject --issue <N>).

    With --pick: automatically selects the oldest open company-request
    issue that has no active PR, equivalent to --issue with that number.

    Without --issue (active workspace): displays the current step.
    """
    if ctx.invoked_subcommand is not None:
        return

    if pick_next:
        if issue is not None:
            out.die("Cannot use --issue and --pick together.")
            return

        from src.workspace.git import check_gh_auth, fetch_oldest_open_issue

        if not check_gh_auth():
            out.die("GitHub CLI not authenticated. Run: gh auth login")
            return

        out.info("task", "Searching for oldest open company-request issue...")
        issue = fetch_oldest_open_issue()
        if issue is None:
            out.info("task", "No open company-request issues without an active PR.")
            return
        out.info("task", f"Selected issue #{issue}")

    # --issue:
    # - continue in active workspace when it matches
    # - otherwise bind to an existing workspace with the same issue (if unique)
    # - otherwise render pre-verify for a new workflow
    if issue is not None:
        active = get_active_slug()
        if active:
            try:
                ws = load_workspace(active)
                if str(ws.issue) != str(issue):
                    active = None
            except FileNotFoundError:
                active = None

        if not active:
            matches = [w.slug for w in list_workspaces() if str(w.issue) == str(issue)]
            if len(matches) == 1:
                set_active_slug(matches[0])
                out.info("task", f"Using existing workspace {matches[0]!r} for issue #{issue}")
            elif len(matches) > 1:
                choices = ", ".join(repr(s) for s in matches)
                out.die(f"Multiple workspaces match issue #{issue}: {choices}. Run: ws use <slug>")
                return
            else:
                _pre_verify(issue)
                return

    # Active workspace → show current step
    slug = resolve_slug(None)

    try:
        wf = _load_wf_from_disk(slug)
    except FileNotFoundError:
        out.die(f"Workspace {slug!r} not found. Run: ws task --issue <N>")
        return

    if wf.failed:
        _print_failed(wf)
        return

    if wf.current_step == "done":
        out.info("task", "Workflow complete! All steps done.")
        return

    try:
        step, ws, boards, wf, board = resolve_current_step(slug)
    except FileNotFoundError:
        out.die(f"Workspace {slug!r} not found. Run: ws task --issue <N>")
        return

    # Build context and render
    ctx_vars = build_context(ws, boards, wf, board)
    instructions = render_step(step, ctx_vars)

    # Print header
    _print_step_header(step, wf, boards)

    # Print instructions
    print(instructions)


def _pre_verify(issue: int) -> None:
    """Fetch issue from GitHub and render 00-pre-verify.md with context."""
    from pathlib import Path

    from src.workspace.git import check_gh_auth, fetch_issue

    if not check_gh_auth():
        out.die("GitHub CLI not authenticated. Run: gh auth login")
        return

    out.info("task", f"Fetching issue #{issue}...")

    try:
        data = fetch_issue(issue)
    except Exception as exc:
        out.die(f"Failed to fetch issue #{issue}: {exc}")
        return

    title = data.get("title", "(no title)")
    body = data.get("body", "").strip() or "(no body)"

    # Load and render the pre-verify template
    template_path = Path(__file__).parent.parent / "steps" / "00-pre-verify.md"
    template = template_path.read_text()
    rendered = template.format(
        issue=issue,
        issue_title=title,
        issue_body=body,
    )

    out.plain("task", "Step 0/7: Pre-verify the request")
    print()
    print(rendered)


@task.command(name="next")
@click.option("--notes", required=True, help="Reflection notes for this step (or 'none')")
def task_next(notes: str):
    """Record reflection, verify gate, and advance to next step."""
    slug = resolve_slug(None)
    wf = _load_wf_from_disk(slug)
    prev_step_id = wf.current_step
    prev_board_alias = wf.current_board

    if wf.failed:
        out.die("Workflow is in failed state. Use 'ws task fail' info or start over.")

    if wf.current_step == "done":
        out.info("task", "Workflow already complete.")
        return

    if not notes or not notes.strip():
        out.die("--notes is required. Use --notes 'none' if nothing to report.")

    next_step, message = advance(slug, notes.strip())

    if message and next_step and message.startswith("Cannot advance"):
        out.error("gate", message)
        out.plain("task", "Complete the requirements above, then try again.")
        return

    if next_step is None:
        if message:
            out.info("task", message)
        else:
            out.info("task", "Workflow complete!")
        return

    # Show the new step
    ws = load_workspace(slug)
    boards = list_boards(slug)
    wf = _load_wf_from_disk(slug)
    board = None
    if next_step.phase == "per_board" and wf.current_board:
        for b in boards:
            if b.alias == wf.current_board:
                board = b
                break

    ctx_vars = build_context(ws, boards, wf, board)
    instructions = render_step(next_step, ctx_vars)

    skipped_steps = _skipped_steps_between(
        prev_step_id,
        next_step.id,
        prev_board_alias,
        boards,
    )
    for title, reason in skipped_steps:
        out.info("task", f"Skipped step: {title} ({reason})")
    if skipped_steps:
        print()

    print()
    _print_step_header(next_step, wf, boards)
    print(instructions)


@task.command(name="status")
def task_status():
    """Show workflow progress."""
    slug = resolve_slug(None)
    ws = load_workspace(slug)
    boards = list_boards(slug)
    wf = _load_wf_from_disk(slug)

    all_steps = _all_step_defs()

    out.plain("workflow", f"Workspace: {slug}")
    out.plain("workflow", f"Current step: {wf.current_step}")
    if wf.current_board:
        out.plain("workflow", f"Current board: {wf.current_board}")
    if wf.completed_boards:
        out.plain("workflow", f"Completed boards: {', '.join(wf.completed_boards)}")
    out.plain("workflow", f"Total boards: {len(boards)}")

    print()
    out.plain("progress", "Steps:")

    for step in all_steps:
        if step.phase == "per_board":
            # Show per-board steps for each board
            for b in boards:
                if should_skip(step, b):
                    status = "skipped"
                elif b.alias in wf.completed_boards:
                    status = "done"
                elif step.id == wf.current_step and wf.current_board == b.alias:
                    status = "current"
                else:
                    # Check gate to determine if done
                    passed, _ = check_gate(step, ws, boards, b)
                    status = "done" if passed else "pending"
                symbol = {"done": "v", "current": ">", "skipped": "-", "pending": " "}[status]
                print(f"  [{symbol}] {step.title} ({b.slug})")
        else:
            if step.id == wf.current_step:
                status = "current"
            else:
                # Check if this step comes before or after current
                step_ids = [s.id for s in all_steps]
                current_idx = step_ids.index(wf.current_step) if wf.current_step in step_ids else 0
                this_idx = step_ids.index(step.id)
                status = "done" if this_idx < current_idx else "pending"
            symbol = {"done": "v", "current": ">", "pending": " "}[status]
            print(f"  [{symbol}] {step.title}")

    if wf.failed:
        print()
        out.error("workflow", f"FAILED at step '{wf.current_step}': {wf.fail_reason}")


@task.command(name="complete")
def task_complete():
    """Mark workflow as done (only valid at the final reflect step)."""
    slug = resolve_slug(None)
    wf = _load_wf_from_disk(slug)

    if wf.current_step != "reflect":
        out.die(f"Can only complete from the 'reflect' step. Current step: {wf.current_step}")

    try:
        _persist_kb_updates_if_needed(slug)
    except GitError as exc:
        out.die(f"Failed to persist KB updates: {exc}")

    wf.current_step = "done"
    _save_wf_to_disk(slug, wf)
    out.info("task", "Workflow complete! Nice work.")

    # Print summary of reflections
    non_none = [r for r in wf.reflections if r.get("notes", "none") != "none"]
    if non_none:
        print()
        out.plain("summary", f"{len(non_none)} reflection(s) recorded during this run.")


@task.command(name="fail")
@click.option("--reason", required=True, help="Why the step cannot be completed")
def task_fail(reason: str):
    """Mark the current step as failed and enter exploration mode."""
    slug = resolve_slug(None)
    wf = _load_wf_from_disk(slug)

    wf.failed = True
    wf.fail_reason = reason
    _save_wf_to_disk(slug, wf)

    _print_failed(wf)


@task.command(name="troubleshoot")
@click.argument("query")
def task_troubleshoot(query: str):
    """Search the troubleshooting knowledge base."""
    results = search_kb(query)

    if not results:
        out.plain("kb", f"No matches for: {query}")
        out.plain("kb", "Try different keywords or check ws help troubleshooting")
        return

    out.plain("kb", f"Found {len(results)} match(es):")
    print()

    for r in results:
        print(f"  --- {r['symptom']} ---")
        print(f"  Step: {r['step']}  Tags: {', '.join(r['tags'])}")
        # Print first ~10 lines of body
        lines = r["body"].strip().split("\n")
        for line in lines[:12]:
            print(f"  {line}")
        if len(lines) > 12:
            print(f"  ... ({len(lines) - 12} more lines)")
        print()


@task.command(name="learn")
@click.option("--step", required=True, help="Step ID where this applies")
@click.option("--symptom", required=True, help="What went wrong")
@click.option("--solution", required=True, help="What fixed it")
@click.option("--tags", required=True, help="Comma-separated tags")
def task_learn(step: str, symptom: str, solution: str, tags: str):
    """Add a new entry to the troubleshooting knowledge base."""
    slug = resolve_slug(None)
    path = create_kb_entry(slug, step, symptom, solution, tags)
    out.info("kb", f"Created KB entry: {path.name}")
    out.plain("kb", f"  Symptom: {symptom}")
    out.plain("kb", f"  Tags: {tags}")


# ── Display helpers ──────────────────────────────────────────────────


def _persist_kb_updates_if_needed(slug: str) -> None:
    """Commit/push KB updates created during reflection after submit."""
    from src.workspace.commands.lifecycle import is_local_mode

    if is_local_mode():
        return

    ws = load_workspace(slug)
    if not ws.submit_state.get("pushed"):
        return

    from src.workspace import git

    kb_path = "apps/crawler/src/workspace/kb/"
    if not git.has_uncommitted_changes([kb_path]):
        return

    git.add_files([kb_path])
    commit_msg = f"Add KB reflections for {ws.slug}"
    if ws.issue:
        commit_msg += f"\n\nRefs #{ws.issue}"
    git.commit(commit_msg)
    if git.is_ahead_of_remote():
        git.push()
    out.info("kb", "Committed and pushed KB updates.")


def _print_step_header(step, wf: WorkflowState, boards: list) -> None:
    """Print the step header with progress info."""
    total_steps = len(_all_step_defs())
    all_steps = _all_step_defs()
    step_ids = [s.id for s in all_steps]
    current_idx = step_ids.index(step.id) if step.id in step_ids else 0

    # For per-board steps, show board progress
    board_info = ""
    if step.phase == "per_board" and wf.current_board:
        done = len(wf.completed_boards)
        total = len(boards)
        board_info = f"  (board {done + 1}/{total})"

    out.plain("task", f"Step {current_idx + 1}/{total_steps}: {step.title}{board_info}")
    print()


def _print_failed(wf: WorkflowState) -> None:
    """Print failure info and coding mode instructions."""
    from pathlib import Path

    template_path = Path(__file__).parent.parent / "steps" / "fail-mode.md"
    template = template_path.read_text()
    rendered = template.format(
        failed_step=wf.current_step,
        fail_reason=wf.fail_reason,
    )
    print(rendered)


def _skip_reason(skip_when: str | None) -> str:
    if skip_when == "rich_monitor":
        return "active monitor is rich"
    return skip_when or "condition met"


def _skipped_steps_between(
    prev_step_id: str,
    next_step_id: str,
    board_alias: str | None,
    boards: list,
) -> list[tuple[str, str]]:
    """Return skipped per-board step titles between two step ids."""
    if not board_alias:
        return []

    board = next((b for b in boards if b.alias == board_alias), None)
    if board is None:
        return []

    all_steps = _all_step_defs()
    step_ids = [s.id for s in all_steps]
    if prev_step_id not in step_ids or next_step_id not in step_ids:
        return []

    start = step_ids.index(prev_step_id)
    end = step_ids.index(next_step_id)
    if end <= start + 1:
        return []

    skipped: list[tuple[str, str]] = []
    for step in all_steps[start + 1 : end]:
        if step.phase != "per_board":
            continue
        if step.skip_when and should_skip(step, board):
            skipped.append((step.title, _skip_reason(step.skip_when)))
    return skipped
