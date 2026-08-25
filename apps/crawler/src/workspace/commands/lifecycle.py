"""Lifecycle commands: new, use, reject, del, status, validate, submit."""

from __future__ import annotations

import copy
import json
import os
import shutil
from datetime import UTC, datetime
from functools import wraps

import click

from src.shared.constants import SLUG_RE, get_data_dir
from src.shared.csv_io import read_csv
from src.workspace import log as action_log
from src.workspace import output as out
from src.workspace.errors import CsvToolError, WorkspaceError
from src.workspace.state import (
    Board,
    Workspace,
    clear_active_slug,
    delete_workspace,
    get_active_slug,
    list_boards,
    list_workspaces,
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


def _serialize_company_lifecycle(func):
    """Hold the reentrant per-company lock for a complete lifecycle command."""

    @wraps(func)
    def locked(*args, **kwargs):
        slug = kwargs.get("slug") if "slug" in kwargs else (args[0] if args else None)
        if slug is None:
            slug = resolve_slug(None)
        if not isinstance(slug, str):
            raise WorkspaceError("Could not determine company slug for lifecycle lock")
        from src.workspace.filelock import company_lifecycle_lock

        with company_lifecycle_lock(slug):
            return func(*args, **kwargs)

    return locked


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
@click.argument("query")
def search(query: str):
    """Search existing companies by name, slug, or website.

    Checks if a company already exists before creating a workspace.
    """
    companies_path = get_data_dir() / "companies.csv"
    if not companies_path.exists():
        out.info("search", "No companies.csv found")
        return

    _, rows = read_csv(companies_path)
    q = query.lower()
    matches = []
    for r in rows:
        slug = r.get("slug", "")
        name = r.get("name", "")
        website = r.get("website", "")
        if q in slug.lower() or q in name.lower() or q in website.lower():
            matches.append(r)

    if not matches:
        out.info("search", f"No companies matching '{query}'")
        return

    out.info("search", f"{len(matches)} match(es) for '{query}':")
    for r in matches:
        slug = r.get("slug", "?")
        name = r.get("name", "?")
        website = r.get("website", "")
        industry = r.get("industry", "")
        out.plain("search", f"  {slug} — {name} ({website}) [industry={industry}]")


@click.command()
@click.argument("slug")
@click.option("--issue", type=int, default=None, help="GitHub issue number")
@click.option("--pr", "pr_opt", type=int, default=None, help="Attach to existing PR number")
@click.option("--reconfig", is_flag=True, help="Reconfigure an existing company")
@click.option("--reset", is_flag=True, help="Purge managed clone and re-clone from scratch")
@click.option("--start-at", default=None, help="Start workflow at this step (reconfig only)")
@_serialize_company_lifecycle
def new(
    slug: str,
    issue: int | None,
    pr_opt: int | None,
    reconfig: bool,
    reset: bool,
    start_at: str | None,
):
    """Create a local workspace, stub CSV row, and branch.

    With --reconfig, creates a workspace for an existing company to
    re-probe and update its monitor/scraper configuration.

    With --pr N, attaches to an existing pull request instead of
    creating a new one. Otherwise the draft PR is created by ws submit
    after the complete configuration is committed and pushed.

    Idempotent: if a previous run partially succeeded, leftover state
    (workspace dir, worktree, local/remote branch) is cleaned up
    automatically on retry.
    """
    local = is_local_mode()
    inventory_seed = None

    # Ensure we have a repo clone with latest main when running in pip-installed mode
    if not local:
        from src.shared.constants import set_repo_root
        from src.workspace.git import ensure_clone

        repo_root = ensure_clone(reset=reset)
        set_repo_root(repo_root)

    # Validate slug format
    if not SLUG_RE.match(slug):
        out.die(f"Invalid slug format: {slug!r}")

    # A second same-slug process may have waited on the lifecycle lock while
    # the first process published this state.  Never interpret that valid
    # state as stale and delete it; make retries an idempotent resume instead.
    if workspace_exists(slug):
        existing_ws = load_workspace(slug)
        if issue is not None and existing_ws.issue != issue:
            out.die(
                f"Workspace {slug!r} belongs to issue #{existing_ws.issue}, not issue #{issue}; "
                "refusing to replace it"
            )
        if pr_opt is not None and existing_ws.pr != pr_opt:
            out.die(
                f"Workspace {slug!r} belongs to PR #{existing_ws.pr}, not PR #{pr_opt}; "
                "refusing to replace it"
            )
        set_active_slug(slug)
        out.info("workspace", f"Workspace {slug!r} already exists; resumed without mutation")
        return

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
        # Orphaned CSV row from a previous failed attempt (workspace YAML
        # was already cleaned up above but CSV row survived).  Clean it up
        # and continue instead of dying.
        import contextlib

        from src.csvtool import company_del

        out.warn("csv", f"Slug {slug!r} found in CSV without workspace — cleaning up orphaned row")
        with contextlib.suppress(CsvToolError):
            company_del(slug)

    branch = f"fix-crawler/{slug}" if reconfig else f"add-company/{slug}"
    pr_number: int | None = None
    pr_details: dict | None = None
    automatic_resume = False

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

        # Inventory-generated issues carry redundant, content-addressed
        # evidence.  Parse it here (after auth, before state creation) so
        # normal human requests remain byte-for-byte on the established path.
        if issue:
            from src.workspace.ats_seed import (
                InventorySeedInvalid,
                issue_has_inventory_label,
                parse_inventory_seed,
            )

            try:
                issue_data = git.fetch_issue(issue)
                if issue_has_inventory_label(issue_data):
                    inventory_seed = parse_inventory_seed(str(issue_data.get("body") or ""))
            except InventorySeedInvalid as exc:
                out.warn(
                    "inventory",
                    f"Ignoring invalid ATS inventory seed ({exc}); using normal discovery",
                )
            except Exception as exc:
                out.warn(
                    "inventory",
                    f"Could not load ATS inventory seed ({exc}); using normal discovery",
                )

        base_ref = git.get_main_branch()

        # Attach to explicit PR or reuse one from a previous attempt. Automatic
        # reuse is stricter than explicit operator attachment: it also binds the
        # PR author and structured issue relationship.
        if pr_opt:
            pr_number = pr_opt
            pr_details = git.get_pr_details_strict(pr_number)
            git.validate_pr_attachment(
                pr_details,
                pr_number=pr_number,
                branch=branch,
                base_ref=base_ref,
                issue=issue,
                slug=slug,
                authorized_actor=None,
            )
            out.info("github", f"Attaching to existing PR #{pr_number} (branch {branch})")
        elif issue:
            existing = git.check_existing_prs_strict(issue)
            if existing:
                classification = git.classify_issue_prs(existing)
                if classification != "resumable":
                    out.die(
                        f"Issue #{issue} has {classification} linked PR state; "
                        "refusing to attach automatically"
                    )
                pr_number = existing[0]["number"]
                if not isinstance(pr_number, int):
                    out.die(f"Issue #{issue} linked PR has no valid number")
                issue_data = git.fetch_issue(issue)
                labels = {
                    label.get("name")
                    for label in issue_data.get("labels", [])
                    if isinstance(label, dict)
                }
                if "company-request" not in labels:
                    out.die(
                        f"Issue #{issue} is not labelled company-request; "
                        "refusing automatic PR attachment"
                    )
                pr_details = git.get_pr_details_strict(pr_number)
                actor = git.get_authenticated_login_strict()
                git.validate_pr_attachment(
                    pr_details,
                    pr_number=pr_number,
                    branch=branch,
                    base_ref=base_ref,
                    issue=issue,
                    slug=slug,
                    authorized_actor=actor,
                )
                automatic_resume = True
                out.info(
                    "github",
                    f"Reusing existing PR #{pr_number} for issue #{issue} (branch {branch})",
                )

        # Branch names are deterministic per company.  A different issue can
        # therefore resolve to the same canonical slug (for example, two ATS
        # inventory candidates for one company) without being linked to the
        # PR that already owns the branch.  Deleting that remote branch would
        # silently close the active PR.  Check branch ownership independently
        # of issue linkage and fail closed until the active PR is resolved.
        if not pr_number:
            branch_pr = git.find_open_pr_for_branch(branch)
            if branch_pr:
                issue_detail = f" while starting issue #{issue}" if issue else ""
                out.die(
                    f"Branch {branch!r} is owned by open PR #{branch_pr}{issue_detail}; "
                    "refusing to delete an active PR. Do not close or replace it; "
                    "leave this issue pending and retry after that PR is resolved."
                )

        # Create a worktree for this workspace so multiple agents
        # can work on different companies concurrently.
        # create_worktree handles stale worktrees and local branches.
        git.fetch()
        worktree_path = git.worktrees_dir() / slug

        if pr_number:
            # Attach to the existing PR branch, then integrate current main.
            # The outer resolver worktree starts at origin/main, but this
            # managed company worktree is where all subsequent work happens;
            # leaving it at the historical draft tip reintroduces stale code
            # and unrelated diffs.
            assert pr_details is not None
            expected_head = str(pr_details["headRefOid"])
            remote_head = git.remote_branch_oid_strict(branch)
            if remote_head != expected_head:
                raise WorkspaceError(
                    f"PR #{pr_number} branch changed during attachment; refusing to continue"
                )
            git.create_worktree(branch, worktree_path, start_point=expected_head)
        else:
            main = git.get_main_branch()
            # Clean up stale remote branch (previous push that wasn't merged)
            git.delete_remote_branch(branch)
            git.create_worktree(branch, worktree_path, start_point=f"origin/{main}")
        set_repo_root(worktree_path)
        if pr_number:
            try:
                git.sync_branch_with_main(branch)
            except Exception:
                # Do not leave a half-merged worktree registered as active.
                # Restore the managed-clone root before propagating the error
                # so the runner can safely retry or escalate.
                git.remove_worktree(worktree_path)
                set_repo_root(repo_root)
                raise
        out.plain("git", f"Created worktree at {worktree_path} (branch {branch})")

    resume_existing_pr = False
    if not reconfig:
        # Add stub CSV row for new companies
        from src.csvtool import company_add
        from src.workspace.errors import NothingToUpdateError

        try:
            company_add(slug)
            out.plain("csv", "Added stub row to companies.csv")
        except NothingToUpdateError:
            # Slug already present in worktree CSV from a previous attempt
            out.warn("csv", f"Slug {slug!r} already in worktree CSV — continuing")
            if pr_number is not None:
                # Preserve existing aliases and metadata from the authenticated
                # resumed branch instead of creating a second alias that only
                # collides when ws submit writes the CSVs.
                resume_existing_pr = True
                out.info("workspace", "Loading existing configuration from resumed PR")
        except Exception:
            # Clean up worktree before re-raising unexpected errors
            if not local:
                from src.workspace import git as _git

                _git.remove_worktree(worktree_path)
            raise

    # Create workspace
    worktree_str = "" if local else str(git.worktrees_dir() / slug)
    ws = Workspace(
        slug=slug,
        created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        branch=branch,
        issue=issue,
        pr=pr_number,
        pr_provenance=(
            git.pr_provenance(pr_details, issue=issue, slug=slug)
            if not local and pr_details is not None
            else {}
        ),
        worktree=worktree_str,
    )

    # Pre-populate from existing CSV data when reconfiguring or resuming a PR
    # that already contains this company.
    load_existing_config = reconfig or resume_existing_pr
    if load_existing_config:
        company_data = _load_existing_company(slug)
        if company_data:
            ws.name = company_data.get("name", "")
            ws.website = company_data.get("website", "")
            ws.logo_url = company_data.get("logo_url", "")
            ws.icon_url = company_data.get("icon_url", "")
            ws.logo_type = company_data.get("logo_type", "")
            ws.industry = int(company_data["industry"]) if company_data.get("industry") else None
            ws.employee_count_range = (
                int(company_data["employee_count_range"])
                if company_data.get("employee_count_range")
                else None
            )
            ws.founded_year = (
                int(company_data["founded_year"]) if company_data.get("founded_year") else None
            )
            out.info("reconfig", f"Loaded company: {ws.name or slug}")

        # Load descriptions from company_descriptions.csv
        desc_path = get_data_dir() / "company_descriptions.csv"
        if desc_path.exists():
            _, desc_rows = read_csv(desc_path)
            for r in desc_rows:
                if r.get("slug") == slug:
                    for locale in ("en", "de", "fr", "it"):
                        if r.get(locale):
                            ws.descriptions[locale] = r[locale]
                    if ws.descriptions:
                        out.info("reconfig", f"Loaded descriptions: {', '.join(ws.descriptions)}")
                    break

    existing_boards = _load_existing_boards(slug) if load_existing_config else []
    seeded_board = None
    if inventory_seed is not None:
        from src.workspace.ats_seed import (
            apply_inventory_fallback,
            apply_inventory_seed,
            available_inventory_board_alias,
            current_registry_hard_evidence,
        )

        evidence = current_registry_hard_evidence(
            inventory_seed,
            companies_path=get_data_dir() / "companies.csv",
            boards_path=get_data_dir() / "boards.csv",
        )
        if evidence:
            codes = ", ".join(item.code for item in evidence)
            reason = f"current registry hard evidence: {codes}"
            apply_inventory_fallback(ws, inventory_seed, reason)
            out.warn(
                "inventory",
                f"Seed now matches the current registry ({codes}); using normal discovery",
            )
        else:
            existing_aliases: set[str] = set()
            for row in existing_boards:
                board_slug = row.get("board_slug", "")
                alias = (
                    board_slug.removeprefix(f"{slug}-")
                    if board_slug.startswith(f"{slug}-")
                    else board_slug
                )
                existing_aliases.add(alias)
            seeded_board = apply_inventory_seed(
                ws,
                inventory_seed,
                board_alias=available_inventory_board_alias(existing_aliases),
            )

    if automatic_resume:
        # Reauthenticate the exact ref immediately before publishing local
        # ownership state. The earlier check cannot authorize a later ref.
        refreshed = git.get_pr_details_strict(pr_number)
        git.validate_pr_attachment(
            refreshed,
            pr_number=pr_number,
            branch=branch,
            base_ref=base_ref,
            issue=issue,
            slug=slug,
            authorized_actor=git.get_authenticated_login_strict(),
        )
        expected_head = str(pr_details["headRefOid"])
        if (
            refreshed.get("headRefOid") != expected_head
            or git.remote_branch_oid_strict(branch) != expected_head
        ):
            raise WorkspaceError(
                f"PR #{pr_number} branch changed before workspace publication; refusing resume"
            )
        pr_details = refreshed
        ws.pr_provenance = git.pr_provenance(refreshed, issue=issue, slug=slug)

    save_workspace(ws)

    if seeded_board is not None:
        save_board(slug, seeded_board)
        out.info(
            "inventory",
            f"Seeded {inventory_seed.monitor_type} monitor on {inventory_seed.board_url}",
        )
        out.plain(
            "inventory",
            "Run the seeded monitor first; probe normally if it fails or returns no jobs",
        )

    # Pre-populate boards when reconfiguring
    if load_existing_config:
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
        if seeded_board is not None:
            ws.active_board = seeded_board.alias
        save_workspace(ws)

    # For reconfig, advance workflow past setup/add_boards (already satisfied)
    if load_existing_config and (existing_boards or seeded_board is not None):
        from src.workspace.workflow import WorkflowState, _all_step_defs, _save_wf_to_disk

        step_id = start_at or "select_monitor"
        valid_ids = {s.id for s in _all_step_defs()}
        if step_id not in valid_ids:
            out.die(f"Unknown step: {step_id!r}. Valid: {', '.join(sorted(valid_ids))}")

        wf = WorkflowState(
            current_step=step_id,
            current_board=(
                seeded_board.alias
                if seeded_board is not None
                else existing_boards[0].get("board_slug", "").removeprefix(f"{slug}-")
            ),
        )
        _save_wf_to_disk(slug, wf)
    elif start_at:
        out.warn("new", "--start-at is only used with --reconfig, ignoring")

    # Set as active workspace
    set_active_slug(slug)

    # Log
    if local:
        log_msg = "Created workspace (local mode)"
    elif pr_number:
        log_msg = f"Created workspace, branch {branch}, attached PR #{pr_number}"
    elif reconfig:
        log_msg = f"Created reconfig workspace, branch {branch}; PR deferred until submit"
    else:
        log_msg = f"Created workspace, branch {branch}; PR deferred until submit"
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
            "duplicate",
        ]
    ),
)
@click.option("--message", required=True, help="Human-readable explanation")
def reject(slug: str | None, issue: int | None, reason: str, message: str):
    """Atomically clean resolver artifacts, comment, and reject an issue."""
    local = is_local_mode()
    slug, ws, issue = _resolve_outcome_workspace(slug=slug, issue=issue)
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
        if reason in ("duplicate", "subsidiary"):
            out.plain("github", f"Would add {reason!r} label to issue #{issue}")
        out.plain("github", f"Would close issue #{issue}")
    else:
        from src.workspace import git

        _cleanup_resolver_artifacts(issue=issue, slug=slug, ws=ws, local=False)
        git.comment_on_issue_once(issue, f"<!-- validation-failed: {reason} -->", body)
        if reason in ("duplicate", "subsidiary"):
            git.add_label_to_issue(issue, reason)
        git.unclaim_issue_strict(issue)
        git.close_issue_if_open(issue)
        out.info("github", f"Commented on issue #{issue} (validation-failed: {reason})")
        out.info("github", f"Closed issue #{issue}")

    out.info("task", "Done. Do not pick another issue — stop here.")

    if local:
        _cleanup_resolver_artifacts(issue=issue, slug=slug, ws=ws, local=True)


