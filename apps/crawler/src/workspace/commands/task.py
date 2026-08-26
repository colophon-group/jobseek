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
- ``ws task escalate``            — terminally clean up and record a human follow-up
- ``ws task troubleshoot``        — search the knowledge base
- ``ws task troubleshoot --view`` — view a full KB entry
- ``ws task learn``               — add a KB entry from experience
- ``ws task casestudy``           — record a case study from a complex setup

Instruction sources available to crawler setup agents:
- Step markdown rendered from ``apps/crawler/src/workspace/steps/`` via ``workflow.yaml``
- ``ws help`` topic text defined in ``apps/crawler/src/workspace/commands/help.py``
- KB entries in ``apps/crawler/src/workspace/kb/`` accessed via ``ws task troubleshoot``

Developer docs (for example AGENTS.md and docs/) are not part of the runtime
instruction stream unless explicitly copied into the sources above.
"""

from __future__ import annotations

import copy
import os
import re

import click

from src.workspace import log as action_log
from src.workspace import output as out
from src.workspace.errors import WorkspaceError
from src.workspace.state import (
    get_active_slug,
    list_boards,
    list_workspaces,
    load_workspace,
    resolve_slug,
    save_workspace,
    set_active_slug,
    ws_log_path,
)
from src.workspace.workflow import (
    WorkflowState,
    _all_step_defs,
    _load_wf_from_disk,
    _save_wf_to_disk,
    advance,
    build_context,
    check_gate,
    create_casestudy_entry,
    create_kb_entry,
    go_back,
    read_kb_entry,
    render_step,
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

        from src.workspace.git import check_gh_auth, claim_issue, fetch_oldest_open_issue

        if not check_gh_auth():
            out.die("GitHub CLI not authenticated. Run: gh auth login")
            return

        out.info("task", "Searching for oldest open company-request issue...")
        issue = fetch_oldest_open_issue()
        if issue is None:
            out.info("task", "No open company-request issues without an active PR.")
            return
        out.info("task", f"Selected issue #{issue}")
        claim_issue(issue)
        out.info("task", f"Claimed issue #{issue}")

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

    # Active workspace → show parallel orchestrator instructions
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

    ws = load_workspace(slug)
    boards = list_boards(slug)

    from src.workspace.state import ws_dir
    from src.workspace.workflow import render_parallel_prompt

    # Copy prompt templates into workspace as fallback
    prompts_dir = ws_dir(slug) / "prompts"
    if not prompts_dir.exists():
        import shutil
        from pathlib import Path

        src_prompts = Path(__file__).parent.parent / "steps" / "parallel"
        if src_prompts.is_dir():
            shutil.copytree(src_prompts, prompts_dir)

    # Build context with pre-rendered subagent prompts inlined
    base_ctx = {
        "slug": slug,
        "issue": str(ws.issue or ""),
        "website": ws.website or "",
        "company_name": ws.name or "",
        "prompts_dir": str(prompts_dir),
        "ats_inventory_seed": ws.ats_inventory,
    }

    # Embed industry table in track-a
    industry_table = _get_industry_table()
    track_a_ctx = {**base_ctx, "industry_table": industry_table}
    track_a_prompt = render_parallel_prompt("track-a-enrichment", track_a_ctx)

    # Embed monitor type list in track-c
    from src.workspace.commands.help import MONITORS

    monitor_table = MONITORS.split("Interpretation guide")[0].strip()
    track_c_ctx = {**base_ctx, "monitor_table": monitor_table}
    track_c_prompt = render_parallel_prompt("track-c-boards", track_c_ctx)

    # Track B — no extra embedding needed
    track_b_prompt = render_parallel_prompt("track-b-logos", base_ctx)

    # Config tester / comparison — read raw templates (board-specific vars not yet known)
    config_tester_raw = _read_raw_template("config-tester.md")
    config_comparison_raw = _read_raw_template("config-comparison.md")

    ctx = {
        **base_ctx,
        "track_a_prompt": track_a_prompt,
        "track_b_prompt": track_b_prompt,
        "track_c_prompt": track_c_prompt,
        "config_tester_raw": config_tester_raw,
        "config_comparison_raw": config_comparison_raw,
    }
    instructions = render_parallel_prompt("orchestrator", ctx)

    out.plain("task", f"Workspace: {slug} | Issue: #{ws.issue or '?'}")
    if boards:
        out.plain("task", f"Boards: {', '.join(b.alias for b in boards)}")
    print()
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

    from src.workspace.ats_seed import issue_has_inventory_label, preverify_inventory_context

    ats_inventory_context = (
        preverify_inventory_context(body) if issue_has_inventory_label(data) else ""
    )

    # Load and render the pre-verify template
    template_path = Path(__file__).parent.parent / "steps" / "00-pre-verify.md"
    template = template_path.read_text()
    rendered = template.format(
        issue=issue,
        issue_title=title,
        issue_body=body,
        ats_inventory_context=ats_inventory_context,
    )

    out.plain("task", "Step 0/7: Pre-verify the request")
    print()
    print(rendered)


@task.command(name="next")
@click.option(
    "--notes",
    "--reflection",
    "--reflect",
    required=True,
    help="Reflection notes for this step (or 'none')",
)
def task_next(notes: str):
    """Record reflection, verify gate, and advance to next step."""
    slug = resolve_slug(None)
    wf = _load_wf_from_disk(slug)
    prev_step_id = wf.current_step
    prev_board_alias = wf.current_board

    if wf.failed:
        out.die("Workflow is in failed state. Use 'ws task fail' info or start over.")

    if wf.current_step == "done":
        _finalize_workflow(slug)
        return

    if not notes or not notes.strip():
        out.die("--notes is required. Use --notes 'none' if nothing to report.")

    next_step, message = advance(
        slug,
        notes.strip(),
        defer_terminal_publication=prev_step_id == "reflect",
    )

    if message and next_step and message.startswith("Cannot advance"):
        out.error("gate", message)
        out.plain("task", "Complete the requirements above, then try again.")
        return

    if next_step is None:
        # Workflow finished — if we just advanced past reflect, run
        # the full completion logic (trace upload, mark PR ready, etc.)
        if prev_step_id == "reflect":
            _finalize_workflow(slug)
        else:
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


@task.command(name="back")
@click.option("--to", "target_step", required=True, help="Step ID to go back to")
@click.option("--reason", required=True, help="Why backtracking is needed")
@click.option("--board", "board_alias", default=None, help="Board alias (for per-board steps)")
def task_back(target_step: str, reason: str, board_alias: str | None):
    """Move workflow backward to a previous step.

    Does not discard any configs or state — only moves the workflow cursor.
    The reason is logged to reflections for auditability.
    """
    slug = resolve_slug(None)
    wf = _load_wf_from_disk(slug)

    if wf.current_step == "done":
        out.die("Workflow already complete — cannot go back.")

    # Allow backtracking from failed state — clears the failure
    if wf.failed:
        wf.failed = False
        wf.fail_reason = ""
        _save_wf_to_disk(slug, wf)
        out.info("task", "Cleared failed state")

    target, message = go_back(slug, target_step, reason, board_alias)

    if message:
        out.die(message)

    out.info("task", f"Moved back to step: {target.title}")
    out.plain("task", f"Reason: {reason}")

    # Show the target step instructions
    ws = load_workspace(slug)
    boards = list_boards(slug)
    wf = _load_wf_from_disk(slug)
    board = None
    if target.phase == "per_board" and wf.current_board:
        for b in boards:
            if b.alias == wf.current_board:
                board = b
                break

    ctx_vars = build_context(ws, boards, wf, board)
    instructions = render_step(target, ctx_vars)

    print()
    _print_step_header(target, wf, boards)
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


def _finalize_workflow(slug: str) -> None:
    """Durably transition the exact draft PR before publishing completion."""
    from src.workspace.filelock import company_lifecycle_lock

    with company_lifecycle_lock(slug):
        _finalize_workflow_locked(slug)


_READY_KEYS = {
    "version",
    "slug",
    "issue",
    "pr",
    "branch",
    "initial_provenance",
    "initial_head_oid",
    "kb_required",
    "kb_publish_oid",
    "claim_initially_present",
    "attempts",
}
_READY_ATTEMPT_KEYS = {"kb_push", "draft_recovery", "workflow_done", "claim_release"}
_PROVENANCE_KEYS = {
    "number",
    "head_ref_name",
    "head_ref_oid",
    "head_repository",
    "base_repository",
    "base_ref_name",
    "author_login",
    "is_draft",
    "closing_issues",
    "review_evidence",
    "hold_labels",
    "issue",
    "slug",
}
_OID_RE = re.compile(r"[0-9a-f]{40}")
_KB_PATH = "apps/crawler/src/workspace/kb/"


def _validate_ready_state(state: object, ws) -> dict:
    if not isinstance(state, dict) or set(state) != _READY_KEYS:
        raise WorkspaceError("Ready journal has an invalid exact schema")
    if state.get("version") != 3 or state.get("slug") != ws.slug:
        raise WorkspaceError("Ready journal version/slug is invalid")
    if (state.get("issue"), state.get("pr"), state.get("branch")) != (
        ws.issue,
        ws.pr,
        ws.branch,
    ):
        raise WorkspaceError("Ready journal no longer matches workspace ownership")
    provenance = state.get("initial_provenance")
    if state["pr"] is None:
        if provenance != {} or state.get("initial_head_oid") is not None:
            raise WorkspaceError("Ready journal has provenance without a PR")
    elif not isinstance(provenance, dict) or set(provenance) != _PROVENANCE_KEYS:
        raise WorkspaceError("Ready journal PR provenance schema is invalid")
    if not isinstance(provenance, dict):
        raise WorkspaceError("Ready journal provenance is invalid")
    for key in ("initial_head_oid", "kb_publish_oid"):
        value = state.get(key)
        if value is not None and (not isinstance(value, str) or not _OID_RE.fullmatch(value)):
            raise WorkspaceError(f"Ready journal {key} is invalid")
    if state["pr"] is not None and state["initial_head_oid"] != provenance["head_ref_oid"]:
        raise WorkspaceError("Ready journal head contradicts PR provenance")
    if not isinstance(state.get("kb_required"), bool) or not isinstance(
        state.get("claim_initially_present"), bool
    ):
        raise WorkspaceError("Ready journal boolean fields are invalid")
    if not state["kb_required"] and state["kb_publish_oid"] is not None:
        raise WorkspaceError("Ready journal records an unnecessary KB publication")
    attempts = state.get("attempts")
    if not isinstance(attempts, dict) or set(attempts) != _READY_ATTEMPT_KEYS:
        raise WorkspaceError("Ready journal attempts schema is invalid")
    if not all(isinstance(value, bool) for value in attempts.values()):
        raise WorkspaceError("Ready journal attempts must be boolean")
    expected_current = copy.deepcopy(provenance)
    if state["kb_publish_oid"] is not None:
        expected_current["head_ref_oid"] = state["kb_publish_oid"]
    if ws.pr is not None and ws.pr_provenance not in (provenance, expected_current):
        raise WorkspaceError("Workspace PR provenance contradicts ready journal")
    return state


def _save_ready_attempt(ws, state: dict, key: str) -> None:
    if key not in _READY_ATTEMPT_KEYS:
        raise WorkspaceError(f"Unknown ready attempt {key!r}")
    if not state["attempts"][key]:
        state["attempts"][key] = True
        ws.ready_state = _validate_ready_state(state, ws)
        save_workspace(ws)


def _kb_commit_message(ws) -> str:
    message = f"Add KB reflections for {ws.slug}"
    if ws.issue:
        message += f"\n\nRefs #{ws.issue}"
    return message


def _initialize_ready_state(ws, *, local: bool) -> dict:
    if local:
        provenance = copy.deepcopy(ws.pr_provenance) if ws.pr is not None else {}
        state = {
            "version": 3,
            "slug": ws.slug,
            "issue": ws.issue,
            "pr": ws.pr,
            "branch": ws.branch,
            "initial_provenance": provenance,
            "initial_head_oid": provenance.get("head_ref_oid"),
            "kb_required": False,
            "kb_publish_oid": None,
            "claim_initially_present": False,
            "attempts": {key: False for key in _READY_ATTEMPT_KEYS},
        }
    else:
        from src.workspace import git
        from src.workspace.commands.lifecycle import (
            _authenticate_workspace_worktree,
            _verify_workspace_pr_before_mutation,
        )

        if ws.pr is None:
            raise WorkspaceError("Submitted workspace has no PR for ready publication")
        _authenticate_workspace_worktree(ws)
        _verify_workspace_pr_before_mutation(ws)
        initial_head = git.current_head_oid_strict()
        if initial_head != ws.pr_provenance.get("head_ref_oid"):
            raise WorkspaceError("Local HEAD contradicts the reviewed PR head")
        changed = git.changed_paths_strict()
        if any(path != _KB_PATH.rstrip("/") and not path.startswith(_KB_PATH) for path in changed):
            raise WorkspaceError("Ready publication found changes outside the KB directory")
        state = {
            "version": 3,
            "slug": ws.slug,
            "issue": ws.issue,
            "pr": ws.pr,
            "branch": ws.branch,
            "initial_provenance": copy.deepcopy(ws.pr_provenance),
            "initial_head_oid": initial_head,
            "kb_required": bool(changed),
            "kb_publish_oid": None,
            "claim_initially_present": bool(ws.issue and git.is_issue_claimed_strict(ws.issue)),
            "attempts": {key: False for key in _READY_ATTEMPT_KEYS},
        }
    ws.ready_state = _validate_ready_state(state, ws)
    save_workspace(ws)
    return state


def _publish_journaled_kb(ws, state: dict) -> None:
    from src.workspace import git
    from src.workspace.commands.lifecycle import (
        _advance_workspace_worktree_head,
        _authenticate_workspace_worktree,
        _record_current_pr_provenance,
    )

    initial = state["initial_head_oid"]
    publish = state["kb_publish_oid"]
    message = _kb_commit_message(ws)
    if ws.pr is None:
        raise WorkspaceError("Journaled KB publication lost its PR number")

    def verify_initial_pr_lease() -> None:
        git.verify_recorded_pr(
            state["initial_provenance"],
            pr_number=ws.pr,
            branch=ws.branch,
            issue=ws.issue,
            slug=ws.slug,
        )

    current = git.current_head_oid_strict()
    changed = git.changed_paths_strict()
    _authenticate_workspace_worktree(ws)
    if publish is None:
        if current == initial:
            if not changed or any(
                path != _KB_PATH.rstrip("/") and not path.startswith(_KB_PATH) for path in changed
            ):
                raise WorkspaceError("Journaled KB changes disappeared or changed scope")
            git.add_files([_KB_PATH])
            _authenticate_workspace_worktree(ws)
            verify_initial_pr_lease()
            git.commit(message)
            current = git.current_head_oid_strict()
            _advance_workspace_worktree_head(ws, initial, current)
        elif changed:
            raise WorkspaceError("KB commit recovery found additional working-tree changes")
        git.verify_single_commit_strict(
            current,
            parent_oid=initial,
            allowed_prefix=_KB_PATH,
            message=message,
        )
        state["kb_publish_oid"] = current
        ws.ready_state = _validate_ready_state(state, ws)
        save_workspace(ws)
        publish = current
    else:
        if current != publish or changed:
            raise WorkspaceError("Local KB publication state contradicts ready journal")
        git.verify_single_commit_strict(
            publish,
            parent_oid=initial,
            allowed_prefix=_KB_PATH,
            message=message,
        )

    remote = git.remote_branch_oid_strict(ws.branch)
    if remote == initial:
        _save_ready_attempt(ws, state, "kb_push")
        _authenticate_workspace_worktree(ws)
        verify_initial_pr_lease()
        git.push_branch_at_expected_oid(ws.branch, publish, initial)
    elif remote == publish:
        if not state["attempts"]["kb_push"]:
            raise WorkspaceError("KB commit appeared remotely without a journaled push")
    else:
        raise WorkspaceError("Remote branch contradicts ready KB publication")
    if git.remote_branch_oid_strict(ws.branch) != publish:
        raise WorkspaceError("KB commit was not published at the exact journaled OID")

    if ws.pr_provenance == state["initial_provenance"]:
        _record_current_pr_provenance(
            ws,
            require_current_actor=False,
            expected_head_oid=publish,
        )
    expected = copy.deepcopy(state["initial_provenance"])
    expected["head_ref_oid"] = publish
    if ws.pr_provenance != expected:
        raise WorkspaceError("PR provenance did not reconcile to the KB publication")
    out.info("kb", "Committed and pushed KB updates.")


def _finalize_workflow_locked(slug: str) -> None:
    from src.workspace.commands.lifecycle import is_local_mode

    local = is_local_mode()
    ws = load_workspace(slug)
    wf = _load_wf_from_disk(slug)
    state = (
        _initialize_ready_state(ws, local=local)
        if not ws.ready_state
        else _validate_ready_state(ws.ready_state, ws)
    )

    if not local:
        from src.workspace import git
        from src.workspace.commands.lifecycle import _authenticate_workspace_worktree

        if ws.pr is None:
            raise WorkspaceError("Ready journal workspace lost its PR number")
        _authenticate_workspace_worktree(ws)
        if state["kb_required"]:
            _publish_journaled_kb(ws, state)
        else:
            if git.changed_paths_strict():
                raise WorkspaceError("Working tree changed after ready journaling")
            if git.current_head_oid_strict() != state["initial_head_oid"]:
                raise WorkspaceError("Local HEAD changed after ready journaling")
            if git.remote_branch_oid_strict(ws.branch) != state["initial_head_oid"]:
                raise WorkspaceError("Remote branch changed after ready journaling")

        effective = copy.deepcopy(state["initial_provenance"])
        if state["kb_publish_oid"] is not None:
            effective["head_ref_oid"] = state["kb_publish_oid"]
        if ws.pr_provenance != effective:
            raise WorkspaceError("Workspace provenance does not match ready publication")
        details = git.get_pr_details_strict(ws.pr)
        if details.get("state") != "OPEN":
            raise WorkspaceError("Ready journal PR is no longer open")
        current_provenance = git.pr_provenance(details, issue=ws.issue, slug=ws.slug)
        if details.get("isDraft") is True:
            if current_provenance != effective:
                raise WorkspaceError("Draft PR identity changed during ready publication")
            git.verify_recorded_pr(
                effective,
                pr_number=ws.pr,
                branch=ws.branch,
                issue=ws.issue,
                slug=ws.slug,
            )
        elif details.get("isDraft") is False:
            expected_ready = copy.deepcopy(effective)
            expected_ready["is_draft"] = False
            if current_provenance != expected_ready:
                raise WorkspaceError(
                    "PR readiness changed with review/head/ownership evidence; refusing mutation"
                )
            _save_ready_attempt(ws, state, "draft_recovery")
            _authenticate_workspace_worktree(ws)
            git.verify_pr_ready(
                effective,
                pr_number=ws.pr,
                branch=ws.branch,
                issue=ws.issue,
                slug=ws.slug,
            )
            git.mark_pr_draft(ws.pr)
            git.verify_recorded_pr(
                effective,
                pr_number=ws.pr,
                branch=ws.branch,
                issue=ws.issue,
                slug=ws.slug,
            )
            out.warn("github", f"PR #{ws.pr} became ready during automation; returned to draft")
        else:
            raise WorkspaceError("PR draft state is invalid")

        if state["attempts"]["draft_recovery"] and ws.issue:
            marker = f"<!-- resolver-ready-race:{ws.pr}:{effective['head_ref_oid']} -->"
            git.comment_on_issue_once(
                ws.issue,
                marker,
                (
                    f"{marker}\nResolver safety audit: PR #{ws.pr} became ready while "
                    f"the exact-head lease `{effective['head_ref_oid']}` was active. "
                    "It was returned to draft; no branch content was overwritten."
                ),
            )

        out.info(
            "github",
            f"PR #{ws.pr} remains draft pending independent exact-head review and required CI",
        )

    if wf.current_step == "reflect":
        _save_ready_attempt(ws, state, "workflow_done")
        wf.current_step = "done"
        _save_wf_to_disk(slug, wf)
        wf = _load_wf_from_disk(slug)
    elif wf.current_step == "done":
        if not state["attempts"]["workflow_done"]:
            raise WorkspaceError("Workflow became done without a journaled transition")
    else:
        raise WorkspaceError(f"Cannot finalize workflow from {wf.current_step!r}")
    if wf.current_step != "done":
        raise WorkspaceError("Workflow completion did not persist")

    claim_before_bookkeeping = False
    if ws.issue and not local:
        from src.workspace import git

        claim_before_bookkeeping = git.is_issue_claimed_strict(ws.issue)
        if state["claim_initially_present"]:
            if not claim_before_bookkeeping:
                if not state["attempts"]["claim_release"]:
                    raise WorkspaceError("Issue claim disappeared without a journaled release")
                out.info("task", "Workflow already complete.")
                return
        elif claim_before_bookkeeping:
            raise WorkspaceError("A new issue claim appeared during ready finalization")

    # Publish local/trace bookkeeping before claim release, which is the last
    # lifecycle mutation.
    action_log.append(ws_log_path(slug), "complete", True, "Workflow complete")
    non_none = [r for r in wf.reflections if r.get("notes", "none") != "none"]
    if not os.environ.get("JOBSEEK_CODEX_RUN_ID"):
        try:
            from src.workspace.trace import upload_trace_to_hf

            hf_url = upload_trace_to_hf(slug)
            if hf_url:
                out.info("trace", f"Uploaded: {hf_url}")
            else:
                out.plain("trace", "No matching transcript found — export manually if needed")
        except Exception as exc:
            out.warn("trace", f"Could not upload trace: {exc}")

    if ws.issue and not local and claim_before_bookkeeping:
        from src.workspace import git

        _save_ready_attempt(ws, state, "claim_release")
        claimed = git.is_issue_claimed_strict(ws.issue)
        if claimed:
            git.unclaim_issue_strict(ws.issue)
            if git.is_issue_claimed_strict(ws.issue):
                raise WorkspaceError("Issue claim survived ready finalization")

    out.info("task", "Workflow complete! Nice work. Do not pick another issue — stop here.")
    if non_none:
        print()
        out.plain("summary", f"{len(non_none)} reflection(s) recorded during this run.")


@task.command(name="complete")
def task_complete():
    """Mark workflow as done (only valid at the reflect step or already done)."""
    slug = resolve_slug(None)
    wf = _load_wf_from_disk(slug)

    if wf.current_step == "done":
        _finalize_workflow(slug)
        return

    if wf.current_step != "reflect":
        out.die(f"Can only complete from the 'reflect' step. Current step: {wf.current_step}")

    _finalize_workflow(slug)


@task.command(name="fail")
@click.option("--reason", required=True, help="Why the step cannot be completed")
def task_fail(reason: str):
    """Mark the current step as failed and enter exploration mode."""
    slug = resolve_slug(None)
    wf = _load_wf_from_disk(slug)

    wf.failed = True
    wf.fail_reason = reason
    _save_wf_to_disk(slug, wf)

    # Log failure (timestamp used for transcript discovery)
    action_log.append(ws_log_path(slug), "fail", False, f"Failed: {reason}")

    # Export trace before entering coding mode (best-effort)
    try:
        from src.shared.constants import get_data_dir
        from src.workspace.trace import export_trace

        trace_path = export_trace(slug, get_data_dir().parent / "traces")
        if trace_path:
            out.info("trace", f"Exported: {trace_path}")
        else:
            out.plain("trace", "No matching transcript found — trace not exported")
    except Exception as exc:
        out.warn("trace", f"Could not export trace: {exc}")

    _print_failed(wf)


@task.command(name="escalate")
@click.option("--issue", type=int, default=None, help="GitHub issue number")
@click.option("--reason", required=True, help="Why automation cannot safely finish")
@click.option("--follow-up", required=True, help="Concrete human or engineering follow-up")
def task_escalate(issue: int | None, reason: str, follow_up: str):
    """Record a terminal escalation after strictly cleaning resolver artifacts."""
    from src.workspace.commands.lifecycle import (
        _cleanup_resolver_artifacts,
        _resolve_outcome_workspace,
        is_local_mode,
    )

    slug, ws, issue = _resolve_outcome_workspace(slug=None, issue=issue)
    if not issue:
        out.die("Provide --issue or run from a workspace with a linked issue")
    assert issue is not None

    marker = "<!-- resolver-outcome: escalated -->"
    body = (
        f"{marker}\n"
        "**Resolver escalated this request for human follow-up.**\n\n"
        f"Reason: {reason}\n"
        f"Follow-up: {follow_up}"
    )
    local = is_local_mode()
    outcome = {
        "marker": marker,
        "body": body,
        "labels": [],
        "close_issue": True,
    }
    _cleanup_resolver_artifacts(
        issue=issue,
        slug=slug,
        ws=ws,
        local=local,
        outcome=outcome,
    )
    if local:
        out.warn("github", "Local mode — skipping escalation comment and issue close")
    else:
        out.info("github", f"Escalated and closed issue #{issue}")
    out.info("task", "Done. Do not pick another issue — stop here.")


@task.command(name="troubleshoot")
@click.argument("query", required=False, default=None)
@click.option("--view", default=None, help="Print the full content of a KB entry by filename")
def task_troubleshoot(query: str | None, view: str | None):
    """Search the knowledge base or view a full entry."""
    if view:
        content = read_kb_entry(view)
        if content is None:
            out.die(f"KB entry not found: {view}")
            return
        print(content)
        return

    if not query:
        out.die("Provide a search query or use --view <filename>")
        return

    results = search_kb(query)

    if not results:
        out.plain("kb", f"No matches for: {query}")
        out.plain("kb", "Try different keywords or check ws help troubleshooting")
        return

    out.plain("kb", f"Found {len(results)} match(es):")
    print()

    for r in results:
        if r["type"] == "case-study":
            summary = r.get("summary") or r["path"]
            print(f"  [case-study] {summary}")
            print(f"  Tags: {', '.join(r['tags'])}")
            print(f"  To view full study: ws task troubleshoot --view {r['path']}")
        else:
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


@task.command(name="casestudy")
@click.option("--company", required=True, help="Company slug")
@click.option("--monitor", required=True, help="Monitor type used")
@click.option("--scraper", required=True, help="Scraper type used")
@click.option("--tags", required=True, help="Comma-separated tags")
@click.option(
    "--summary", required=True, help="One-line summary of what makes this board interesting"
)
def task_casestudy(company: str, monitor: str, scraper: str, tags: str, summary: str):
    """Create a case study from a complex board configuration."""
    path = create_casestudy_entry(company, monitor, scraper, tags, summary)
    out.info("kb", f"Created case study: {path.name}")
    out.plain("kb", f"  Company: {company}")
    out.plain("kb", f"  Summary: {summary}")
    out.plain("kb", f"  Tags: {tags}")
    out.plain("kb", "Fill in the Key decisions and Config sections in the generated file.")


# ── Prompt helpers ───────────────────────────────────────────────────


def _get_industry_table() -> str:
    """Build a compact industry ID table for embedding in prompts."""
    import csv

    from src.shared.constants import get_data_dir

    path = get_data_dir() / "industries.csv"
    if not path.exists():
        return ""
    with open(path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    if not rows:
        return ""
    lines = [f"{'ID':>3}  {'Name':<30}"]
    lines.append(f"{'──':>3}  {'─' * 30}")
    for r in rows:
        lines.append(f"{r['id']:>3}  {r.get('en') or r.get('name', ''):<30}")
    return "\n".join(lines)


def _read_raw_template(name: str) -> str:
    """Read a parallel step template as raw text (no rendering)."""
    from pathlib import Path

    path = Path(__file__).parent.parent / "steps" / "parallel" / name
    if path.exists():
        return path.read_text()
    return ""


# ── Display helpers ──────────────────────────────────────────────────


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
    if skip_when == "scraper_auto_configured":
        return "scraper auto-configured by monitor"
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
