"""Lifecycle commands: new, use, reject, del, status, validate, submit."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import click

from src.core.monitors import is_rich_monitor
from src.shared.constants import DATA_DIR, SLUG_RE
from src.shared.csv_io import read_csv
from src.workspace import log as action_log
from src.workspace import output as out
from src.workspace.errors import CsvToolError, GitError, GitHubApiError
from src.workspace.state import (
    Board,
    Workspace,
    clear_active_slug,
    delete_workspace,
    get_active_slug,
    list_boards,
    list_workspaces,
    load_workspace,
    resolve_slug,
    save_workspace,
    set_active_slug,
    workspace_exists,
    ws_log_path,
)


@click.command()
@click.argument("slug")
@click.option("--issue", required=True, type=int, help="GitHub issue number")
def new(slug: str, issue: int):
    """Create workspace + stub CSV row + branch + draft PR."""
    from src.workspace import git

    # Validate slug format
    if not SLUG_RE.match(slug):
        out.die(f"Invalid slug format: {slug!r}")

    # Check if already in companies.csv
    companies_path = DATA_DIR / "companies.csv"
    if companies_path.exists():
        _, rows = read_csv(companies_path)
        if any(r["slug"] == slug for r in rows):
            out.die(f"Slug {slug!r} already exists in companies.csv")

    # Check if workspace already exists
    if workspace_exists(slug):
        out.die(f"Workspace {slug!r} already exists")

    # Check gh auth
    if not git.check_gh_auth():
        out.die("GitHub CLI not authenticated. Run: gh auth login")
    out.info("github", "gh authenticated")
    out.info("workspace", f"Slug {slug!r} is valid, not in companies.csv")

    # Check for existing PRs
    existing = git.check_existing_prs(issue)
    if existing:
        pr = existing[0]
        out.error("github", f"Open PR #{pr['number']} already exists for issue #{issue}")
        out.die(f"PR #{pr['number']}: {pr['title']}")
    out.info("github", f"No open PRs for issue #{issue}")

    # Create branch
    branch = f"add-company/{slug}"
    git.create_branch(branch)
    out.plain("git", f"Created branch {branch}")

    # Add stub CSV row
    from src.csvtool import company_add

    company_add(slug)
    out.plain("csv", "Added stub row to companies.csv")

    # Commit and push
    git.add_files(["data/companies.csv"])
    git.commit(f"Add {slug}")
    out.plain("git", f'Committed: "Add {slug}"')

    git.push(branch, set_upstream=True)
    out.plain("git", f"Pushed to origin/{branch}")

    # Create draft PR
    pr_number = git.create_draft_pr(
        title=f"Add {slug}",
        body=f"Closes #{issue}",
    )
    out.info("github", f'Created draft PR #{pr_number} — "Add {slug}" (closes #{issue})')

    # Create workspace
    ws = Workspace(
        slug=slug,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        branch=branch,
        issue=issue,
        pr=pr_number,
    )
    save_workspace(ws)

    # Set as active workspace
    set_active_slug(slug)

    # Log
    action_log.append(
        ws_log_path(slug),
        "new",
        True,
        f"Created workspace, branch {branch}, draft PR #{pr_number}",
    )

    out.plain("workspace", f"State: created (active: {slug})")
    out.next_step('ws set --name "..." --website "..."')


@click.command()
@click.argument("slug", required=False)
@click.argument("board", required=False)
@click.option("--company", "-c", "company_opt", help="Set active workspace by slug")
@click.option("--board", "-b", "board_opt", help="Set active board by alias")
def use(slug: str | None, board: str | None, company_opt: str | None, board_opt: str | None):
    """Set active workspace and/or board.

    \b
    ws use <slug>                  Set active workspace
    ws use <slug> <board>          Set active workspace + board
    ws use --company <slug>        Set active workspace
    ws use --board <board>         Set active board (in current workspace)
    """
    from src.workspace.state import board_yaml_path

    # Resolve company slug: positional > --company > active
    target_slug = slug or company_opt
    # Resolve board alias: positional > --board
    target_board = board or board_opt

    if not target_slug and not target_board:
        out.die("Provide a workspace slug, --board, or both.")

    # Set workspace
    if target_slug:
        if not workspace_exists(target_slug):
            out.die(f"Workspace {target_slug!r} not found")
        set_active_slug(target_slug)
        out.info("workspace", f"Active workspace: {target_slug}")

    # Set board
    if target_board:
        ws_slug = target_slug or resolve_slug(None)
        if not workspace_exists(ws_slug):
            out.die(f"Workspace {ws_slug!r} not found")
        path = board_yaml_path(ws_slug, target_board)
        if not path.exists():
            out.die(f"Board {target_board!r} not found in workspace {ws_slug!r}")
        ws_obj = load_workspace(ws_slug)
        ws_obj.active_board = target_board
        save_workspace(ws_obj)
        out.info("board", f"Active board: {ws_slug}-{target_board}")


@click.command()
@click.argument("slug", required=False)
@click.option("--issue", type=int, help="GitHub issue number (if no workspace)")
@click.option(
    "--reason",
    required=True,
    type=click.Choice(
        [
            "not-a-company",
            "company-not-found",
            "no-job-board",
            "no-open-positions",
        ]
    ),
)
@click.option("--message", required=True, help="Human-readable explanation")
def reject(slug: str | None, issue: int | None, reason: str, message: str):
    """Comment + close an issue as rejected."""
    from src.workspace import git

    # Resolve slug from active workspace if not given explicitly
    if not slug:
        slug = get_active_slug()

    # Determine issue number
    if slug and workspace_exists(slug):
        ws = load_workspace(slug)
        issue = ws.issue
    if not issue:
        out.die("Provide --issue or a workspace slug with a linked issue")

    body = (
        f"<!-- validation-failed: {reason} -->\n"
        f"**This request could not be processed:** {message}\n\n"
        f"If this was closed in error, reopen the issue with additional context."
    )

    git.comment_on_issue(issue, body)
    git.close_issue(issue)
    out.info("github", f"Commented on issue #{issue} (validation-failed: {reason})")
    out.info("github", f"Closed issue #{issue}")

    if slug and workspace_exists(slug):
        action_log.append(
            ws_log_path(slug),
            "reject",
            True,
            f"Rejected issue #{issue}: {reason}",
        )


@click.command(name="del")
@click.argument("slug", required=False)
def del_(slug: str | None):
    """Remove workspace + CSV rows + close PR + delete branch."""
    from src.csvtool import company_del
    from src.workspace import git

    slug = resolve_slug(slug)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    ws = load_workspace(slug)

    # Close PR if it exists
    if ws.pr:
        try:
            git.close_pr(ws.pr)
            out.info("github", f"Closed PR #{ws.pr}")
        except GitHubApiError:
            out.warn("github", f"Could not close PR #{ws.pr}")

    # Delete CSV rows
    try:
        company_del(slug)
        out.info("csv", f"Removed {slug!r} from companies.csv (+ boards)")
    except CsvToolError:
        out.warn("csv", f"Company {slug!r} not found in CSV (may not have been added)")

    # Delete branch
    if ws.branch:
        git.delete_branch(ws.branch, remote=True)
        out.info("git", f"Deleted branch {ws.branch}")

    # Delete workspace directory
    delete_workspace(slug)
    out.info("workspace", f"Removed workspace {slug!r}")

    # Clear active if this was the active workspace
    if get_active_slug() == slug:
        clear_active_slug()


@click.command()
@click.argument("slug", required=False)
def status(slug: str | None):
    """Show workspace state (specific or list all)."""
    # If no slug given, try active workspace for detail view
    if not slug:
        slug = get_active_slug()

    if slug:
        if not workspace_exists(slug):
            out.die(f"Workspace {slug!r} not found")
        ws = load_workspace(slug)
        boards = list_boards(slug)

        active_marker = " (active)" if get_active_slug() == slug else ""
        print(f"\nWorkspace: {ws.slug}{active_marker}")
        print(f"  Branch:  {ws.branch}")
        print(f"  Issue:   #{ws.issue}" if ws.issue else "  Issue:   (none)")
        print(f"  PR:      #{ws.pr}" if ws.pr else "  PR:      (none)")
        print(f"  Name:    {ws.name or '(not set)'}")
        print(f"  Website: {ws.website or '(not set)'}")
        print()

        if boards:
            print("  Boards:")
            for b in boards:
                active = " *" if b.alias == ws.active_board else ""
                if b.active_config:
                    cfg = (b.configs or {}).get(b.active_config, {})
                    mtype = cfg.get("monitor_type", "?")
                    status_str = cfg.get("status", "?")
                    jobs = (cfg.get("run") or {}).get("jobs", "?")
                    cost = (cfg.get("cost") or {}).get("monitor_per_cycle")
                    cost_str = f" ~{cost}s" if cost is not None else ""
                    fb = cfg.get("feedback", {})
                    verdict = fb.get("verdict", "") if fb else ""
                    verdict_str = f" [{verdict}]" if verdict else ""
                    print(
                        f"    {b.slug}{active} — {b.active_config}"
                        f" ({mtype}, {jobs} jobs{cost_str}){verdict_str}"
                    )
                    if status_str == "selected":
                        print("      -> Not tested yet")
                else:
                    print(f"    {b.slug}{active} — no config selected")
            print()

            # Show config history if multiple configs exist
            has_multi = any(len(b.configs or {}) > 1 for b in boards)
            if has_multi:
                print("  Configs:")
                for b in boards:
                    for name, cfg in (b.configs or {}).items():
                        mtype = cfg.get("monitor_type", "?")
                        cfg_status = cfg.get("status", "?")
                        sel = " *" if name == b.active_config else ""
                        print(f"    {name}{sel} ({mtype}) — {cfg_status}")
                print()

        # Progress
        submitted = ws.submitted
        all_ready = all(not _check_board_readiness(b) for b in boards) if boards else False

        if submitted:
            print("  Status: submitted")
        elif all_ready:
            print("  Status: ready to submit")
        elif not boards:
            print("  Status: no boards configured")
        else:
            print("  Status: in progress")

        # Last error
        if ws.last_error:
            print()
            cmd = ws.last_error.get("command", "?")
            step = ws.last_error.get("step")
            err = ws.last_error.get("error", "?")
            step_str = f" (step: {step})" if step else ""
            print(f"  Last error: {cmd}{step_str} — {err}")

        print()
    else:
        workspaces = list_workspaces()
        if not workspaces:
            print("No workspaces found.")
            return
        active = get_active_slug()
        print()
        for ws in workspaces:
            submitted = "\u2713" if ws.submitted else " "
            issue_str = f"#{ws.issue}" if ws.issue else ""
            pr_str = f"PR #{ws.pr}" if ws.pr else ""
            marker = " *" if ws.slug == active else ""
            print(f"  [{submitted}] {ws.slug}{marker:<20} {issue_str:<8} {pr_str}")
        print()


@click.command()
def validate():
    """Run CSV validation."""
    from src.inspect import validate_csvs

    errors = validate_csvs()
    if errors:
        print(f"Validation failed with {len(errors)} error(s):\n")
        for error in errors:
            print(f"  {error}")
        raise SystemExit(1)
    else:
        out.info("validate", "CSV validation passed")


def _build_pr_body(ws: Workspace, boards: list[Board]) -> str:
    """Build enriched PR body with company info, board configs, and quality data."""
    lines = [f"Closes #{ws.issue}", ""]

    display_name = ws.name or ws.slug
    lines.append(f"## {display_name}")
    lines.append("")

    for b in boards:
        lines.append("| | |")
        lines.append("|---|---|")
        if ws.website:
            lines.append(f"| Website | {ws.website} |")
        lines.append(f"| Board | `{b.slug}` — {b.url} |")

        monitor_cfg = ""
        if b.monitor_config:
            monitor_cfg = f" · `{json.dumps(b.monitor_config)}`"
        lines.append(f"| Monitor | `{b.monitor_type}`{monitor_cfg} |")

        if is_rich_monitor(b.monitor_type, b.monitor_config):
            lines.append("| Scraper | *(API — not needed)* |")
        elif b.scraper_type:
            scraper_cfg = ""
            if b.scraper_config:
                scraper_cfg = f" · `{json.dumps(b.scraper_config)}`"
            lines.append(f"| Scraper | `{b.scraper_type}`{scraper_cfg} |")

        job_count = (b.monitor_run or {}).get("jobs", "?")
        lines.append(f"| Jobs | {job_count} |")

        # Show cost if available
        cfg = (b.configs or {}).get(b.active_config or "")
        if cfg and cfg.get("cost"):
            cost = cfg["cost"]
            mon = cost.get("monitor_per_cycle")
            if mon is not None:
                lines.append(f"| Cost | ~{mon}s/cycle |")

        lines.append("")

        # Extraction quality from feedback
        if cfg and cfg.get("feedback"):
            fb = cfg["feedback"]
            verdict = fb.get("verdict", "?")
            lines.append("### Extraction Quality")
            lines.append("")
            lines.append("| Field | Quality |")
            lines.append("|-------|---------|")
            from src.workspace.log import _format_field_quality

            for field_name, quality in fb.get("fields", {}).items():
                lines.append(f"| {field_name} | {_format_field_quality(quality)} |")
            notes = fb.get("notes", "")
            lines.append("")
            lines.append(f"**Verdict**: {verdict}")
            if notes:
                lines.append(f" — {notes}")
            lines.append("")

    # Configs comparison (collapsed)
    all_configs = []
    for b in boards:
        for name, cfg in (b.configs or {}).items():
            cfg_status = cfg.get("status", "?")
            mtype = cfg.get("monitor_type", "?")
            stype = cfg.get("scraper_type") or "—"
            cost = cfg.get("cost", {})
            mon_cost = cost.get("monitor_per_cycle")
            cost_str = f"~{mon_cost}s" if mon_cost is not None else "—"
            jobs = cfg.get("run", {}).get("jobs", "?") if cfg.get("run") else "—"
            fb = cfg.get("feedback")
            fb_verdict = fb.get("verdict", "") if fb else ""
            rejection = cfg.get("rejection_reason", "")
            # Build status cell
            if name == b.active_config:
                status_cell = "**selected**"
            elif rejection:
                status_cell = f"rejected: {rejection}"
            else:
                status_cell = cfg_status
            # Build notes
            notes = fb_verdict if fb_verdict else ""
            all_configs.append((name, mtype, stype, jobs, cost_str, status_cell, notes))

    if len(all_configs) > 1:
        lines.append("<details>")
        lines.append("<summary>Configurations evaluated</summary>")
        lines.append("")
        lines.append("| # | Config | Monitor | Scraper | Jobs | Cost | Status | Notes |")
        lines.append("|---|--------|---------|---------|------|------|--------|-------|")
        for i, (name, mtype, stype, jobs, cost_str, status_cell, notes) in enumerate(  # noqa: E501
            all_configs,
            1,
        ):
            lines.append(
                f"| {i} | {name} | `{mtype}` | {stype}"
                f" | {jobs} | {cost_str} | {status_cell} | {notes} |"
            )
        lines.append("")
        lines.append("</details>")
        lines.append("")

    return "\n".join(lines)


# ── Submit step registry ──────────────────────────────────────────────

# (key, description, critical)
# Critical steps abort on failure; non-critical warn and continue.
SUBMIT_STEPS: list[tuple[str, str, bool]] = [
    ("csv_written", "Write company/board CSVs", True),
    ("validated", "Validate CSVs", True),
    ("committed", "Commit changes", True),
    ("pushed", "Push to remote", True),
    ("pr_body_updated", "Update PR body", False),
    ("stats_posted", "Post crawl stats on PR", False),
    ("transcript_posted", "Post transcript on PR", False),
    ("pr_ready", "Mark PR ready for review", False),
    ("issue_completed", "Post completion on issue", False),
]


def _execute_submit_step(
    step_key: str,
    ws: Workspace,
    boards: list[Board],
    summary: str | None,
) -> None:
    """Execute a single submit step. Raises on failure."""
    from src.csvtool import board_add, company_add
    from src.inspect import validate_csvs
    from src.workspace import git

    if step_key == "csv_written":
        # Write company details
        kwargs = {}
        if ws.name:
            kwargs["name"] = ws.name
        if ws.website:
            kwargs["website"] = ws.website
        if ws.logo_url:
            kwargs["logo_url"] = ws.logo_url
        if ws.icon_url:
            kwargs["icon_url"] = ws.icon_url
        if kwargs:
            company_add(ws.slug, **kwargs)

        # Write board configs
        for b in boards:
            board_kwargs: dict = {
                "board_slug": b.slug,
                "board_url": b.url,
            }
            if b.monitor_type:
                board_kwargs["monitor_type"] = b.monitor_type
            if b.monitor_config:
                board_kwargs["monitor_config"] = json.dumps(b.monitor_config)
            if b.scraper_type:
                board_kwargs["scraper_type"] = b.scraper_type
            if b.scraper_config:
                board_kwargs["scraper_config"] = json.dumps(b.scraper_config)
            board_add(ws.slug, **board_kwargs)

    elif step_key == "validated":
        errors = validate_csvs()
        if errors:
            raise CsvToolError(f"CSV validation failed: {'; '.join(errors[:3])}")

    elif step_key == "committed":
        if not git.has_uncommitted_changes(["data/"]):
            return  # Nothing to commit — already done
        git.add_files(["data/"])
        commit_msg = f"Configure {ws.name or ws.slug}"
        if ws.issue:
            commit_msg += f"\n\nCloses #{ws.issue}"
        git.commit(commit_msg)

    elif step_key == "pushed":
        if not git.is_ahead_of_remote():
            return  # Already pushed
        git.push()

    elif step_key == "pr_body_updated":
        if ws.pr and boards:
            pr_body = _build_pr_body(ws, boards)
            git.edit_pr_body(ws.pr, pr_body)

    elif step_key == "stats_posted":
        if ws.pr and boards:
            board_data = {b.alias: b.to_dict() for b in boards}
            stats_comment = action_log.format_crawl_stats(board_data)
            git.comment_on_pr(ws.pr, stats_comment)

    elif step_key == "transcript_posted":
        if ws.pr:
            ws_log = action_log.read(ws_log_path(ws.slug))
            board_logs = {b.alias: b.log for b in boards}
            transcript_body = action_log.format_transcript(ws_log, board_logs)
            summary_text = summary or f"Configured {ws.name or ws.slug}"
            transcript_comment = (
                f"**Summary**: {summary_text}\n\n"
                f"<details>\n<summary>Agent transcript</summary>\n\n"
                f"{transcript_body}\n\n"
                f"</details>"
            )
            git.comment_on_pr(ws.pr, transcript_comment)

    elif step_key == "pr_ready":
        if ws.pr:
            git.mark_pr_ready(ws.pr)

    elif step_key == "issue_completed":
        if ws.issue:
            total_jobs = sum((b.monitor_run or {}).get("jobs", 0) for b in boards)
            display_name = ws.name or ws.slug
            body = f"**{display_name}** has been added — {total_jobs} open positions found.\n\n"
            if ws.pr:
                body += f"Merging #{ws.pr} will activate monitoring."
            git.comment_on_issue(ws.issue, body)


@click.command()
@click.argument("slug", required=False)
@click.option("--summary", help="One-line summary for the transcript")
@click.option("--force", is_flag=True, help="Force submit despite poor quality verdict")
def submit(slug: str | None, summary: str | None, force: bool):
    """Finalize: write CSV, validate, commit, push, post stats, mark PR ready."""
    from src.workspace.commands.crawl import run_quality_gates

    slug = resolve_slug(slug)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    ws = load_workspace(slug)
    boards = list_boards(slug)

    # Quality gates
    blockers, warnings = run_quality_gates(ws, boards)
    for w in warnings:
        out.warn("submit", w)
    if blockers:
        poor_only = all("poor" in b for b in blockers)
        if not force or not poor_only:
            for b in blockers:
                out.error("submit", b)
            out.die("Quality gates failed. Fix issues or use --force.")
        else:
            for b in blockers:
                out.warn("submit", f"(forced) {b}")

    # Stale submit detection: if config selections changed since last submit, restart
    current_configs = {b.alias: b.active_config for b in boards}
    prev_configs = ws.submit_state.get("_active_configs")
    if prev_configs and prev_configs != current_configs:
        out.warn("submit", "Board config changed since last submit — restarting")
        ws.submit_state = {}

    ws.submit_state["_active_configs"] = current_configs

    # Execute steps with checkpointing
    for step_key, step_desc, critical in SUBMIT_STEPS:
        if ws.submit_state.get(step_key):
            out.plain("submit", f"OK {step_desc} (done)")
            continue

        try:
            _execute_submit_step(step_key, ws, boards, summary)
            ws.submit_state[step_key] = True
            save_workspace(ws)
            out.info("submit", f"OK {step_desc}")
        except (GitError, CsvToolError) as e:
            if critical:
                ws.last_error = {
                    "command": "submit",
                    "step": step_key,
                    "error": str(e),
                    "at": datetime.now(UTC).isoformat(),
                }
                save_workspace(ws)
                out.die(f"{step_desc} failed: {e}")
            else:
                out.warn("submit", f"{step_desc} failed: {e}")

    # Clear last_error on success
    ws.last_error = {}
    save_workspace(ws)

    action_log.append(
        ws_log_path(slug),
        "submit",
        True,
        f"CSV updated, validated, committed, pushed, PR #{ws.pr} ready",
    )

    out.info("workspace", "Submit complete")


# ── Resume ────────────────────────────────────────────────────────────


# Priority-ordered: first matching issue determines the "Next:" suggestion.
_NEXT_STEPS: list[tuple[str | None, str]] = [
    ("branch_missing", "Branch not found — recreate with: git checkout -b {branch}"),
    ("wrong_branch", "git checkout {branch}"),
    ("pr_merged", "PR is already merged — workspace is complete"),
    ("pr_closed", "PR is closed — reopen or create a new workspace"),
    ("no_name", 'ws set --name "..." --website "..."'),
    ("no_website", 'ws set --name "..." --website "..."'),
    ("no_boards", "ws add board <alias> --url <url>"),
    ("no_config", "ws probe monitor --current-jobs N"),
    ("config_rejected", "ws probe monitor --current-jobs N"),
    ("config_missing", "ws probe monitor --current-jobs N"),
    ("not_tested", "ws run monitor"),
    ("zero_jobs", "ws select monitor <type> --as <name>"),
    ("no_feedback", 'ws feedback "<config-name>"'),
    ("unusable", "ws select monitor <type> --as <name>"),
    ("poor_quality", "ws submit --force  # or try another config"),
    (None, "ws submit"),
]


def _check_environment(ws: Workspace) -> list[tuple[str, str, str]]:
    """Check environment health. Returns [(code, message, severity), ...]."""
    from src.workspace import git

    issues: list[tuple[str, str, str]] = []

    # Branch exists locally?
    if ws.branch:
        try:
            result = git._run(["git", "branch", "--list", ws.branch], check=False)
            if ws.branch not in result.stdout:
                issues.append(
                    (
                        "branch_missing",
                        f"Branch {ws.branch} not found locally",
                        "critical",
                    )
                )
            else:
                current = git.current_branch()
                if current != ws.branch:
                    issues.append(
                        (
                            "wrong_branch",
                            f"On {current}, expected {ws.branch}",
                            "warning",
                        )
                    )
        except Exception:
            issues.append(("git_error", "Could not check git state", "warning"))

    # PR still open?
    if ws.pr:
        try:
            result = git._run(["gh", "pr", "view", str(ws.pr), "--json", "state"], check=False)
            if result.returncode == 0:
                state = json.loads(result.stdout).get("state")
                if state == "MERGED":
                    issues.append(("pr_merged", f"PR #{ws.pr} is already merged", "info"))
                elif state != "OPEN":
                    issues.append(("pr_closed", f"PR #{ws.pr} is {state}", "warning"))
        except Exception:
            pass  # Skip if gh not available

    return issues


def _check_workspace_completeness(ws: Workspace, boards: list[Board]) -> list[tuple[str, str, str]]:
    """Check workspace data completeness."""
    issues: list[tuple[str, str, str]] = []
    if not ws.name:
        issues.append(("no_name", "Company name not set", "warning"))
    if not ws.website:
        issues.append(("no_website", "Company website not set", "warning"))
    if not boards:
        issues.append(("no_boards", "No boards configured", "critical"))
    return issues


def _check_board_readiness(board: Board) -> list[tuple[str, str, str]]:
    """Check per-board readiness."""
    issues: list[tuple[str, str, str]] = []

    if not board.active_config:
        issues.append(("no_config", f"Board {board.alias}: no config selected", "critical"))
        return issues

    cfg = (board.configs or {}).get(board.active_config)
    if not cfg:
        issues.append(
            (
                "config_missing",
                f"Board {board.alias}: config {board.active_config!r} not found",
                "critical",
            )
        )
        return issues

    status = cfg.get("status", "selected")
    if status == "rejected":
        issues.append(
            (
                "config_rejected",
                f"Board {board.alias}: active config is rejected",
                "critical",
            )
        )
    elif status == "selected":
        issues.append(("not_tested", f"Board {board.alias}: config not tested yet", "warning"))
    elif status == "tested":
        run = cfg.get("run") or {}
        if run.get("jobs", 0) == 0:
            issues.append(("zero_jobs", f"Board {board.alias}: 0 jobs found", "critical"))
        fb = cfg.get("feedback")
        if not fb:
            issues.append(("no_feedback", f"Board {board.alias}: no feedback recorded", "warning"))
        elif fb.get("verdict") == "unusable":
            issues.append(("unusable", f"Board {board.alias}: verdict is unusable", "critical"))
        elif fb.get("verdict") == "poor":
            issues.append(("poor_quality", f"Board {board.alias}: verdict is poor", "warning"))

    return issues


@click.command()
@click.argument("slug", required=False)
def resume(slug: str | None):
    """Analyze workspace state and suggest next action."""
    slug = resolve_slug(slug)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    ws = load_workspace(slug)
    boards = list_boards(slug)

    print(f"\n  Workspace: {ws.slug}", end="")
    if ws.branch:
        print(f" (branch: {ws.branch}", end="")
        if ws.pr:
            print(f", PR #{ws.pr}", end="")
        print(")", end="")
    print()

    # Phase 1: Environment
    all_issues: list[tuple[str, str, str]] = []
    env_issues = _check_environment(ws)
    if env_issues or ws.branch:
        print("\n  Environment:")
        if ws.branch and not any(c == "branch_missing" for c, _, _ in env_issues):
            print("    OK Branch exists")
        for _code, msg, severity in env_issues:
            sym = "!!" if severity == "critical" else "!"
            print(f"    {sym} {msg}")
        if ws.pr and not any(c == "pr_closed" for c, _, _ in env_issues):
            print(f"    OK PR #{ws.pr} is open")
    all_issues.extend(env_issues)

    # Phase 2: Workspace completeness
    ws_issues = _check_workspace_completeness(ws, boards)
    print("\n  Company:")
    if ws.name:
        print(f"    OK Name: {ws.name}")
    if ws.website:
        print(f"    OK Website: {ws.website}")
    for _code, msg, _severity in ws_issues:
        print(f"    !! {msg}")
    all_issues.extend(ws_issues)

    # Phase 3: Per-board readiness
    for b in boards:
        board_issues = _check_board_readiness(b)
        print(f"\n  Board: {b.slug}")

        if b.active_config:
            cfg = (b.configs or {}).get(b.active_config, {})
            mtype = cfg.get("monitor_type", "?")
            jobs = (cfg.get("run") or {}).get("jobs", "?")
            cost = (cfg.get("cost") or {}).get("monitor_per_cycle")
            cost_str = f", ~{cost}s" if cost is not None else ""
            print(f"    OK Config: {b.active_config} ({mtype}, {jobs} jobs{cost_str})")

            fb = cfg.get("feedback")
            if fb:
                print(f"    OK Feedback: {fb.get('verdict', '?')}")

        if not board_issues:
            print("    -> Ready")
        for _code, msg, severity in board_issues:
            sym = "!!" if severity == "critical" else "!"
            print(f"    {sym} {msg}")
        all_issues.extend(board_issues)

    # Last error
    if ws.last_error:
        print("\n  Last error:")
        cmd = ws.last_error.get("command", "?")
        step = ws.last_error.get("step")
        err = ws.last_error.get("error", "?")
        at = ws.last_error.get("at", "?")
        step_str = f" (step: {step})" if step else ""
        print(f"    Command: {cmd}{step_str}")
        print(f"    Error: {err}")
        print(f"    At: {at}")

    # Next step suggestion
    issue_codes = {c for c, _, _ in all_issues}
    next_cmd = None
    for code, suggestion in _NEXT_STEPS:
        if code is None or code in issue_codes:
            next_cmd = suggestion.format(branch=ws.branch or "?")
            break

    print()
    if next_cmd:
        out.next_step(next_cmd)
    print()