def _resolve_outcome_workspace(
    *,
    slug: str | None,
    issue: int | None,
) -> tuple[str | None, Workspace | None, int | None]:
    """Resolve a unique workspace without letting active state override --issue."""
    if slug is not None:
        if not workspace_exists(slug):
            out.die(f"Workspace {slug!r} not found")
        ws = load_workspace(slug)
        if issue is None:
            issue = ws.issue
        elif ws.issue and ws.issue != issue:
            out.die(f"--issue {issue} does not match workspace {slug!r} (linked issue #{ws.issue})")
        return slug, ws, issue

    if issue is not None:
        matches = [workspace for workspace in list_workspaces() if workspace.issue == issue]
        if len(matches) > 1:
            choices = ", ".join(sorted(workspace.slug for workspace in matches))
            out.die(f"Multiple workspaces match issue #{issue}: {choices}")
        if matches:
            return matches[0].slug, matches[0], issue
        return None, None, issue

    active = get_active_slug()
    if active and workspace_exists(active):
        ws = load_workspace(active)
        return active, ws, ws.issue
    return None, None, None


def _cleanup_resolver_artifacts(
    *,
    issue: int,
    slug: str | None,
    ws: Workspace | None,
    local: bool,
) -> None:
    """Remove one resolver attempt before its issue is terminally closed.

    GitHub and branch failures intentionally propagate.  The issue remains
    open, so rerunning the terminal command safely resumes cleanup.
    """
    from src.workspace.filelock import company_lifecycle_lock

    lock_slug = slug or (ws.slug if ws is not None else None)
    if lock_slug is None and not local:
        # Discover a possible slug only to select the lock. The complete query
        # and all validation are repeated after acquiring it.
        from src.workspace import git

        candidates = git.check_existing_prs_strict(issue)
        if len(candidates) == 1:
            branch = candidates[0].get("headRefName")
            if isinstance(branch, str) and branch.startswith("add-company/"):
                lock_slug = branch.removeprefix("add-company/")

    if lock_slug is None:
        _cleanup_resolver_artifacts_locked(issue=issue, slug=slug, ws=ws, local=local)
        return
    with company_lifecycle_lock(lock_slug):
        _cleanup_resolver_artifacts_locked(issue=issue, slug=lock_slug, ws=ws, local=local)


