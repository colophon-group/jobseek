"""Lifecycle commands: new, use, reject, del, status, validate, submit."""

from __future__ import annotations

from datetime import UTC, datetime

import click

from src.shared.constants import DATA_DIR, SLUG_RE
from src.shared.csv_io import read_csv
from src.workspace import log as action_log
from src.workspace import output as out
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
        except Exception:
            out.warn("github", f"Could not close PR #{ws.pr}")

    # Delete CSV rows
    try:
        company_del(slug)
        out.info("csv", f"Removed {slug!r} from companies.csv (+ boards)")
    except SystemExit:
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
        print(f"  Active:  {ws.active_board or '(none)'}")
        print()

        if boards:
            print("  Boards:")
            for b in boards:
                active = " *" if b.alias == ws.active_board else ""
                monitor = b.monitor_type or "(none)"
                jobs = b.monitor_run.get("jobs", "?")
                print(f"    {b.slug}{active} — {monitor}, {jobs} jobs")
            print()

        # Progress
        print("  Progress:")
        for key, done in ws.progress.items():
            sym = "\u2713" if done else "\u2717"
            print(f"    {sym} {key}")
        print()
    else:
        workspaces = list_workspaces()
        if not workspaces:
            print("No workspaces found.")
            return
        active = get_active_slug()
        print()
        for ws in workspaces:
            submitted = "\u2713" if ws.progress.get("submitted") else " "
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
    """Build enriched PR body with company and board summary."""
    import json

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

        api_monitors = {"ashby", "greenhouse", "lever"}
        is_rich_api = b.monitor_type in api_monitors or (
            b.monitor_type == "api_sniffer" and (b.monitor_config or {}).get("fields")
        )
        if is_rich_api:
            lines.append("| Scraper | *(API — not needed)* |")
        elif b.scraper_type:
            scraper_cfg = ""
            if b.scraper_config:
                scraper_cfg = f" · `{json.dumps(b.scraper_config)}`"
            lines.append(f"| Scraper | `{b.scraper_type}`{scraper_cfg} |")

        job_count = (b.monitor_run or {}).get("jobs", "?")
        lines.append(f"| Jobs | {job_count} |")
        lines.append("")

    return "\n".join(lines)


@click.command()
@click.argument("slug", required=False)
@click.option("--summary", help="One-line summary for the transcript")
def submit(slug: str | None, summary: str | None):
    """Finalize: write CSV, validate, commit, push, post stats, mark PR ready."""
    from src.csvtool import board_add, company_add
    from src.inspect import validate_csvs
    from src.workspace import git

    slug = resolve_slug(slug)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    ws = load_workspace(slug)
    boards = list_boards(slug)

    # Advisory warnings
    if not boards:
        out.warn("submit", "No boards configured")
    for b in boards:
        if not b.monitor_type:
            out.warn("submit", f"Board {b.alias}: no monitor selected")
        if not b.monitor_run:
            out.warn("submit", f"Board {b.alias}: monitor not tested")
        # Check if scraper is needed but not tested
        api_monitors = {"ashby", "greenhouse", "lever"}
        is_rich_api = b.monitor_type in api_monitors or (
            b.monitor_type == "api_sniffer" and (b.monitor_config or {}).get("fields")
        )
        if b.monitor_type and not is_rich_api:
            if not b.scraper_type:
                out.warn("submit", f"Board {b.alias}: no scraper selected (non-API monitor)")
            elif not b.scraper_run:
                out.warn("submit", f"Board {b.alias}: scraper not tested")

    # Step 1: Write company details to CSV
    import json

    try:
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
            company_add(slug, **kwargs)
            out.info("csv", f"Updated company {slug!r} in companies.csv")
    except SystemExit:
        out.die("Failed to update company CSV row")

    # Step 2: Write board configs to CSV
    for b in boards:
        try:
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
            board_add(slug, **board_kwargs)
            out.info("csv", f"Added/updated board {b.slug!r} in boards.csv")
        except SystemExit:
            out.die(f"Failed to add/update board {b.slug!r}")

    # Step 3: Validate
    errors = validate_csvs()
    if errors:
        out.error("validate", f"CSV validation failed with {len(errors)} error(s):")
        for error in errors:
            out.error("validate", f"  {error}")
        out.die("Fix validation errors before submitting")
    out.info("validate", "CSV validation passed")

    # Step 4: Commit and push
    git.add_files(["data/"])
    commit_msg = f"Configure {ws.name or slug}"
    if ws.issue:
        commit_msg += f"\n\nCloses #{ws.issue}"
    git.commit(commit_msg)
    git.push()
    out.info("git", "Committed and pushed")

    # Step 5: Enrich PR body
    if ws.pr and boards:
        pr_body = _build_pr_body(ws, boards)
        git.edit_pr_body(ws.pr, pr_body)
        out.info("github", f"Updated PR #{ws.pr} body")

    # Step 6: Post crawl stats
    if boards:
        board_data = {b.alias: b.to_dict() for b in boards}
        stats_comment = action_log.format_crawl_stats(board_data)
        if ws.pr:
            git.comment_on_pr(ws.pr, stats_comment)
            out.info("github", f"Posted crawl stats on PR #{ws.pr}")

    # Step 7: Mark PR ready
    if ws.pr:
        git.mark_pr_ready(ws.pr)
        out.info("github", f"PR #{ws.pr} marked as ready for review")

    # Step 8: Post transcript
    if ws.issue:
        ws_log = action_log.read(ws_log_path(slug))
        board_logs = {b.alias: b.log for b in boards}
        transcript_body = action_log.format_transcript(ws_log, board_logs)

        summary_text = summary or f"Configured {ws.name or slug}"
        transcript_comment = (
            f"**Transcript summary**: {summary_text}\n\n"
            f"<details>\n<summary>Transcript</summary>\n\n"
            f"{transcript_body}\n\n"
            f"</details>"
        )
        git.comment_on_issue(ws.issue, transcript_comment)
        out.info("github", f"Posted transcript on issue #{ws.issue}")

    # Step 9: Update progress
    ws.progress["submitted"] = True
    save_workspace(ws)

    action_log.append(
        ws_log_path(slug),
        "submit",
        True,
        f"CSV updated, validated, committed, pushed, PR #{ws.pr} ready",
    )

    out.info("workspace", "Submit complete")
