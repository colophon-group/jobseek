"""Lifecycle commands: new, use, reject, del, status, validate, submit."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime

import click

from src.shared.constants import SLUG_RE, get_data_dir
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
    load_workspace,
    resolve_board_alias,
    resolve_slug,
    save_board,
    save_workspace,
    set_active_slug,
    workspace_exists,
    ws_log_path,
)


def is_local_mode() -> bool:
    """Check if running in local-only mode (no git/GitHub interactions).

    Set ``WS_LOCAL=1`` to enable. Useful for debugging and testing configs
    without creating branches, PRs, or pushing to GitHub.
    """
    return os.environ.get("WS_LOCAL", "").strip() in ("1", "true", "yes")


def _load_existing_company(slug: str) -> dict[str, str]:
    """Load existing company data from companies.csv."""
    companies_path = get_data_dir() / "companies.csv"
    if not companies_path.exists():
        return {}
    _, rows = read_csv(companies_path)
    for r in rows:
        if r["slug"] == slug:
            return r
    return {}


def _load_existing_boards(slug: str) -> list[dict[str, str]]:
    """Load existing board rows from boards.csv for a company."""
    boards_path = get_data_dir() / "boards.csv"
    if not boards_path.exists():
        return []
    _, rows = read_csv(boards_path)
    return [r for r in rows if r.get("company_slug") == slug]


@click.command()
@click.argument("slug")
@click.option("--issue", type=int, default=None, help="GitHub issue number")
@click.option("--reconfig", is_flag=True, help="Reconfigure an existing company")
@click.option("--reset", is_flag=True, help="Purge managed clone and re-clone from scratch")
def new(slug: str, issue: int | None, reconfig: bool, reset: bool):
    """Create workspace + stub CSV row + branch + draft PR.

    With --reconfig, creates a workspace for an existing company to
    re-probe and update its monitor/scraper configuration.

    Idempotent: if a previous run partially succeeded, leftover state
    (workspace dir, worktree, local/remote branch) is cleaned up
    automatically on retry.
    """
    local = is_local_mode()

    # Ensure we have a repo clone when running in pip-installed mode
    if not local:
        from src.shared.constants import get_repo_root, set_repo_root

        if get_repo_root() is None or reset:
            from src.workspace.git import ensure_clone

            repo_root = ensure_clone(reset=reset)
            set_repo_root(repo_root)

    # Validate slug format
    if not SLUG_RE.match(slug):
        out.die(f"Invalid slug format: {slug!r}")

    # Clean up leftover workspace from a previous failed attempt
    if workspace_exists(slug):
        import contextlib

        out.warn("workspace", f"Cleaning up leftover workspace {slug!r}")
        if not reconfig:
            from src.csvtool import company_del

            with contextlib.suppress(CsvToolError):
                company_del(slug)
        delete_workspace(slug)
        if get_active_slug() == slug:
            clear_active_slug()

    # Check companies.csv
    companies_path = get_data_dir() / "companies.csv"
    slug_in_csv = False
    if companies_path.exists():
        _, rows = read_csv(companies_path)
        slug_in_csv = any(r["slug"] == slug for r in rows)

    if reconfig:
        if not slug_in_csv:
            out.die(
                f"Slug {slug!r} not found in companies.csv (--reconfig requires existing company)"
            )
    elif slug_in_csv:
        out.die(f"Slug {slug!r} already exists in companies.csv")

    branch = f"fix-crawler/{slug}" if reconfig else f"add-company/{slug}"
    pr_number: int | None = None
    pr_title = f"Reconfigure {slug}" if reconfig else f"Add {slug}"

    if local:
        out.warn("workspace", "Local mode — skipping git/GitHub operations")
        out.info("workspace", f"Slug {slug!r} is valid")
    else:
        from src.workspace import git

        # Check gh auth
        if not git.check_gh_auth():
            out.die("GitHub CLI not authenticated. Run: gh auth login")
        out.info("github", "gh authenticated")
        out.info("workspace", f"Slug {slug!r} is valid")

        # Reuse existing PR if one was created by a previous attempt
        if issue:
            existing = git.check_existing_prs(issue)
            if existing:
                pr_number = existing[0]["number"]
                out.info("github", f"Reusing existing PR #{pr_number} for issue #{issue}")

        # Create a worktree for this workspace so multiple agents
        # can work on different companies concurrently.
        # create_worktree handles stale worktrees and local branches.
        git.fetch()
        main = git.get_main_branch()
        worktree_path = git.worktrees_dir() / slug

        # Clean up stale remote branch (previous push that wasn't merged)
        git.delete_remote_branch(branch)

        git.create_worktree(branch, worktree_path, start_point=f"origin/{main}")
        set_repo_root(worktree_path)
        out.plain("git", f"Created worktree at {worktree_path} (branch {branch})")

    if not reconfig:
        # Add stub CSV row for new companies
        from src.csvtool import company_add

        company_add(slug)
        out.plain("csv", "Added stub row to companies.csv")

    if not local:
        from src.workspace import git

        if not reconfig:
            # Commit and push stub row
            git.add_files(["apps/crawler/data/companies.csv"])
            git.commit(f"Add {slug}")
            out.plain("git", f'Committed: "Add {slug}"')

            # Push branch (needed before creating PR)
            git.push(branch, set_upstream=True)
            out.plain("git", f"Pushed to origin/{branch}")

        # Create draft PR (unless reusing one or deferring for reconfig)
        if not pr_number and not reconfig:
            pr_body = f"Closes #{issue}" if issue else ""
            pr_number = git.create_draft_pr(
                title=pr_title,
                body=pr_body,
            )
            issue_ref = f" (closes #{issue})" if issue else ""
            out.info("github", f'Created draft PR #{pr_number} — "{pr_title}"{issue_ref}')

    # Create workspace
    worktree_str = "" if local else str(git.worktrees_dir() / slug)
    ws = Workspace(
        slug=slug,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        branch=branch,
        issue=issue,
        pr=pr_number,
        worktree=worktree_str,
    )

    # Pre-populate from existing CSV data when reconfiguring
    if reconfig:
        company_data = _load_existing_company(slug)
        if company_data:
            ws.name = company_data.get("name", "")
            ws.website = company_data.get("website", "")
            ws.logo_url = company_data.get("logo_url", "")
            ws.icon_url = company_data.get("icon_url", "")
            ws.logo_type = company_data.get("logo_type", "")
            out.info("reconfig", f"Loaded company: {ws.name or slug}")

    save_workspace(ws)

    # Pre-populate boards when reconfiguring
    if reconfig:
        existing_boards = _load_existing_boards(slug)
        for brow in existing_boards:
            board_slug = brow.get("board_slug", "")
            alias = (
                board_slug.removeprefix(f"{slug}-")
                if board_slug.startswith(f"{slug}-")
                else board_slug
            )
            board = Board(
                alias=alias,
                slug=board_slug,
                url=brow.get("board_url", ""),
            )
            save_board(slug, board)
            ws.active_board = alias
            out.info("reconfig", f"Loaded board: {alias} — {board.url}")
        save_workspace(ws)

    # For reconfig, advance workflow past setup/add_boards (already satisfied)
    if reconfig and existing_boards:
        from src.workspace.workflow import WorkflowState, _save_wf_to_disk

        wf = WorkflowState(
            current_step="select_monitor",
            current_board=existing_boards[0].get("board_slug", "").removeprefix(f"{slug}-"),
        )
        _save_wf_to_disk(slug, wf)

    # Set as active workspace
    set_active_slug(slug)

    # Log
    if local:
        log_msg = "Created workspace (local mode)"
    elif reconfig:
        log_msg = f"Created reconfig workspace, branch {branch}, draft PR #{pr_number}"
    else:
        log_msg = f"Created workspace, branch {branch}, draft PR #{pr_number}"
    action_log.append(ws_log_path(slug), "new", True, log_msg)

    out.plain("workspace", f"State: created (active: {slug})")


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
        resolved_alias = resolve_board_alias(ws_slug, target_board)
        path = board_yaml_path(ws_slug, resolved_alias)
        if not path.exists():
            out.die(f"Board {target_board!r} not found in workspace {ws_slug!r}")
        ws_obj = load_workspace(ws_slug)
        ws_obj.active_board = resolved_alias
        save_workspace(ws_obj)
        if resolved_alias != target_board:
            out.warn("board", f"Resolved {target_board!r} to alias {resolved_alias!r}")
        out.info("board", f"Active board: {ws_slug}-{resolved_alias} (alias: {resolved_alias})")


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
    local = is_local_mode()

    ws: Workspace | None = None

    # Explicit slug: validate it and use its linked issue when --issue omitted.
    if slug is not None:
        if not workspace_exists(slug):
            out.die(f"Workspace {slug!r} not found")
        ws = load_workspace(slug)
        if issue is None:
            issue = ws.issue
        elif ws.issue and ws.issue != issue:
            out.die(f"--issue {issue} does not match workspace {slug!r} (linked issue #{ws.issue})")

    # No explicit slug and no explicit issue: derive from active workspace.
    elif issue is None:
        active = get_active_slug()
        if active and workspace_exists(active):
            slug = active
            ws = load_workspace(active)
            issue = ws.issue

    # If --issue is provided without slug, keep it authoritative.
    if not issue:
        out.die("Provide --issue or a workspace slug with a linked issue")

    body = (
        f"<!-- validation-failed: {reason} -->\n"
        f"**This request could not be processed:** {message}\n\n"
        f"If this was closed in error, reopen the issue with additional context."
    )

    if local:
        out.warn("github", "Local mode — skipping issue comment and close")
        out.plain("github", f"Would comment on issue #{issue}: {reason}")
        out.plain("github", f"Would close issue #{issue}")
    else:
        from src.workspace import git

        git.comment_on_issue(issue, body)
        git.unclaim_issue(issue)
        git.close_issue(issue)
        out.info("github", f"Commented on issue #{issue} (validation-failed: {reason})")
        out.info("github", f"Closed issue #{issue}")

    out.info("task", "Done. Do not pick another issue — stop here.")

    if ws is not None:
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

    local = is_local_mode()
    slug = resolve_slug(slug)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    ws = load_workspace(slug)

    # Remove claim comment if issue is linked
    if ws.issue and not local:
        from src.workspace import git as _git

        _git.unclaim_issue(ws.issue)

    # Close PR if it exists
    if ws.pr:
        if local:
            out.warn("github", f"Local mode — skipping PR #{ws.pr} close")
        else:
            from src.workspace import git

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

    # Remove worktree and delete branch
    if ws.branch:
        if local:
            out.warn("git", f"Local mode — skipping branch {ws.branch} deletion")
        else:
            from pathlib import Path

            from src.workspace import git

            if ws.worktree:
                git.remove_worktree(Path(ws.worktree))
                # Pivot back to managed clone so git commands work
                from src.shared.constants import set_repo_root

                set_repo_root(git.managed_repo())
                out.info("git", f"Removed worktree {ws.worktree}")
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
        print(f"  LogoType: {ws.logo_type or '(not set)'}")
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
        out.die("No active workspace. Provide a slug or run: ws new <slug> --issue N")


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


_MAX_INLINE_CONFIG = 60


def _row(label: str, cells: list[str]) -> str:
    """Build a markdown table row: | label | cell1 | cell2 | ..."""
    return f"| {label} | " + " | ".join(cells) + " |"


def _short_config(config: dict) -> str:
    """Inline JSON if short, otherwise '*(configured)*'."""
    if not config:
        return ""
    s = json.dumps(config)
    if len(s) <= _MAX_INLINE_CONFIG:
        return f" · `{s}`"
    return " *(configured)*"


def _build_pr_body(ws: Workspace, boards: list[Board]) -> str:
    """Build enriched PR body with company info as a columnar table.

    Each board becomes a column. Metadata, field quality, and verdict
    are all in the same table.
    """
    from src.workspace.log import _format_field_quality

    lines = [f"Closes #{ws.issue}", ""] if ws.issue else [""]

    display_name = ws.name or ws.slug
    lines.append(f"## {display_name}")
    if ws.website:
        lines.append(ws.website)
    if ws.logo_type:
        lines.append(f"logo_type: {ws.logo_type}")
    lines.append("")

    # Full + minified logo preview (images committed to data/images/<slug>/)
    img_dir = get_data_dir() / "images" / ws.slug
    if img_dir.exists():
        from src.workspace import git

        try:
            repo = git.repo_name_with_owner()
            commit = git.current_commit()
            img_base = (
                f"https://raw.githubusercontent.com/{repo}/{commit}"
                f"/apps/crawler/data/images/{ws.slug}"
            )
            logo_files = list(img_dir.glob("logo.*"))
            icon_files = list(img_dir.glob("icon.*"))
            if logo_files or icon_files:
                lines.append("| Full Logo | Minified Logo |")
                lines.append("|-----------|----------------|")
                logo_cell = f"![full-logo]({img_base}/{logo_files[0].name})" if logo_files else "—"
                icon_cell = (
                    f"![minified-logo]({img_base}/{icon_files[0].name})" if icon_files else "—"
                )
                lines.append(f"| {logo_cell} | {icon_cell} |")
                lines.append("")
        except Exception:
            pass  # Skip image preview if git info unavailable

    slugs = [b.slug for b in boards]
    n = len(boards)

    # Table header
    lines.append(_row("", slugs))
    lines.append("|---" + "|---" * n + "|")

    # URL row
    lines.append(_row("URL", [b.url for b in boards]))

    # Monitor row
    monitor_cells = []
    for b in boards:
        cell = f"`{b.monitor_type}`" if b.monitor_type else "?"
        cell += _short_config(b.monitor_config)
        monitor_cells.append(cell)
    lines.append(_row("Monitor", monitor_cells))

    # Scraper row
    scraper_cells = []
    for b in boards:
        if b.scraper_type == "skip":
            scraper_cells.append("*(auto)*")
        elif b.scraper_type:
            cell = f"`{b.scraper_type}`"
            cell += _short_config(b.scraper_config)
            scraper_cells.append(cell)
        else:
            scraper_cells.append("—")
    lines.append(_row("Scraper", scraper_cells))

    # Jobs row
    job_cells = []
    for b in boards:
        job_count = (b.monitor_run or {}).get("jobs", "?")
        job_cells.append(str(job_count))
    lines.append(_row("Jobs", job_cells))

    # Cost row (only if any board has cost data)
    cost_cells = []
    any_cost = False
    for b in boards:
        cfg = (b.configs or {}).get(b.active_config or "")
        cost = (cfg or {}).get("cost", {})
        mon = cost.get("monitor_per_cycle") if cost else None
        if mon is not None:
            cost_cells.append(f"~{mon}s/cycle")
            any_cost = True
        else:
            cost_cells.append("—")
    if any_cost:
        lines.append(_row("Cost", cost_cells))

    # Field quality rows — union of all boards' feedback fields
    all_fields: list[str] = []
    seen: set[str] = set()
    for b in boards:
        cfg = (b.configs or {}).get(b.active_config or "")
        fb = (cfg or {}).get("feedback") or {}
        for field_name in fb.get("fields", {}):
            if field_name not in seen:
                all_fields.append(field_name)
                seen.add(field_name)

    for field_name in all_fields:
        cells = []
        for b in boards:
            cfg = (b.configs or {}).get(b.active_config or "")
            fb = (cfg or {}).get("feedback") or {}
            quality = fb.get("fields", {}).get(field_name)
            cells.append(_format_field_quality(quality) if quality else "—")
        lines.append(_row(field_name, cells))

    # Verdict row
    verdict_cells = []
    for b in boards:
        cfg = (b.configs or {}).get(b.active_config or "")
        fb = (cfg or {}).get("feedback") or {}
        verdict = fb.get("verdict")
        notes = fb.get("notes", "") or fb.get("verdict_notes", "")
        if verdict:
            cell = f"**{verdict}**"
            if notes:
                cell += f" — {notes}"
            verdict_cells.append(cell)
        else:
            verdict_cells.append("—")
    lines.append(_row("**Verdict**", verdict_cells))

    lines.append("")

    # Configs comparison (collapsed), grouped by board
    any_configs = any(len(b.configs or {}) > 1 for b in boards)
    if any_configs:
        lines.append("<details>")
        lines.append("<summary>Configurations evaluated</summary>")
        lines.append("")
        for b in boards:
            board_configs = b.configs or {}
            if len(board_configs) <= 1:
                continue
            lines.append(f"#### `{b.slug}`")
            lines.append("")
            lines.append("| # | Config | Monitor | Scraper | Jobs | Cost | Status | Notes |")
            lines.append("|---|--------|---------|---------|------|------|--------|-------|")
            for i, (name, cfg) in enumerate(board_configs.items(), 1):
                cfg_status = cfg.get("status", "?")
                mtype = cfg.get("monitor_type", "?")
                stype = cfg.get("scraper_type") or "—"
                cost = cfg.get("cost", {})
                mon_cost = cost.get("monitor_per_cycle")
                cost_str = f"~{mon_cost}s" if mon_cost is not None else "—"
                jobs = cfg.get("run", {}).get("jobs", "?") if cfg.get("run") else "—"
                fb = cfg.get("feedback")
                fb_verdict = fb.get("verdict", "") if fb else ""
                fb_notes = fb.get("verdict_notes", "") if fb else ""
                rejection = cfg.get("rejection_reason", "")
                # Build status cell
                if name == b.active_config:
                    status_cell = "**selected**"
                elif rejection:
                    status_cell = "rejected"
                else:
                    status_cell = cfg_status
                # Build notes — show verdict + notes or rejection reason
                if fb_notes:
                    notes = f"{fb_verdict}: {fb_notes}" if fb_verdict else fb_notes
                elif rejection:
                    notes = rejection
                elif fb_verdict:
                    notes = fb_verdict
                else:
                    notes = ""
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

    local = is_local_mode()

    if step_key == "csv_written":
        # Write company details (logo_url/icon_url for full/minified are filled by CI from R2)
        kwargs = {}
        if ws.name:
            kwargs["name"] = ws.name
        if ws.website:
            kwargs["website"] = ws.website
        if ws.logo_type:
            kwargs["logo_type"] = ws.logo_type
        if ws.description:
            kwargs["description"] = ws.description
        if ws.industry is not None:
            kwargs["industry"] = ws.industry
        if ws.employee_count_range is not None:
            kwargs["employee_count_range"] = ws.employee_count_range
        if ws.founded_year is not None:
            kwargs["founded_year"] = ws.founded_year
        if ws.enrichment_extras:
            import json as _json

            kwargs["extras"] = _json.dumps(ws.enrichment_extras)
        if kwargs:
            company_add(ws.slug, **kwargs)

        # Copy original image artifacts to data/images/<slug>/ for git commit
        from src.workspace.state import ws_dir

        artifacts = ws_dir(ws.slug) / "artifacts" / "company"
        img_dir = get_data_dir() / "images" / ws.slug
        for role in ("logo", "icon"):
            originals = list(artifacts.glob(f"{role}_original.*"))
            if originals:
                img_dir.mkdir(parents=True, exist_ok=True)
                src = originals[0]
                shutil.copy2(src, img_dir / f"{role}{src.suffix}")

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

        # Sort CSVs by slug to minimize merge conflicts
        from src.csvtool import sort_csvs

        sort_csvs()

    elif step_key == "validated":
        errors = validate_csvs()
        if errors:
            raise CsvToolError(f"CSV validation failed: {'; '.join(str(e) for e in errors[:3])}")

    elif step_key == "committed":
        if local:
            return  # Local mode — skip git commit
        from src.workspace import git

        # Stage only this company's files to avoid committing leftover
        # data from a previously submitted company branch
        commit_paths = [
            "apps/crawler/data/companies.csv",
            "apps/crawler/data/boards.csv",
            f"apps/crawler/data/images/{ws.slug}/",
            "apps/crawler/src/workspace/kb/",
        ]
        if not git.has_uncommitted_changes(commit_paths):
            return  # Nothing to commit — already done
        git.add_files(commit_paths)
        commit_msg = f"Configure {ws.name or ws.slug}"
        if ws.issue:
            commit_msg += f"\n\nCloses #{ws.issue}"
        git.commit(commit_msg)

    elif step_key == "pushed":
        if local:
            return  # Local mode — skip push
        from src.workspace import git

        git.push(ws.branch, set_upstream=True)

        # Create PR if it doesn't exist yet (e.g. reconfig deferred PR creation)
        if not ws.pr:
            pr_title = (
                f"Reconfigure {ws.name or ws.slug}"
                if ws.branch.startswith("fix-crawler/")
                else f"Add {ws.name or ws.slug}"
            )
            pr_body = f"Closes #{ws.issue}" if ws.issue else ""
            ws.pr = git.create_draft_pr(title=pr_title, body=pr_body)
            save_workspace(ws)
            out.info("github", f"Created draft PR #{ws.pr}")

    elif step_key == "pr_body_updated":
        if local:
            return  # Local mode — skip PR body update
        from src.workspace import git

        if ws.pr and boards:
            pr_body = _build_pr_body(ws, boards)
            git.edit_pr_body(ws.pr, pr_body)

    elif step_key == "stats_posted":
        if local:
            return  # Local mode — skip stats posting
        from src.workspace import git

        if ws.pr and boards:
            board_data = {b.alias: b.to_dict() for b in boards}
            stats_comment = action_log.format_crawl_stats(board_data)
            git.comment_on_pr(ws.pr, stats_comment)

    elif step_key == "transcript_posted":
        if local:
            return  # Local mode — skip transcript posting
        from src.workspace import git

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
        if local:
            return  # Local mode — skip PR ready
        from src.workspace import git

        if ws.pr:
            git.mark_pr_ready(ws.pr)

    elif step_key == "issue_completed":
        if local:
            return  # Local mode — skip issue comment
        from src.workspace import git

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

    # Ensure we're operating in the correct worktree. _pivot_to_worktree()
    # in main() may have failed if the active-slug file couldn't be found
    # (e.g. ppid changed between CLI invocations under Claude Code).
    if ws.worktree and not is_local_mode():
        from pathlib import Path

        from src.shared.constants import get_repo_root, set_repo_root

        wt = Path(ws.worktree)
        current_root = get_repo_root()
        if current_root and current_root != wt and (wt / "apps" / "crawler" / "data").exists():
            set_repo_root(wt)

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

    if is_local_mode():
        log_msg = "CSV updated, validated (local mode — git/PR steps skipped)"
    else:
        log_msg = f"CSV updated, validated, committed, pushed, PR #{ws.pr} ready"
    action_log.append(ws_log_path(slug), "submit", True, log_msg)

    out.info("workspace", "Submit complete")


# ── Resume ────────────────────────────────────────────────────────────


def _check_environment(ws: Workspace) -> list[tuple[str, str, str]]:
    """Check environment health. Returns [(code, message, severity), ...]."""
    if is_local_mode():
        return []  # Skip all git/gh checks in local mode

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
    if ws.logo_type:
        print(f"    OK Logo type: {ws.logo_type}")
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

    print()