def _cleanup_resolver_artifacts_locked(
    *,
    issue: int,
    slug: str | None,
    ws: Workspace | None,
    local: bool,
) -> None:
    """Locked implementation for terminal resolver cleanup."""
    if slug and workspace_exists(slug):
        ws = load_workspace(slug)
    branch = ws.branch if ws and ws.branch else ""
    pr_number = ws.pr if ws else None

    if not local and (ws is None or not ws.terminal_state):
        from src.workspace import git

        linked_prs = git.check_existing_prs_strict(issue)
        classification = git.classify_issue_prs(linked_prs)
        if classification in {"submitted", "conflicting"}:
            raise WorkspaceError(
                f"Issue #{issue} has {classification} linked PR state; refusing automatic cleanup"
            )
        linked_numbers = {pr.get("number") for pr in linked_prs}
        if pr_number is not None:
            if linked_numbers != {pr_number}:
                raise WorkspaceError(
                    f"Workspace PR #{pr_number} is not the sole structured PR for issue #{issue}"
                )
            assert ws is not None
            git.verify_recorded_pr(
                ws.pr_provenance,
                pr_number=pr_number,
                branch=branch,
                issue=issue,
                slug=ws.slug,
                allow_closed=True,
            )
        elif linked_prs:
            linked = linked_prs[0]
            number = linked.get("number")
            linked_branch = linked.get("headRefName")
            if not isinstance(number, int) or not isinstance(linked_branch, str):
                raise WorkspaceError(f"Issue #{issue} linked PR metadata is incomplete")
            if slug is None:
                if not linked_branch.startswith("add-company/"):
                    raise WorkspaceError(f"Issue #{issue} linked branch has no company slug")
                slug = linked_branch.removeprefix("add-company/")
            branch = f"add-company/{slug}"
            details = git.get_pr_details_strict(number)
            issue_data = git.fetch_issue(issue)
            labels = {
                label.get("name")
                for label in issue_data.get("labels", [])
                if isinstance(label, dict)
            }
            if "company-request" not in labels:
                raise WorkspaceError(f"Issue #{issue} is not labelled company-request")
            git.validate_pr_attachment(
                details,
                pr_number=number,
                branch=branch,
                base_ref=git.get_main_branch(),
                issue=issue,
                slug=slug,
                authorized_actor=git.get_authenticated_login_strict(),
            )
            pr_number = number
            expected_remote_oid = str(details["headRefOid"])
            if git.remote_branch_oid_strict(branch) != expected_remote_oid:
                raise WorkspaceError(f"PR #{number} remote ref changed during terminal cleanup")
            assert slug is not None
            ws = Workspace(
                slug=slug,
                created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                branch=branch,
                issue=issue,
                pr=pr_number,
                pr_provenance=git.pr_provenance(details, issue=issue, slug=slug),
                worktree=str(git.worktrees_dir() / slug),
            )
            save_workspace(ws)
        elif branch and git.remote_branch_oid_strict(branch) is not None:
            raise WorkspaceError(
                f"Remote branch {branch!r} exists without exact PR provenance; refusing cleanup"
            )

    if ws is not None:
        action_log.append(
            ws_log_path(ws.slug),
            "cleanup",
            True,
            f"Cleaning terminal resolver artifacts for issue #{issue}",
        )

    if ws is not None:
        _run_terminal_cleanup(ws, local=local)


def _run_terminal_cleanup(ws: Workspace, *, local: bool) -> None:
    """Resume one journaled terminal transition to completion."""
    from pathlib import Path

    from src.csvtool import company_del
    from src.workspace import git

    branch = ws.branch
    if branch and branch not in {f"add-company/{ws.slug}", f"fix-crawler/{ws.slug}"}:
        raise WorkspaceError(f"Workspace branch {branch!r} is not bound to slug {ws.slug!r}")
    if not branch and (ws.pr is not None or ws.worktree):
        raise WorkspaceError("Workspace has remote/local artifacts without a recorded branch")

    state = ws.terminal_state
    if not state:
        expected_remote_oid: str | None = None
        if not local:
            if ws.pr is not None:
                git.verify_recorded_pr(
                    ws.pr_provenance,
                    pr_number=ws.pr,
                    branch=branch,
                    issue=ws.issue,
                    slug=ws.slug,
                    allow_closed=True,
                )
                expected_remote_oid = str(ws.pr_provenance["head_ref_oid"])
            elif branch and git.remote_branch_oid_strict(branch) is not None:
                raise WorkspaceError(f"Remote branch {branch!r} exists without exact PR provenance")

        expected_worktree = Path(os.path.abspath(str(git.worktrees_dir() / ws.slug)))
        recorded_worktree = (
            Path(os.path.abspath(os.path.expanduser(ws.worktree))) if ws.worktree else None
        )
        if not local and ws.worktree and recorded_worktree != expected_worktree:
            raise WorkspaceError(
                f"Workspace {ws.slug!r} records unexpected worktree {ws.worktree!r}"
            )
        worktree_head = (
            git.managed_worktree_head_strict(expected_worktree, branch)
            if not local and ws.worktree
            else None
        )
        state = {
            "version": 1,
            "slug": ws.slug,
            "branch": branch,
            "issue": ws.issue,
            "pr": ws.pr,
            "expected_remote_oid": expected_remote_oid,
            "worktree": str(expected_worktree) if not local else None,
            "worktree_head": worktree_head,
            "remote_delete_attempted": False,
            "remote_deleted": expected_remote_oid is None,
            "pr_close_attempted": False,
            "pr_closed": ws.pr is None,
            "worktree_remove_attempted": False,
            "worktree_removed": worktree_head is None,
            "local_branch_remove_attempted": False,
            "local_branch_removed": local or not branch,
            "claim_release_attempted": False,
            "claim_released": local or ws.issue is None,
            "data_removed": False,
        }
        ws.terminal_state = state
        save_workspace(ws)
    else:
        immutable = {"slug": ws.slug, "branch": branch, "issue": ws.issue, "pr": ws.pr}
        if any(state.get(key) != value for key, value in immutable.items()):
            raise WorkspaceError("Terminal journal no longer matches workspace ownership")

    expected_remote_oid = state.get("expected_remote_oid")
    if not local and expected_remote_oid is not None and not state.get("remote_deleted"):
        if not state.get("remote_delete_attempted"):
            git.verify_recorded_pr(
                ws.pr_provenance,
                pr_number=ws.pr,
                branch=branch,
                issue=ws.issue,
                slug=ws.slug,
                allow_closed=True,
            )
            state["remote_delete_attempted"] = True
            save_workspace(ws)
        git.delete_remote_branch_at_expected_oid(
            branch,
            str(expected_remote_oid),
            absent_is_success=True,
        )
        state["remote_deleted"] = True
        save_workspace(ws)

    if not local and ws.pr is not None and not state.get("pr_closed"):
        if not state.get("pr_close_attempted"):
            state["pr_close_attempted"] = True
            save_workspace(ws)
        git.close_pr_if_open(ws.pr)
        state["pr_closed"] = True
        save_workspace(ws)

    worktree = state.get("worktree")
    worktree_head = state.get("worktree_head")
    if not local and worktree and worktree_head and not state.get("worktree_removed"):
        if not state.get("worktree_remove_attempted"):
            git.authenticate_managed_worktree(Path(worktree), branch, worktree_head)
            state["worktree_remove_attempted"] = True
            save_workspace(ws)
        git.remove_authenticated_worktree(
            Path(worktree), branch, worktree_head, absent_is_success=True
        )
        state["worktree_removed"] = True
        save_workspace(ws)
        from src.shared.constants import set_repo_root

        set_repo_root(git.managed_repo())

    if not local and not state.get("local_branch_removed"):
        if not state.get("local_branch_remove_attempted"):
            state["local_branch_remove_attempted"] = True
            save_workspace(ws)
        git.delete_local_branch_strict(branch)
        state["local_branch_removed"] = True
        save_workspace(ws)

    if not local and ws.issue and not state.get("claim_released"):
        if not state.get("claim_release_attempted"):
            state["claim_release_attempted"] = True
            save_workspace(ws)
        git.unclaim_issue_strict(ws.issue)
        state["claim_released"] = True
        save_workspace(ws)

    if not state.get("data_removed") and not branch.startswith("fix-crawler/"):
        try:
            company_del(ws.slug)
            out.info("csv", f"Removed {ws.slug!r} from companies.csv (+ boards)")
        except (CsvToolError, FileNotFoundError):
            pass
        state["data_removed"] = True
        save_workspace(ws)

    delete_workspace(ws.slug)
    if get_active_slug() == ws.slug:
        clear_active_slug()
    out.info("workspace", f"Removed workspace {ws.slug!r}")


@click.command(name="del")
@click.argument("slug", required=False)
def del_(slug: str | None):
    """Remove workspace + CSV rows + close PR + delete branch."""
    from src.workspace.filelock import company_lifecycle_lock

    local = is_local_mode()
    slug = resolve_slug(slug)
    if not SLUG_RE.fullmatch(slug):
        out.die(f"Invalid slug format: {slug!r}")

    with company_lifecycle_lock(slug):
        if not workspace_exists(slug):
            out.die(
                f"Workspace {slug!r} has no recorded ownership state; "
                "refusing best-effort PR or branch deletion"
            )
        ws = load_workspace(slug)
        branch = ws.branch
        if branch not in {f"add-company/{slug}", f"fix-crawler/{slug}"}:
            out.die(
                f"Workspace branch {branch!r} is not bound to slug {slug!r}; "
                "refusing destructive cleanup"
            )
        _run_terminal_cleanup(ws, local=local)


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

        # Enrichment
        from src.shared.constants import DISPLAY_LOCALES

        desc_status = "  ".join(
            f"{loc} {'✓' if ws.descriptions.get(loc) else '✗'}" for loc in DISPLAY_LOCALES
        )
        print(f"  Description: {desc_status}")
        if ws.industry is not None:
            print(f"  Industry: {ws.industry}")
        if ws.employee_count_range is not None:
            print("  Employees: (set)")
        if ws.founded_year is not None:
            print(f"  Founded: {ws.founded_year}")
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

        # Background discovery status
        from src.workspace.state import discovery_status_path

        disc_path = discovery_status_path(slug)
        if disc_path.exists():
            try:
                import yaml

                disc = yaml.safe_load(disc_path.read_text()) or {}
                logo = disc.get("logo_discovery", "unknown")
                career = disc.get("career_discovery", "unknown")
                enrich = disc.get("enrichment", "unknown")
                print("  Background discovery:")
                print(f"    Logo discovery:   {logo}")
                print(f"    Career discovery: {career}")
                print(f"    Enrichment:       {enrich}")
            except Exception:
                # Corrupt discovery status should not hide the rest of workspace status.
                pass

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

    if ws.ats_inventory:
        from src.ats_inventory.candidates import candidate_marker

        source_key = str(ws.ats_inventory.get("source_key") or "")
        board_url = str(ws.ats_inventory.get("board_url") or "")
        if source_key and board_url:
            lines.extend(
                [
                    candidate_marker(source_key, board_url),
                    "## ATS inventory provenance",
                    f"Source key: `{source_key}`",
                    f"Seed verification: `{ws.ats_inventory.get('status', 'unknown')}`",
                    "",
                ]
            )

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

    completeness_cells = []
    for b in boards:
        run = ((b.configs or {}).get(b.active_config or "") or {}).get("run") or {}
        if not run:
            completeness_cells.append("untested")
        elif run.get("truncated"):
            completeness_cells.append("**incomplete (truncated)**")
        else:
            completeness_cells.append("complete")
    lines.append(_row("Completeness", completeness_cells))

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
                    if rejection:
                        status_cell = "**selected** (rejected)"
                    elif cfg_status != "tested":
                        status_cell = f"**selected** ({cfg_status})"
                    elif (cfg.get("run") or {}).get("truncated"):
                        status_cell = "**selected** (incomplete)"
                    else:
                        status_cell = "**selected**"
                elif rejection:
                    status_cell = "rejected"
                elif cfg_status == "selected":
                    status_cell = "tested" if cfg.get("run") else "untested"
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


def _record_current_pr_provenance(
    ws: Workspace,
    *,
    require_current_actor: bool,
    expected_head_oid: str | None = None,
) -> None:
    """Validate and persist the current exact PR head after publication."""
    from src.workspace import git

    if ws.pr is None:
        raise WorkspaceError("Cannot record provenance without a PR number")
    details = git.get_pr_details_strict(ws.pr)
    actor = git.get_authenticated_login_strict() if require_current_actor else None
    git.validate_pr_attachment(
        details,
        pr_number=ws.pr,
        branch=ws.branch,
        base_ref=git.get_main_branch(),
        issue=ws.issue,
        slug=ws.slug,
        authorized_actor=actor,
    )
    if expected_head_oid is not None and details.get("headRefOid") != expected_head_oid:
        raise WorkspaceError(
            f"PR #{ws.pr} head changed after publication; expected {expected_head_oid}"
        )
    if (
        expected_head_oid is not None
        and git.remote_branch_oid_strict(ws.branch) != expected_head_oid
    ):
        raise WorkspaceError(
            f"PR #{ws.pr} remote ref changed after publication; expected {expected_head_oid}"
        )
    if ws.pr_provenance:
        recorded_author = ws.pr_provenance.get("author_login")
        current_author = git.pr_provenance(details, issue=ws.issue, slug=ws.slug).get(
            "author_login"
        )
        if current_author != recorded_author:
            raise WorkspaceError(f"PR #{ws.pr} author changed; refusing to update provenance")
    recorded = git.pr_provenance(details, issue=ws.issue, slug=ws.slug)
    if expected_head_oid is not None:
        # Never bless a later head merely because it was observed after our push.
        recorded["head_ref_oid"] = expected_head_oid
    ws.pr_provenance = recorded
    save_workspace(ws)


def _verify_workspace_pr_before_mutation(ws: Workspace) -> None:
    """Authenticate a recorded PR immediately before a non-create mutation."""
    from src.workspace import git

    if ws.pr is None:
        raise WorkspaceError("Workspace has no PR to mutate")
    git.verify_recorded_pr(
        ws.pr_provenance,
        pr_number=ws.pr,
        branch=ws.branch,
        issue=ws.issue,
        slug=ws.slug,
    )


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
        if ws.descriptions:
            from src.csvtool import company_description_set

            for locale, text in ws.descriptions.items():
                if text:
                    company_description_set(ws.slug, locale, text)
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
            scraper = b.scraper_type
            scraper_config = b.scraper_config
            active_config = (b.configs or {}).get(b.active_config or "", {})
            scraper_type_was_empty = not scraper
            scraper_config_is_explicit = "scraper_config" in active_config and not (
                # V1 migration materializes a paired null/empty placeholder
                # even when no scraper was selected. Treat that pair as
                # legacy absence so both auto tuple elements are restored.
                scraper_type_was_empty and not active_config.get("scraper_config")
            )
            auto = None
            if b.monitor_type:
                from src.workspace._compat import auto_scraper_type

                auto = auto_scraper_type(b.monitor_type, b.monitor_config)
                if not scraper and auto:
                    scraper = auto[0]
                # Repair workspace configs created before auto scraper configs
                # were persisted, while preserving any manual scraper choice
                # or explicit scraper configuration.
                if auto and scraper == auto[0] and not scraper_config_is_explicit and auto[1]:
                    scraper_config = copy.deepcopy(auto[1])
            if scraper:
                board_kwargs["scraper_type"] = scraper
            if scraper_config:
                board_kwargs["scraper_config"] = json.dumps(scraper_config)
            elif scraper_config_is_explicit:
                # Reconfiguration must be able to clear a previously stored
                # CSV config when the agent explicitly selected an empty one.
                board_kwargs["scraper_config"] = ""
            board_add(ws.slug, **board_kwargs)

        # Normalize any out-of-order input before validation and commit. The
        # validator independently enforces this invariant for direct edits.
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
        img_path = f"apps/crawler/data/images/{ws.slug}/"
        img_abs = get_data_dir() / "images" / ws.slug
        commit_paths = [
            "apps/crawler/data/companies.csv",
            "apps/crawler/data/boards.csv",
            "apps/crawler/data/company_descriptions.csv",
            "apps/crawler/data/industries.csv",
            "apps/crawler/src/workspace/kb/",
        ]
        if img_abs.is_dir():
            commit_paths.append(img_path)
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

        current_local_oid = git.current_head_oid_strict()
        local_oid = ws.submit_state.get("publish_oid")
        if local_oid is None:
            local_oid = current_local_oid
            ws.submit_state["publish_oid"] = local_oid
            ws.submit_state["publish_expected_remote_oid"] = (
                ws.pr_provenance.get("head_ref_oid") if ws.pr is not None else None
            )
            ws.submit_state["publish_attempted"] = False
            save_workspace(ws)
        elif local_oid != current_local_oid:
            raise WorkspaceError(
                "Local HEAD changed after submit publication was journaled; refusing to bless it"
            )

        expected_remote_oid = ws.submit_state.get("publish_expected_remote_oid")
        remote_oid = git.remote_branch_oid_strict(ws.branch)
        attempted = bool(ws.submit_state.get("publish_attempted"))
        if remote_oid == local_oid and not attempted and ws.pr is None:
            raise WorkspaceError(
                "Remote branch already exists at the local commit before publication was attempted"
            )
        if ws.pr is not None and remote_oid != local_oid:
            _verify_workspace_pr_before_mutation(ws)
            if expected_remote_oid != ws.pr_provenance.get("head_ref_oid"):
                raise WorkspaceError("Submit journal does not match recorded PR provenance")

        if remote_oid != local_oid:
            if remote_oid != expected_remote_oid:
                raise WorkspaceError(
                    f"Remote branch changed from {expected_remote_oid!r} to {remote_oid!r}"
                )
            if not attempted:
                ws.submit_state["publish_attempted"] = True
                save_workspace(ws)
            git.push_branch_at_expected_oid(ws.branch, local_oid, expected_remote_oid)

        if ws.pr is not None:
            _record_current_pr_provenance(
                ws,
                require_current_actor=False,
                expected_head_oid=local_oid,
            )
            return

        # Create the PR only after the complete configuration is committed and
        # pushed. Recover by branch first so a process interruption after
        # GitHub creates the PR cannot produce a duplicate on retry.
        if not ws.pr:
            existing_pr = git.find_open_pr_for_branch(ws.branch)
            if existing_pr:
                ws.pr = existing_pr
                _record_current_pr_provenance(
                    ws,
                    require_current_actor=True,
                    expected_head_oid=local_oid,
                )
                out.info("github", f"Recovered existing draft PR #{ws.pr}")
                return

            # A concurrent/manual PR for the same issue must stop publication,
            # not create another PR or silently attach this branch to it.
            if ws.issue:
                issue_prs = git.check_existing_prs(ws.issue)
                if issue_prs:
                    issue_pr = int(issue_prs[0]["number"])
                    issue_branch = git.get_pr_branch(issue_pr)
                    if issue_branch != ws.branch:
                        branch_detail = issue_branch or "an unknown branch"
                        raise WorkspaceError(
                            f"Issue #{ws.issue} already has open PR #{issue_pr} "
                            f"on {branch_detail}; refusing to create a duplicate"
                        )
                    ws.pr = issue_pr
                    _record_current_pr_provenance(
                        ws,
                        require_current_actor=True,
                        expected_head_oid=local_oid,
                    )
                    out.info("github", f"Recovered existing draft PR #{ws.pr}")
                    return

            pr_title = (
                f"Reconfigure {ws.name or ws.slug}"
                if ws.branch.startswith("fix-crawler/")
                else f"Add {ws.name or ws.slug}"
            )
            pr_body = f"Closes #{ws.issue}" if ws.issue else ""
            ws.pr = git.create_draft_pr(title=pr_title, body=pr_body)
            _record_current_pr_provenance(
                ws,
                require_current_actor=True,
                expected_head_oid=local_oid,
            )
            out.info("github", f"Created draft PR #{ws.pr}")

    elif step_key == "pr_body_updated":
        if local:
            return  # Local mode — skip PR body update
        from src.workspace import git

        if ws.pr and boards:
            _verify_workspace_pr_before_mutation(ws)
            pr_body = _build_pr_body(ws, boards)
            git.edit_pr_body(ws.pr, pr_body)

    elif step_key == "stats_posted":
        if local:
            return  # Local mode — skip stats posting
        from src.workspace import git

        if ws.pr and boards:
            _verify_workspace_pr_before_mutation(ws)
            board_data = {b.alias: b.to_dict() for b in boards}
            stats_comment = action_log.format_crawl_stats(board_data)
            git.comment_on_pr(ws.pr, stats_comment)

    elif step_key == "transcript_posted":
        if local:
            return  # Local mode — skip transcript posting
        from src.workspace import git

        if ws.pr:
            _verify_workspace_pr_before_mutation(ws)
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
@_serialize_company_lifecycle
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
        forceable = all("poor" in b or "0 jobs found" in b for b in blockers)
        if not force or not forceable:
            for b in blockers:
                out.error("submit", b)
            out.die("Quality gates failed. Fix issues or use --force.")
        else:
            for b in blockers:
                out.warn("submit", f"(forced) {b}")

    # Stale submit detection: if config selections or content changed since last submit, restart
    current_configs = {}
    for b in boards:
        cfg = b._active_cfg()
        current_configs[b.alias] = {
            "active": b.active_config,
            "url": b.url,
            "monitor_type": cfg.get("monitor_type"),
            "monitor_config": copy.deepcopy(cfg.get("monitor_config") or {}),
            "scraper_type": cfg.get("scraper_type"),
            "scraper_config": copy.deepcopy(cfg.get("scraper_config") or {}),
        }
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
        except WorkspaceError as e:
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

    # Advance workflow to "reflect" so task complete can proceed.
    # The parallel orchestrator calls submit directly without stepping
    # through the workflow, leaving the cursor at "setup". A successful
    # submit means all gates have been satisfied.
    try:
        from src.workspace.workflow import _load_wf_from_disk, _save_wf_to_disk

        wf = _load_wf_from_disk(slug)
        if wf.current_step not in ("reflect", "done") and not wf.failed:
            wf.current_step = "reflect"
            # Mark all boards as completed
            boards = list_boards(slug)
            wf.completed_boards = [b.alias for b in boards]
            _save_wf_to_disk(slug, wf)
    except FileNotFoundError:
        pass  # No workflow state — agent ran outside task workflow

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
    from src.shared.constants import DISPLAY_LOCALES

    issues: list[tuple[str, str, str]] = []
    if not ws.name:
        issues.append(("no_name", "Company name not set", "warning"))
    if not ws.website:
        issues.append(("no_website", "Company website not set", "warning"))
    missing_locales = [loc for loc in DISPLAY_LOCALES if not ws.descriptions.get(loc)]
    if missing_locales:
        issues.append(
            (
                "missing_descriptions",
                f"Missing description locales: {', '.join(missing_locales)}",
                "warning",
            )
        )
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
