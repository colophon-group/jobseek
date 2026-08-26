"""Lifecycle commands: new, use, reject, del, status, validate, submit."""

from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import shutil
import stat
import uuid
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

import click
import yaml

from src.shared.constants import SLUG_RE, get_data_dir
from src.shared.csv_io import read_csv
from src.workspace import log as action_log
from src.workspace import output as out
from src.workspace.errors import CsvToolError, WorkspaceError
from src.workspace.state import (
    Board,
    Workspace,
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

    With --pr N, an interactive operator may attach to an existing pull
    request at its exact current head. Scheduled resolver runs may not attach
    to or take over an existing PR. Otherwise the draft PR is created by ws
    submit after the complete configuration is committed and pushed.

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
        out.die(
            f"Slug {slug!r} exists in CSV without authenticated workspace ownership; "
            "refusing bootstrap cleanup"
        )

    branch = f"fix-crawler/{slug}" if reconfig else f"add-company/{slug}"
    pr_number: int | None = None
    pr_details: dict | None = None
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

        # Existing PRs are externally owned unless an interactive operator
        # explicitly attaches.  A linked issue, deterministic branch name,
        # draft state, or prior resolver authorship is not a cross-run lease.
        if pr_opt:
            if os.environ.get("JOBSEEK_CODEX_RUN_ID"):
                out.die(
                    "Scheduled company resolvers cannot attach to existing PRs; "
                    "leave the PR unchanged for human review"
                )
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
                linked_number = existing[0].get("number")
                out.die(
                    f"Issue #{issue} already has {classification} linked PR "
                    f"#{linked_number}; refusing cross-run branch takeover"
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

        # Create only from an entirely fresh path/ref. Lifecycle journals own
        # cleanup; bootstrap must never guess that pre-existing state is stale.
        git.fetch()
        worktree_path = git.worktrees_dir() / slug

        if pr_number:
            # Explicit operator attachment preserves the exact existing head.
            # It never merges main into a reviewed company branch.
            assert pr_details is not None
            expected_head = str(pr_details["headRefOid"])
            remote_head = git.remote_branch_oid_strict(branch)
            if remote_head != expected_head:
                raise WorkspaceError(
                    f"PR #{pr_number} branch changed during attachment; refusing to continue"
                )
            created_identity = git.create_worktree(branch, worktree_path, start_point=expected_head)
        else:
            main = git.get_main_branch()
            if git.remote_branch_oid_strict(branch) is not None:
                raise WorkspaceError(
                    f"Remote branch {branch!r} exists without authenticated workspace ownership"
                )
            created_identity = git.create_worktree(
                branch, worktree_path, start_point=f"origin/{main}"
            )
        set_repo_root(worktree_path)
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

                _git.remove_authenticated_worktree(
                    worktree_path,
                    branch,
                    str(created_identity["head"]),
                    expected_dev=int(created_identity["dev"]),
                    expected_ino=int(created_identity["ino"]),
                )
                _git.delete_local_branch_at_expected_oid(branch, str(created_identity["head"]))
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

    if pr_number is not None:
        # Reauthenticate the exact ref immediately before publishing local
        # ownership state. The earlier check cannot authorize a later ref.
        assert pr_details is not None
        refreshed = git.get_pr_details_strict(pr_number)
        git.validate_pr_attachment(
            refreshed,
            pr_number=pr_number,
            branch=branch,
            base_ref=base_ref,
            issue=issue,
            slug=slug,
            authorized_actor=None,
        )
        expected_head = str(pr_details["headRefOid"])
        if (
            refreshed.get("headRefOid") != expected_head
            or git.remote_branch_oid_strict(branch) != expected_head
        ):
            raise WorkspaceError(
                f"PR #{pr_number} branch changed before workspace publication; refusing attachment"
            )
        pr_details = refreshed
        ws.pr_provenance = git.pr_provenance(refreshed, issue=issue, slug=slug)

    if not local:
        ws.worktree_identity = {
            "version": 1,
            "path": worktree_str,
            "slug": slug,
            "branch": branch,
            "head": str(created_identity["head"]),
            "dev": int(created_identity["dev"]),
            "ino": int(created_identity["ino"]),
            "issue": issue,
            "pr": pr_number,
            "pr_provenance": copy.deepcopy(ws.pr_provenance),
        }

    save_workspace(ws)

    if seeded_board is not None:
        assert inventory_seed is not None
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
    assert issue is not None

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
    labels = [reason] if reason in ("duplicate", "subsidiary") else []
    outcome = {
        "marker": f"<!-- validation-failed: {reason} -->",
        "body": body,
        "labels": labels,
        "close_issue": True,
    }
    _cleanup_resolver_artifacts(
        issue=issue,
        slug=slug,
        ws=ws,
        local=local,
        outcome=outcome,
    )
    if not local:
        out.info("github", f"Commented on issue #{issue} (validation-failed: {reason})")
        out.info("github", f"Closed issue #{issue}")

    out.info("task", "Done. Do not pick another issue — stop here.")


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
        journal_slug = _find_terminal_slug_for_issue(issue)
        if journal_slug is not None:
            return journal_slug, None, issue
        return None, None, issue

    active = get_active_slug()
    if active and workspace_exists(active):
        ws = load_workspace(active)
        return active, ws, ws.issue
    return None, None, None


_TERMINAL_ATTEMPT_KEYS = {
    "remote_delete",
    "pr_close",
    "issue_comment",
    "issue_labels",
    "issue_close",
    "worktree_remove",
    "local_branch_remove",
    "data_remove",
    "active_clear",
    "workspace_remove",
}
_TERMINAL_JOURNAL_KEYS = {
    "version",
    "journal_id",
    "slug",
    "branch",
    "issue",
    "pr",
    "pr_provenance",
    "expected_remote_oid",
    "worktree",
    "worktree_head",
    "worktree_dev",
    "worktree_ino",
    "local_branch_oid",
    "data_cleanup_required",
    "data_initially_present",
    "workspace_was_present",
    "active_entries",
    "claim_initially_present",
    "outcome",
    "attempts",
}
_OUTCOME_KEYS = {"marker", "body", "labels", "close_issue"}


def _terminal_journal_dir() -> Path:
    from src.shared.constants import get_workspace_dir

    path = get_workspace_dir() / ".terminal-lifecycle"
    path.mkdir(parents=True, exist_ok=True)
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise WorkspaceError(f"Terminal journal root is unsafe: {path}")
    return path


def _issue_terminal_journal_dir() -> Path:
    """Collision-free namespace for outcomes with no company identity."""
    path = _terminal_journal_dir() / "issues"
    path.mkdir(parents=True, exist_ok=True)
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise WorkspaceError(f"Issue terminal journal root is unsafe: {path}")
    return path


_ISSUE_TERMINAL_KEYS = {
    "version",
    "namespace",
    "issue",
    "outcome",
    "claim_initially_present",
    "attempts",
}
_ISSUE_TERMINAL_ATTEMPTS = {"issue_comment", "issue_labels", "issue_close"}


def _issue_terminal_paths(issue: int) -> tuple[Path, Path]:
    if not isinstance(issue, int) or issue <= 0:
        raise WorkspaceError("Issue terminal identity is invalid")
    root = _issue_terminal_journal_dir()
    return root / f"{issue}.pending.yaml", root / f"{issue}.completed.yaml"


def _validate_issue_terminal_journal(data: object, issue: int) -> dict:
    if (
        not isinstance(data, dict)
        or set(data) != _ISSUE_TERMINAL_KEYS
        or data.get("version") != 1
        or data.get("namespace") != "issue"
        or data.get("issue") != issue
        or not isinstance(data.get("claim_initially_present"), bool)
        or not isinstance(data.get("attempts"), dict)
        or set(data["attempts"]) != _ISSUE_TERMINAL_ATTEMPTS
        or not all(isinstance(value, bool) for value in data["attempts"].values())
    ):
        raise WorkspaceError("Issue terminal journal has an invalid exact schema")
    outcome = data.get("outcome")
    if (
        not isinstance(outcome, dict)
        or set(outcome) != _OUTCOME_KEYS
        or not all(isinstance(outcome.get(key), str) for key in ("marker", "body"))
        or not isinstance(outcome.get("labels"), list)
        or not all(isinstance(label, str) for label in outcome["labels"])
        or len(outcome["labels"]) != len(set(outcome["labels"]))
        or not isinstance(outcome.get("close_issue"), bool)
    ):
        raise WorkspaceError("Issue terminal outcome has an invalid exact schema")
    return data


def _load_issue_terminal_journal(issue: int) -> tuple[dict | None, bool]:
    pending, completed = _issue_terminal_paths(issue)
    matches = [path for path in (pending, completed) if _lexists(path)]
    if len(matches) > 1:
        raise WorkspaceError("Both pending and completed issue terminal journals exist")
    if not matches:
        return None, False
    path = matches[0]
    # Reuse the no-follow journal reader mechanics without accepting the
    # company schema.
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise WorkspaceError(f"Issue terminal journal path is unsafe: {path}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        expected = path.lstat()
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise WorkspaceError("Issue terminal journal changed while opening")
        raw = os.read(fd, 65536).decode()
        if os.read(fd, 1):
            raise WorkspaceError("Issue terminal journal is unexpectedly large")
    finally:
        os.close(fd)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise WorkspaceError("Issue terminal journal YAML is corrupt") from exc
    return _validate_issue_terminal_journal(data, issue), path == completed


def _save_issue_terminal_journal(journal: dict) -> None:
    from src.workspace.state import _atomic_write

    issue = journal.get("issue")
    if not isinstance(issue, int):
        raise WorkspaceError("Issue terminal journal has no valid issue")
    journal = _validate_issue_terminal_journal(journal, issue)
    pending, completed = _issue_terminal_paths(issue)
    if _lexists(completed):
        raise WorkspaceError("Cannot mutate a completed issue terminal journal")
    if _lexists(pending):
        existing, _ = _load_issue_terminal_journal(issue)
        assert existing is not None
        for key in _ISSUE_TERMINAL_KEYS - {"attempts"}:
            if existing[key] != journal[key]:
                raise WorkspaceError("Issue terminal journal immutable fields changed")
        if any(
            existing["attempts"][key] and not journal["attempts"][key]
            for key in _ISSUE_TERMINAL_ATTEMPTS
        ):
            raise WorkspaceError("Issue terminal attempt history moved backward")
    _atomic_write(pending, yaml.dump(journal, default_flow_style=False, sort_keys=False))


def _run_issue_only_terminal_cleanup(
    issue: int,
    *,
    local: bool,
    outcome: dict | None,
) -> None:
    """Run a durable outcome transition that can never name company artifacts."""
    if outcome is None:
        raise WorkspaceError("Issue-only terminal cleanup requires an explicit outcome")
    from src.workspace import git

    journal, completed = _load_issue_terminal_journal(issue)
    if journal is None:
        journal = {
            "version": 1,
            "namespace": "issue",
            "issue": issue,
            "outcome": copy.deepcopy(outcome),
            "claim_initially_present": bool(not local and git.is_issue_claimed_strict(issue)),
            "attempts": {key: False for key in _ISSUE_TERMINAL_ATTEMPTS},
        }
        _save_issue_terminal_journal(journal)
    elif journal["outcome"] != outcome:
        raise WorkspaceError("Issue-only terminal outcome contradicts its receipt")

    def attempt(key: str) -> None:
        if not journal["attempts"][key]:
            journal["attempts"][key] = True
            _save_issue_terminal_journal(journal)

    if not local:
        if completed:
            state, labels = git.issue_state_and_labels_strict(issue)
            if (
                not git.issue_has_comment_marker_strict(issue, outcome["marker"])
                or not set(outcome["labels"]).issubset(labels)
                or (outcome["close_issue"] and state != "CLOSED")
            ):
                raise WorkspaceError("Completed issue-only outcome was altered")
        else:
            if not git.issue_has_comment_marker_strict(issue, outcome["marker"]):
                attempt("issue_comment")
                git.comment_on_issue_once(issue, outcome["marker"], outcome["body"])
            state, labels = git.issue_state_and_labels_strict(issue)
            missing = set(outcome["labels"]) - labels
            if missing:
                attempt("issue_labels")
                for label in sorted(missing):
                    git.add_label_to_issue(issue, label)
                state, labels = git.issue_state_and_labels_strict(issue)
            if outcome["close_issue"] and state == "OPEN":
                attempt("issue_close")
                git.close_issue_if_open(issue)
                state, _ = git.issue_state_and_labels_strict(issue)
            if outcome["close_issue"] and state != "CLOSED":
                raise WorkspaceError("Issue-only outcome did not close the issue")
        if not completed:
            pending, completed_path = _issue_terminal_paths(issue)
            if _load_issue_terminal_journal(issue)[0] != journal:
                raise WorkspaceError("Issue terminal journal changed before finalization")
            os.replace(pending, completed_path)
            completed = True
        claimed = git.is_issue_claimed_strict(issue)
        if journal["claim_initially_present"]:
            if claimed:
                git.unclaim_issue_strict(issue)
                if git.is_issue_claimed_strict(issue):
                    raise WorkspaceError("Issue claim survived issue-only terminal cleanup")
        elif claimed:
            raise WorkspaceError("A new issue claim appeared during issue-only cleanup")
    elif not completed:
        pending, completed_path = _issue_terminal_paths(issue)
        os.replace(pending, completed_path)


def _terminal_pending_path(slug: str) -> Path:
    if not SLUG_RE.fullmatch(slug):
        raise WorkspaceError(f"Invalid terminal journal slug: {slug!r}")
    return _terminal_journal_dir() / f"{slug}.pending.yaml"


def _terminal_completed_path(journal: dict) -> Path:
    return _terminal_journal_dir() / f"{journal['slug']}.{journal['journal_id']}.completed.yaml"


def _terminal_locator_path(slug: str) -> Path:
    if not SLUG_RE.fullmatch(slug):
        raise WorkspaceError(f"Invalid terminal locator slug: {slug!r}")
    return _terminal_journal_dir() / f"{slug}.latest-receipt"


def _read_terminal_locator(slug: str) -> dict | None:
    path = _terminal_locator_path(slug)
    if not _lexists(path):
        return None
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise WorkspaceError("Terminal receipt locator is unsafe")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        raw = os.read(fd, 16384).decode()
        if os.read(fd, 1):
            raise WorkspaceError("Terminal receipt locator is unexpectedly large")
    finally:
        os.close(fd)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise WorkspaceError("Terminal receipt locator is corrupt") from exc
    if (
        not isinstance(data, dict)
        or set(data) != {"version", "slug", "journal_id", "issue"}
        or data.get("version") != 1
        or data.get("slug") != slug
        or not isinstance(data.get("journal_id"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", data["journal_id"])
        or (data.get("issue") is not None and not isinstance(data["issue"], int))
    ):
        raise WorkspaceError("Terminal receipt locator has an invalid exact schema")
    return data


def _validate_terminal_journal(data: object) -> dict:
    from src.workspace.git import _OID_RE

    if not isinstance(data, dict) or set(data) != _TERMINAL_JOURNAL_KEYS:
        raise WorkspaceError("Terminal journal has an invalid exact schema")
    if data.get("version") != 1 or not isinstance(data.get("slug"), str):
        raise WorkspaceError("Terminal journal version/slug is invalid")
    if not isinstance(data.get("journal_id"), str) or not re.fullmatch(
        r"[0-9a-f]{32}", data["journal_id"]
    ):
        raise WorkspaceError("Terminal journal ID is invalid")
    if not SLUG_RE.fullmatch(data["slug"]):
        raise WorkspaceError("Terminal journal slug is invalid")
    branch = data.get("branch")
    if not isinstance(branch, str) or branch not in {
        f"add-company/{data['slug']}",
        f"fix-crawler/{data['slug']}",
        "",
    }:
        raise WorkspaceError("Terminal journal branch is invalid")
    if data.get("issue") is not None and not isinstance(data["issue"], int):
        raise WorkspaceError("Terminal journal issue is invalid")
    if data.get("pr") is not None and not isinstance(data["pr"], int):
        raise WorkspaceError("Terminal journal PR is invalid")
    for key in ("expected_remote_oid", "worktree_head", "local_branch_oid"):
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or not _OID_RE.fullmatch(value)):
            raise WorkspaceError(f"Terminal journal {key} is invalid")
    worktree_fields = (
        data.get("worktree"),
        data.get("worktree_head"),
        data.get("worktree_dev"),
        data.get("worktree_ino"),
    )
    if any(value is not None for value in worktree_fields) and not (
        isinstance(worktree_fields[0], str)
        and isinstance(worktree_fields[1], str)
        and isinstance(worktree_fields[2], int)
        and isinstance(worktree_fields[3], int)
        and worktree_fields[2] >= 0
        and worktree_fields[3] > 0
    ):
        raise WorkspaceError("Terminal journal worktree identity is incomplete")
    for key in (
        "data_cleanup_required",
        "data_initially_present",
        "workspace_was_present",
        "claim_initially_present",
    ):
        if not isinstance(data.get(key), bool):
            raise WorkspaceError(f"Terminal journal {key} must be boolean")
    active_entries = data.get("active_entries")
    if not isinstance(active_entries, list):
        raise WorkspaceError("Terminal journal active_entries is invalid")
    for entry in active_entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"path", "record", "generation"}
            or not isinstance(entry["path"], str)
            or not isinstance(entry["record"], str)
            or not isinstance(entry["generation"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", entry["generation"])
        ):
            raise WorkspaceError("Terminal journal active entry is invalid")
    active_paths = [entry["path"] for entry in active_entries]
    if len(active_paths) != len(set(active_paths)):
        raise WorkspaceError("Terminal journal active entries contain duplicate paths")
    outcome = data.get("outcome")
    if outcome is not None:
        if not isinstance(data["issue"], int):
            raise WorkspaceError("Terminal journal outcome has no issue")
        if not isinstance(outcome, dict) or set(outcome) != _OUTCOME_KEYS:
            raise WorkspaceError("Terminal journal outcome schema is invalid")
        if not all(isinstance(outcome.get(key), str) for key in ("marker", "body")):
            raise WorkspaceError("Terminal journal outcome text is invalid")
        if not isinstance(outcome.get("labels"), list) or not all(
            isinstance(label, str) for label in outcome["labels"]
        ):
            raise WorkspaceError("Terminal journal outcome labels are invalid")
        if len(outcome["labels"]) != len(set(outcome["labels"])):
            raise WorkspaceError("Terminal journal outcome labels contain duplicates")
        if not isinstance(outcome.get("close_issue"), bool):
            raise WorkspaceError("Terminal journal outcome close flag is invalid")
    attempts = data.get("attempts")
    if not isinstance(attempts, dict) or set(attempts) != _TERMINAL_ATTEMPT_KEYS:
        raise WorkspaceError("Terminal journal attempts schema is invalid")
    if not all(isinstance(value, bool) for value in attempts.values()):
        raise WorkspaceError("Terminal journal attempts must be boolean")
    provenance = data.get("pr_provenance")
    if data["pr"] is None:
        if provenance != {} or data["expected_remote_oid"] is not None:
            raise WorkspaceError("Terminal journal has remote provenance without a PR")
    elif not isinstance(provenance, dict) or not provenance:
        raise WorkspaceError("Terminal journal is missing PR provenance")
    return data


def _read_terminal_journal(path: Path) -> dict:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        raise
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise WorkspaceError(f"Terminal journal path is unsafe: {path}")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(fd)
            expected = path.lstat()
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                expected.st_dev,
                expected.st_ino,
            ):
                raise WorkspaceError(f"Terminal journal changed while opening: {path}")
            with os.fdopen(fd) as handle:
                fd = -1
                raw = handle.read()
        finally:
            if fd >= 0:
                os.close(fd)
    except OSError as exc:
        raise WorkspaceError(f"Terminal journal could not be opened safely: {path}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise WorkspaceError(f"Terminal journal YAML is corrupt: {path}") from exc
    return _validate_terminal_journal(data)


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def _load_terminal_journal(
    slug: str,
    *,
    issue: int | None = None,
) -> tuple[dict | None, bool]:
    pending = _terminal_pending_path(slug)
    if _lexists(pending):
        journal = _read_terminal_journal(pending)
        if journal["slug"] != slug:
            raise WorkspaceError("Pending terminal journal filename contradicts its contents")
        if issue is not None and journal["issue"] != issue:
            raise WorkspaceError("Pending terminal journal belongs to a different issue")
        if _lexists(_terminal_completed_path(journal)):
            raise WorkspaceError("Both pending and completed terminal journals exist")
        return journal, False
    matches: list[dict] = []
    for name in sorted(os.listdir(_terminal_journal_dir())):
        if not name.startswith(f"{slug}.") or not name.endswith(".completed.yaml"):
            continue
        journal = _read_terminal_journal(_terminal_journal_dir() / name)
        if journal["slug"] != slug or _terminal_completed_path(journal).name != name:
            raise WorkspaceError("Completed terminal journal filename is invalid")
        if issue is None or journal["issue"] == issue:
            matches.append(journal)
    if issue is None and len(matches) > 1:
        locator = _read_terminal_locator(slug)
        if locator is None:
            raise WorkspaceError("Multiple completed terminal receipts have no exact locator")
        matches = [
            journal
            for journal in matches
            if journal["journal_id"] == locator["journal_id"]
            and journal["issue"] == locator["issue"]
        ]
    if len(matches) > 1:
        raise WorkspaceError("Multiple completed terminal journals match this issue")
    if matches:
        return matches[0], True
    return None, False


def _save_terminal_journal(journal: dict) -> None:
    from src.workspace.state import _atomic_write

    journal = _validate_terminal_journal(journal)
    pending = _terminal_pending_path(journal["slug"])
    completed = _terminal_completed_path(journal)
    if _lexists(completed):
        raise WorkspaceError("Cannot mutate a completed terminal journal")
    if _lexists(pending):
        existing = _read_terminal_journal(pending)
        if any(existing[key] != journal[key] for key in _TERMINAL_JOURNAL_KEYS - {"attempts"}):
            raise WorkspaceError("Terminal journal immutable fields changed before save")
        if any(
            existing["attempts"][key] and not journal["attempts"][key]
            for key in _TERMINAL_ATTEMPT_KEYS
        ):
            raise WorkspaceError("Terminal journal attempt history moved backward")
    _atomic_write(pending, yaml.dump(journal, default_flow_style=False, sort_keys=False))


def _finalize_terminal_journal(journal: dict) -> None:
    from src.workspace.state import _atomic_write

    pending = _terminal_pending_path(journal["slug"])
    completed = _terminal_completed_path(journal)
    if _lexists(completed):
        if _lexists(pending) or _read_terminal_journal(completed) != journal:
            raise WorkspaceError("Completed terminal journal contradicts pending state")
        return
    if not _lexists(pending):
        raise WorkspaceError("Pending terminal journal disappeared before finalization")
    if _read_terminal_journal(pending) != journal:
        raise WorkspaceError("Pending terminal journal changed before finalization")
    locator = {
        "version": 1,
        "slug": journal["slug"],
        "journal_id": journal["journal_id"],
        "issue": journal["issue"],
    }
    _atomic_write(
        _terminal_locator_path(journal["slug"]),
        yaml.dump(locator, default_flow_style=False, sort_keys=False),
    )
    os.replace(pending, completed)
    if _read_terminal_journal(completed) != journal:
        raise WorkspaceError("Completed terminal journal changed during finalization")


def _find_terminal_slug_for_issue(issue: int) -> str | None:
    matches: list[str] = []
    root = _terminal_journal_dir()
    for name in sorted(os.listdir(root)):
        if not name.endswith(".yaml"):
            continue
        path = root / name
        journal = _read_terminal_journal(path)
        expected_paths = {
            _terminal_pending_path(journal["slug"]),
            _terminal_completed_path(journal),
        }
        if path not in expected_paths:
            raise WorkspaceError(f"Terminal journal filename is invalid: {path}")
        if journal["issue"] == issue:
            matches.append(journal["slug"])
    if len(set(matches)) > 1:
        raise WorkspaceError(f"Multiple terminal journals match issue #{issue}")
    return matches[0] if matches else None


def _cleanup_resolver_artifacts(
    *,
    issue: int,
    slug: str | None,
    ws: Workspace | None,
    local: bool,
    outcome: dict | None = None,
) -> None:
    """Remove one resolver attempt before its issue is terminally closed.

    GitHub and branch failures intentionally propagate.  The issue remains
    open, so rerunning the terminal command safely resumes cleanup.
    """
    from src.workspace.filelock import company_lifecycle_lock

    lock_slug = slug or (ws.slug if ws is not None else None)
    if lock_slug is None:
        lock_slug = _find_terminal_slug_for_issue(issue)
    if lock_slug is None and not local:
        # Discover a possible slug only to select the lock. The complete query
        # and all validation are repeated after acquiring it.
        from src.workspace import git

        candidates = git.check_existing_prs_strict(issue)
        if len(candidates) == 1:
            branch = candidates[0].get("headRefName")
            if isinstance(branch, str) and branch.startswith("add-company/"):
                lock_slug = branch.removeprefix("add-company/")

    lock_identity = lock_slug if lock_slug is not None else f"@issue:{issue}"
    with company_lifecycle_lock(lock_identity):
        _cleanup_resolver_artifacts_locked(
            issue=issue,
            slug=lock_slug,
            ws=ws,
            local=local,
            outcome=outcome,
            issue_only_lock=lock_slug is None,
        )


def _cleanup_resolver_artifacts_locked(
    *,
    issue: int,
    slug: str | None,
    ws: Workspace | None,
    local: bool,
    outcome: dict | None,
    issue_only_lock: bool = False,
) -> None:
    """Locked implementation for terminal resolver cleanup."""
    if slug and workspace_exists(slug):
        candidate = load_workspace(slug)
        if candidate.issue != issue:
            raise WorkspaceError(
                f"Workspace {slug!r} belongs to issue #{candidate.issue}, not issue #{issue}"
            )
        ws = candidate
    journal, _ = _load_terminal_journal(slug, issue=issue) if slug else (None, False)
    branch = ws.branch if ws and ws.branch else ""
    pr_number = ws.pr if ws else None

    if not local and journal is None:
        from src.workspace import git

        linked_prs = git.check_existing_prs_strict(issue)
        if issue_only_lock and linked_prs:
            raise WorkspaceError(
                f"Issue #{issue} gained a linked PR during issue-only cleanup; "
                "retry under company ownership"
            )
        classification = git.classify_issue_prs(linked_prs)
        if classification == "conflicting" or (classification == "submitted" and pr_number is None):
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
            candidate_worktree = git.worktrees_dir() / slug
            worktree = (
                str(candidate_worktree)
                if git.managed_worktree_identity_strict(candidate_worktree, branch) is not None
                else ""
            )
            ws = Workspace(
                slug=slug,
                created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                branch=branch,
                issue=issue,
                pr=pr_number,
                pr_provenance=git.pr_provenance(details, issue=issue, slug=slug),
                worktree=worktree,
            )
        elif branch and git.remote_branch_oid_strict(branch) is not None:
            raise WorkspaceError(
                f"Remote branch {branch!r} exists without exact PR provenance; refusing cleanup"
            )

    if ws is not None and workspace_exists(ws.slug):
        action_log.append(
            ws_log_path(ws.slug),
            "cleanup",
            True,
            f"Cleaning terminal resolver artifacts for issue #{issue}",
        )

    if slug is None:
        _run_issue_only_terminal_cleanup(issue, local=local, outcome=outcome)
        return
    if ws is None:
        ws = Workspace(
            slug=slug,
            created_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            issue=issue,
        )
    _run_terminal_cleanup(ws, local=local, outcome=outcome)


def _company_registry_presence(slug: str) -> tuple[bool, bool]:
    companies = _load_existing_company(slug)
    boards = _load_existing_boards(slug)
    return bool(companies), bool(boards)


def _decode_active_pointer(raw: str) -> tuple[str, str] | None:
    """Return ``(slug, generation)`` for the exact authenticated v1 record."""
    try:
        record = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(record, dict)
        or set(record) != {"version", "slug", "generation"}
        or record.get("version") != 1
        or not isinstance(record.get("slug"), str)
        or not isinstance(record.get("generation"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", record["generation"])
    ):
        return None
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    if raw != canonical:
        return None
    return record["slug"], record["generation"]


def _upgrade_legacy_active_pointer(
    root_fd: int,
    name: str,
    *,
    expected: os.stat_result,
    slug: str,
) -> tuple[os.stat_result, str, str]:
    """CAS-upgrade one plaintext pointer before terminal state is journaled."""
    from src.workspace.safe_cleanup import (
        claim_child_at,
        restore_claimed_child_at,
        unlink_child_at,
    )

    generation = uuid.uuid4().hex
    raw = json.dumps(
        {"version": 1, "slug": slug, "generation": generation},
        sort_keys=True,
        separators=(",", ":"),
    )
    claimed = f".jobseek-active-upgrade-v1-{uuid.uuid4().hex}"
    try:
        claim_child_at(root_fd, name, expected=expected, claimed_name=claimed)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(name, flags, 0o600, dir_fd=root_fd)
        try:
            os.write(fd, (raw + "\n").encode())
            os.fsync(fd)
        finally:
            os.close(fd)
        claimed_stat = os.stat(claimed, dir_fd=root_fd, follow_symlinks=False)
        unlink_child_at(root_fd, claimed, expected=claimed_stat)
    except Exception as exc:
        try:
            os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            with contextlib.suppress(RuntimeError):
                restore_claimed_child_at(root_fd, name, claimed, expected=expected)
        raise WorkspaceError("Could not safely upgrade legacy active pointer") from exc
    item = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    return item, raw, generation


def _active_pointer_entries(
    slug: str,
    *,
    upgrade_legacy: bool = False,
) -> list[dict[str, str]]:
    from src.shared.constants import get_workspace_dir
    from src.workspace.safe_cleanup import open_absolute_directory_no_follow

    root = get_workspace_dir()
    root.mkdir(parents=True, exist_ok=True)
    try:
        root_fd = open_absolute_directory_no_follow(root)
    except RuntimeError as exc:
        raise WorkspaceError(f"Workspace root is unsafe: {root}") from exc
    matches: list[dict[str, str]] = []
    try:
        for name in sorted(os.listdir(root_fd)):
            if name != "active" and not name.startswith("active."):
                continue
            try:
                item = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(item.st_mode) or not stat.S_ISREG(item.st_mode):
                raise WorkspaceError(f"Active pointer is unsafe: {root / name}")
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(name, flags, dir_fd=root_fd)
            try:
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino) != (item.st_dev, item.st_ino):
                    raise WorkspaceError(f"Active pointer changed while opening: {root / name}")
                value = os.read(fd, 4096).decode().strip()
            finally:
                os.close(fd)
            decoded = _decode_active_pointer(value)
            if decoded is None and value == slug and upgrade_legacy:
                item, value, generation = _upgrade_legacy_active_pointer(
                    root_fd, name, expected=item, slug=slug
                )
                decoded = (slug, generation)
            if decoded is not None and decoded[0] == slug:
                matches.append(
                    {
                        "path": str(root / name),
                        "record": value,
                        "generation": decoded[1],
                    }
                )
            elif value == slug:
                raise WorkspaceError("An active pointer was replaced after terminal journaling")
    finally:
        os.close(root_fd)
    return matches


def _remove_active_pointers(journal: dict) -> None:
    from src.shared.constants import get_workspace_dir
    from src.workspace.safe_cleanup import (
        open_absolute_directory_no_follow,
        unlink_child_at,
    )

    root = get_workspace_dir()
    expected = {Path(entry["path"]): entry for entry in journal["active_entries"]}
    actual = {Path(entry["path"]): entry for entry in _active_pointer_entries(journal["slug"])}
    if not set(actual).issubset(expected):
        raise WorkspaceError("A new active pointer appeared after terminal journaling")
    for path, entry in actual.items():
        captured = expected[path]
        if entry != captured:
            raise WorkspaceError("An active pointer was replaced after terminal journaling")
    if not journal["attempts"]["active_clear"] and actual != expected:
        raise WorkspaceError("Active pointers changed before their cleanup attempt")
    if not actual:
        if expected and not journal["attempts"]["active_clear"]:
            raise WorkspaceError("Active pointers disappeared without a recorded attempt")
        return
    _set_terminal_attempt(journal, "active_clear")
    root_fd = open_absolute_directory_no_follow(root)
    try:
        for path in sorted(actual):
            if path.parent != root:
                raise WorkspaceError("Terminal journal active pointer is outside workspace root")
            item = os.stat(path.name, dir_fd=root_fd, follow_symlinks=False)
            if stat.S_ISLNK(item.st_mode) or not stat.S_ISREG(item.st_mode):
                raise WorkspaceError(f"Active pointer became unsafe: {path}")
            captured = expected[path]
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path.name, flags, dir_fd=root_fd)
            try:
                opened = os.fstat(fd)
                value = os.read(fd, 4096).decode().strip()
            finally:
                os.close(fd)
            if (opened.st_dev, opened.st_ino) != (item.st_dev, item.st_ino):
                raise WorkspaceError(f"Active pointer changed while opening: {path}")
            decoded = _decode_active_pointer(value)
            if decoded != (journal["slug"], captured["generation"]) or value != captured["record"]:
                raise WorkspaceError(f"Active pointer content changed: {path}")
            try:
                unlink_child_at(root_fd, path.name, expected=item)
            except RuntimeError as exc:
                raise WorkspaceError(f"Could not safely remove active pointer: {path}") from exc
    finally:
        os.close(root_fd)
    if _active_pointer_entries(journal["slug"]):
        raise WorkspaceError("Active pointer survived terminal cleanup")


def _set_terminal_attempt(journal: dict, key: str) -> None:
    if key not in _TERMINAL_ATTEMPT_KEYS:
        raise WorkspaceError(f"Unknown terminal attempt {key!r}")
    if not journal["attempts"][key]:
        journal["attempts"][key] = True
        _save_terminal_journal(journal)


def _initialize_terminal_journal(ws: Workspace, *, local: bool, outcome: dict | None) -> dict:
    from src.workspace import git

    branch = ws.branch
    if branch and branch not in {f"add-company/{ws.slug}", f"fix-crawler/{ws.slug}"}:
        raise WorkspaceError(f"Workspace branch {branch!r} is not bound to slug {ws.slug!r}")
    if not branch and (ws.pr is not None or ws.worktree):
        raise WorkspaceError("Workspace has artifacts without a recorded branch")

    provenance: dict = {}
    remote_oid: str | None = None
    identity: dict[str, str | int] | None = None
    local_oid: str | None = None
    worktree_path: Path | None = None
    if not local:
        # The journal may only capture an identity that was persisted when the
        # workspace was created and is still exact now.  Re-observing the
        # canonical pathname alone would bless a pre-journal replacement.
        _authenticate_workspace_worktree(ws)
        if ws.pr is not None:
            git.verify_recorded_pr(
                ws.pr_provenance,
                pr_number=ws.pr,
                branch=branch,
                issue=ws.issue,
                slug=ws.slug,
                allow_closed=True,
            )
            provenance = copy.deepcopy(ws.pr_provenance)
            remote_oid = str(provenance["head_ref_oid"])
        elif branch and git.remote_branch_oid_strict(branch) is not None:
            raise WorkspaceError(f"Remote branch {branch!r} lacks exact PR provenance")
        if branch:
            canonical = Path(os.path.abspath(str(git.worktrees_dir() / ws.slug)))
            persisted = ws.worktree_identity
            identity = {
                "head": str(persisted["head"]),
                "dev": int(persisted["dev"]),
                "ino": int(persisted["ino"]),
            }
            worktree_path = canonical
            local_oid = git.local_branch_oid_strict(branch)
            if local_oid != identity["head"]:
                raise WorkspaceError("Local branch and worktree commits contradict each other")

    company_present, board_present = _company_registry_presence(ws.slug)
    data_required = branch.startswith("add-company/")
    claim_present = bool(ws.issue and not local and git.is_issue_claimed_strict(ws.issue))
    journal = {
        "version": 1,
        "journal_id": uuid.uuid4().hex,
        "slug": ws.slug,
        "branch": branch,
        "issue": ws.issue,
        "pr": ws.pr if not local else None,
        "pr_provenance": provenance,
        "expected_remote_oid": remote_oid,
        "worktree": str(worktree_path) if worktree_path else None,
        "worktree_head": str(identity["head"]) if identity else None,
        "worktree_dev": int(identity["dev"]) if identity else None,
        "worktree_ino": int(identity["ino"]) if identity else None,
        "local_branch_oid": local_oid,
        "data_cleanup_required": data_required,
        "data_initially_present": bool(company_present or board_present),
        "workspace_was_present": workspace_exists(ws.slug),
        "active_entries": _active_pointer_entries(ws.slug, upgrade_legacy=True),
        "claim_initially_present": claim_present,
        "outcome": copy.deepcopy(outcome),
        "attempts": {key: False for key in _TERMINAL_ATTEMPT_KEYS},
    }
    _save_terminal_journal(journal)
    return journal


def _cross_validate_terminal_journal(
    journal: dict,
    ws: Workspace,
    *,
    local: bool,
    outcome: dict | None,
) -> None:
    from src.shared.constants import get_workspace_dir
    from src.workspace import git

    if journal["outcome"] != outcome:
        raise WorkspaceError("Terminal outcome contradicts the existing journal")
    if journal["issue"] != ws.issue:
        raise WorkspaceError("Terminal journal issue contradicts requested cleanup")
    if journal["pr"] is not None:
        if journal["expected_remote_oid"] != journal["pr_provenance"].get("head_ref_oid"):
            raise WorkspaceError("Terminal remote OID contradicts PR provenance")
        if journal["pr_provenance"].get("head_ref_name") != journal["branch"]:
            raise WorkspaceError("Terminal branch contradicts PR provenance")
    canonical = Path(os.path.abspath(str(git.worktrees_dir() / journal["slug"])))
    if journal["worktree"] is not None and Path(journal["worktree"]) != canonical:
        raise WorkspaceError("Terminal journal worktree path is non-canonical")
    root = get_workspace_dir()
    for entry in journal["active_entries"]:
        path = Path(entry["path"])
        if path.parent != root or (path.name != "active" and not path.name.startswith("active.")):
            raise WorkspaceError("Terminal journal active pointer path is invalid")
    if workspace_exists(ws.slug):
        if (ws.slug, ws.branch, ws.issue, ws.pr if not local else None) != (
            journal["slug"],
            journal["branch"],
            journal["issue"],
            journal["pr"],
        ):
            raise WorkspaceError("Workspace ownership contradicts terminal journal")
        if not local and ws.pr_provenance != journal["pr_provenance"]:
            raise WorkspaceError("Workspace PR provenance contradicts terminal journal")


def _parse_terminal_options(
    args: list[str],
    *,
    allowed: set[str],
) -> tuple[list[str], dict[str, str]] | None:
    """Parse the narrow long-option forms accepted before Click dispatch."""
    positionals: list[str] = []
    options: dict[str, str] = {}
    index = 0
    while index < len(args):
        token = args[index]
        if not token.startswith("--") or token == "--":
            positionals.append(token)
            index += 1
            continue
        name, separator, inline = token.partition("=")
        if name not in allowed or name in options:
            return None
        if separator:
            options[name] = inline
            index += 1
            continue
        if index + 1 >= len(args) or args[index + 1].startswith("--"):
            return None
        options[name] = args[index + 1]
        index += 2
    return positionals, options


def _terminal_recovery_outcome(
    ws: Workspace,
    command_args: list[str],
) -> tuple[bool, dict | None]:
    """Bind one exact terminal argv shape to the outcome it will publish."""
    if command_args == ["del"] or command_args == ["del", ws.slug]:
        return True, None

    if command_args and command_args[0] == "reject":
        parsed = _parse_terminal_options(
            command_args[1:],
            allowed={"--issue", "--reason", "--message"},
        )
        if parsed is None:
            return False, None
        positionals, options = parsed
        if len(positionals) > 1 or (positionals and positionals[0] != ws.slug):
            return False, None
        if set(options) not in (
            {"--reason", "--message"},
            {"--issue", "--reason", "--message"},
        ):
            return False, None
        if options.get("--reason") not in {
            "not-a-company",
            "company-not-found",
            "no-job-board",
            "no-open-positions",
            "duplicate",
        }:
            return False, None
        if "--issue" in options:
            try:
                requested_issue = int(options["--issue"])
            except ValueError:
                return False, None
            if requested_issue != ws.issue:
                return False, None
        if not isinstance(ws.issue, int):
            return False, None
        reason = options["--reason"]
        message = options["--message"]
        marker = f"<!-- validation-failed: {reason} -->"
        return True, {
            "marker": marker,
            "body": (
                f"{marker}\n"
                f"**This request could not be processed:** {message}\n\n"
                "If this was closed in error, reopen the issue with additional context."
            ),
            "labels": [reason] if reason == "duplicate" else [],
            "close_issue": True,
        }

    if command_args[:2] == ["task", "escalate"]:
        parsed = _parse_terminal_options(
            command_args[2:],
            allowed={"--issue", "--reason", "--follow-up"},
        )
        if parsed is None:
            return False, None
        positionals, options = parsed
        if positionals or set(options) not in (
            {"--reason", "--follow-up"},
            {"--issue", "--reason", "--follow-up"},
        ):
            return False, None
        if "--issue" in options:
            try:
                requested_issue = int(options["--issue"])
            except ValueError:
                return False, None
            if requested_issue != ws.issue:
                return False, None
        if not isinstance(ws.issue, int):
            return False, None
        marker = "<!-- resolver-outcome: escalated -->"
        return True, {
            "marker": marker,
            "body": (
                f"{marker}\n"
                "**Resolver escalated this request for human follow-up.**\n\n"
                f"Reason: {options['--reason']}\n"
                f"Follow-up: {options['--follow-up']}"
            ),
            "labels": [],
            "close_issue": True,
        }
    return False, None


def authenticated_terminal_recovery_root(
    ws: Workspace,
    command_args: list[str],
) -> Path | None:
    """Return the only safe startup root for an interrupted terminal command.

    Normal startup must authenticate the live persisted worktree.  The one
    legitimate exception is a pending terminal receipt that recorded the
    removal attempt while the exact persisted checkout still authenticated.
    In that state the checkout may either still be present (the mutation did
    not complete) or be exactly absent (the mutation completed before the
    process died).  Replacements and contradictory receipts still fail
    closed.
    """
    from src.workspace import git
    from src.workspace.worktree_auth import validate_persisted_worktree_identity

    requested, outcome = _terminal_recovery_outcome(ws, command_args)
    if not requested:
        return None
    journal, completed = _load_terminal_journal(ws.slug, issue=ws.issue)
    if journal is None or completed or not journal["attempts"]["worktree_remove"]:
        return None

    _cross_validate_terminal_journal(journal, ws, local=False, outcome=outcome)
    if _active_pointer_entries(ws.slug) != journal["active_entries"]:
        raise WorkspaceError("Terminal recovery active pointer contradicts its journal")
    canonical = validate_persisted_worktree_identity(ws)
    identity = ws.worktree_identity
    if (
        journal["worktree"] != str(canonical)
        or journal["worktree_head"] != identity["head"]
        or journal["worktree_dev"] != identity["dev"]
        or journal["worktree_ino"] != identity["ino"]
    ):
        raise WorkspaceError("Terminal recovery contradicts persisted worktree identity")
    if (
        journal["data_cleanup_required"]
        and journal["data_initially_present"]
        and not journal["attempts"]["data_remove"]
    ):
        raise WorkspaceError("Terminal recovery worktree removal preceded data cleanup")

    removal_state = git.authenticate_terminal_worktree_removal_state(
        canonical,
        journal["branch"],
        journal["worktree_head"],
        expected_dev=journal["worktree_dev"],
        expected_ino=journal["worktree_ino"],
    )
    if removal_state == "canonical":
        if git.local_branch_oid_strict(journal["branch"]) != journal["local_branch_oid"]:
            raise WorkspaceError("Terminal recovery local branch contradicts live worktree")
        return canonical
    observed_local = git.local_branch_oid_strict(journal["branch"])
    if observed_local not in {journal["local_branch_oid"], None}:
        raise WorkspaceError("Terminal recovery local branch changed after journaling")
    if observed_local is None and not journal["attempts"]["local_branch_remove"]:
        raise WorkspaceError("Terminal recovery local branch disappeared without an attempt")
    return Path(os.path.abspath(str(git.managed_repo())))


def _run_terminal_cleanup(
    ws: Workspace,
    *,
    local: bool,
    outcome: dict | None = None,
) -> None:
    """Re-observe and resume one strict write-ahead terminal transition."""
    from src.csvtool import board_del, company_del
    from src.workspace import git

    journal, completed = _load_terminal_journal(ws.slug, issue=ws.issue)
    if journal is None:
        journal = _initialize_terminal_journal(ws, local=local, outcome=outcome)
    else:
        _cross_validate_terminal_journal(journal, ws, local=local, outcome=outcome)

    branch = journal["branch"]
    expected_remote = journal["expected_remote_oid"]
    if not local:
        if journal["pr"] is not None:
            git.verify_recorded_pr_object(
                journal["pr_provenance"],
                pr_number=journal["pr"],
                branch=branch,
                issue=journal["issue"],
                slug=journal["slug"],
            )
        if journal["worktree"] is not None and not journal["attempts"]["worktree_remove"]:
            git.authenticate_managed_worktree(
                Path(journal["worktree"]),
                branch,
                journal["worktree_head"],
                expected_dev=journal["worktree_dev"],
                expected_ino=journal["worktree_ino"],
            )
        if branch:
            observed_local = git.local_branch_oid_strict(branch)
            expected_local = journal["local_branch_oid"]
            if expected_local is None and observed_local is not None:
                raise WorkspaceError("A new local branch appeared after terminal journaling")
            if expected_local is not None and observed_local not in {expected_local, None}:
                raise WorkspaceError("Local branch changed after terminal journaling")
            if (
                expected_local is not None
                and observed_local is None
                and not journal["attempts"]["local_branch_remove"]
            ):
                raise WorkspaceError("Local branch disappeared without an exact deletion attempt")

    if not local and expected_remote is not None:
        current_remote = git.remote_branch_oid_strict(branch)
        if current_remote == expected_remote:
            if completed:
                raise WorkspaceError("Remote branch reappeared after completed cleanup")
            _set_terminal_attempt(journal, "remote_delete")
            git.delete_remote_branch_at_expected_oid(branch, expected_remote)
        elif current_remote is None:
            if not journal["attempts"]["remote_delete"]:
                raise WorkspaceError("Remote branch disappeared without a recorded attempt")
        else:
            raise WorkspaceError("Remote branch changed after terminal journaling")
        if git.remote_branch_oid_strict(branch) is not None:
            raise WorkspaceError("Remote branch survived terminal cleanup")

    if not local and journal["pr"] is not None:
        details = git.verify_recorded_pr_object(
            journal["pr_provenance"],
            pr_number=journal["pr"],
            branch=branch,
            issue=journal["issue"],
            slug=journal["slug"],
        )
        if details.get("state") == "OPEN":
            if completed:
                raise WorkspaceError("PR reopened after completed cleanup")
            _set_terminal_attempt(journal, "pr_close")
            git.close_pr_if_open(journal["pr"])
            details = git.verify_recorded_pr_object(
                journal["pr_provenance"],
                pr_number=journal["pr"],
                branch=branch,
                issue=journal["issue"],
                slug=journal["slug"],
            )
        if details.get("state") != "CLOSED":
            raise WorkspaceError("PR was not closed by terminal cleanup")
        if not journal["attempts"]["pr_close"] and not journal["attempts"]["remote_delete"]:
            raise WorkspaceError("PR closed without a recorded terminal attempt")

    # The authenticated feature worktree is the authority for unmerged
    # add-company CSV rows.  Journal and complete their removal before the
    # worktree itself can disappear; a restart from managed main can then
    # interpret absence only through the durable data_remove attempt.
    company_present, board_present = _company_registry_presence(journal["slug"])
    if journal["data_cleanup_required"]:
        if company_present or board_present:
            if completed or not journal["data_initially_present"]:
                raise WorkspaceError("Company registry rows appeared after terminal journaling")
            _set_terminal_attempt(journal, "data_remove")
            if company_present:
                company_del(journal["slug"])
            elif board_present:
                board_del(journal["slug"])
            company_present, board_present = _company_registry_presence(journal["slug"])
        elif journal["data_initially_present"] and not journal["attempts"]["data_remove"]:
            raise WorkspaceError("Company registry rows disappeared without a recorded attempt")
        if company_present or board_present:
            raise WorkspaceError("Company registry rows survived terminal cleanup")

    worktree = journal["worktree"]
    if not local and worktree is not None:
        if completed:
            if git.managed_worktree_identity_strict(Path(worktree), branch) is not None:
                raise WorkspaceError("Worktree reappeared after completed cleanup")
        else:
            attempted = journal["attempts"]["worktree_remove"]
            if not attempted:
                git.authenticate_managed_worktree(
                    Path(worktree),
                    branch,
                    journal["worktree_head"],
                    expected_dev=journal["worktree_dev"],
                    expected_ino=journal["worktree_ino"],
                )
            _set_terminal_attempt(journal, "worktree_remove")
            git.remove_authenticated_worktree(
                Path(worktree),
                branch,
                journal["worktree_head"],
                expected_dev=journal["worktree_dev"],
                expected_ino=journal["worktree_ino"],
                absent_is_success=attempted,
            )
            from src.shared.constants import set_repo_root

            set_repo_root(git.managed_repo())

    expected_local = journal["local_branch_oid"]
    if not local and branch:
        current_local = git.local_branch_oid_strict(branch)
        if expected_local is None:
            if current_local is not None:
                raise WorkspaceError("A new local branch appeared after terminal journaling")
        elif current_local == expected_local:
            if completed:
                raise WorkspaceError("Local branch reappeared after completed cleanup")
            _set_terminal_attempt(journal, "local_branch_remove")
            git.delete_local_branch_at_expected_oid(branch, expected_local)
        elif current_local is None:
            if not journal["attempts"]["local_branch_remove"]:
                raise WorkspaceError("Local branch disappeared without an exact deletion attempt")
        else:
            raise WorkspaceError("Local branch changed after terminal journaling")

    if completed:
        if _active_pointer_entries(journal["slug"]):
            raise WorkspaceError("Active pointer reappeared after completed cleanup")
    else:
        _remove_active_pointers(journal)

    workspace_now = workspace_exists(journal["slug"])
    if workspace_now:
        if completed or not journal["workspace_was_present"]:
            raise WorkspaceError("Workspace appeared after terminal journaling")
        _set_terminal_attempt(journal, "workspace_remove")
        delete_workspace(journal["slug"])
    elif journal["workspace_was_present"] and not journal["attempts"]["workspace_remove"]:
        raise WorkspaceError("Workspace disappeared without a recorded removal attempt")
    if workspace_exists(journal["slug"]):
        raise WorkspaceError("Workspace survived terminal cleanup")

    if not local and journal["outcome"] is not None:
        if completed:
            state, labels = git.issue_state_and_labels_strict(journal["issue"])
            if (
                not git.issue_has_comment_marker_strict(
                    journal["issue"], journal["outcome"]["marker"]
                )
                or not set(journal["outcome"]["labels"]).issubset(labels)
                or (journal["outcome"]["close_issue"] and state != "CLOSED")
            ):
                raise WorkspaceError("Completed terminal issue outcome was altered")
        else:
            if not git.issue_has_comment_marker_strict(
                journal["issue"], journal["outcome"]["marker"]
            ):
                _set_terminal_attempt(journal, "issue_comment")
                git.comment_on_issue_once(
                    journal["issue"],
                    journal["outcome"]["marker"],
                    journal["outcome"]["body"],
                )
            if not git.issue_has_comment_marker_strict(
                journal["issue"], journal["outcome"]["marker"]
            ):
                raise WorkspaceError("Terminal issue comment was not published")
            issue_state, labels = git.issue_state_and_labels_strict(journal["issue"])
            missing = set(journal["outcome"]["labels"]) - labels
            if missing:
                _set_terminal_attempt(journal, "issue_labels")
                for label in sorted(missing):
                    git.add_label_to_issue(journal["issue"], label)
                issue_state, labels = git.issue_state_and_labels_strict(journal["issue"])
            if not set(journal["outcome"]["labels"]).issubset(labels):
                raise WorkspaceError("Terminal issue labels were not applied")
            if journal["outcome"]["close_issue"] and issue_state == "OPEN":
                _set_terminal_attempt(journal, "issue_close")
                git.close_issue_if_open(journal["issue"])
                issue_state, _ = git.issue_state_and_labels_strict(journal["issue"])
            if journal["outcome"]["close_issue"] and issue_state != "CLOSED":
                raise WorkspaceError("Terminal issue was not closed")

    if not completed:
        _finalize_terminal_journal(journal)
        completed = True

    # Claim release is deliberately last: the completed journal is the
    # durable receipt and there are no local or GitHub mutations afterward.
    if not local and journal["issue"] is not None:
        claimed = git.is_issue_claimed_strict(journal["issue"])
        if journal["claim_initially_present"]:
            if claimed:
                git.unclaim_issue_strict(journal["issue"])
                if git.is_issue_claimed_strict(journal["issue"]):
                    raise WorkspaceError("Issue claim survived terminal cleanup")
            elif not completed:
                raise WorkspaceError("Issue claim disappeared before terminal completion")
        elif claimed:
            raise WorkspaceError("A new issue claim appeared during terminal cleanup")
    out.info("workspace", f"Removed workspace {journal['slug']!r}")


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
            journal, _ = _load_terminal_journal(slug)
            if journal is None:
                out.die(
                    f"Workspace {slug!r} has no recorded ownership state; "
                    "refusing best-effort PR or branch deletion"
                )
            assert journal is not None
            ws = Workspace(slug=slug, branch=journal["branch"], issue=journal["issue"])
        else:
            ws = load_workspace(slug)
            branch = ws.branch
            if branch not in {f"add-company/{slug}", f"fix-crawler/{slug}"}:
                out.die(
                    f"Workspace branch {branch!r} is not bound to slug {slug!r}; "
                    "refusing destructive cleanup"
                )
        _run_terminal_cleanup(ws, local=local, outcome=None)


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
    _rebind_workspace_worktree_identity(ws)
    save_workspace(ws)


def _authenticate_workspace_worktree(ws: Workspace) -> None:
    """Authenticate the exact persisted checkout before a Git/PR mutation."""
    from src.workspace.worktree_auth import authenticate_workspace_worktree

    authenticate_workspace_worktree(ws)


def _advance_workspace_worktree_head(ws: Workspace, previous: str, current: str) -> None:
    """Persist an exact same-checkout HEAD advance after a journaled commit."""
    if not ws.worktree:
        return
    from src.workspace import git

    identity = ws.worktree_identity
    if identity.get("head") != previous:
        raise WorkspaceError("Workspace head changed before its journaled advance")
    canonical = Path(os.path.abspath(str(git.worktrees_dir() / ws.slug)))
    actual = git.managed_worktree_identity_strict(canonical, ws.branch)
    if (
        actual is None
        or actual["head"] != current
        or actual["dev"] != identity.get("dev")
        or actual["ino"] != identity.get("ino")
        or git.local_branch_oid_strict(ws.branch) != current
    ):
        raise WorkspaceError("Worktree identity changed during journaled HEAD advance")
    identity["head"] = current
    save_workspace(ws)


def _rebind_workspace_worktree_identity(ws: Workspace) -> None:
    """Bind the existing checkout to newly authenticated PR provenance."""
    if not ws.worktree:
        return
    identity = ws.worktree_identity
    # The checkout itself must still authenticate under the old provenance.
    old_pr = identity.get("pr")
    old_provenance = identity.get("pr_provenance")
    identity["pr"] = ws.pr
    identity["pr_provenance"] = copy.deepcopy(ws.pr_provenance)
    try:
        _authenticate_workspace_worktree(ws)
    except Exception:
        identity["pr"] = old_pr
        identity["pr_provenance"] = old_provenance
        raise


def _verify_workspace_pr_before_mutation(ws: Workspace) -> None:
    """Authenticate a recorded PR immediately before a non-create mutation."""
    from src.workspace import git

    _authenticate_workspace_worktree(ws)
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

        _authenticate_workspace_worktree(ws)

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
        previous_head = git.current_head_oid_strict()
        _authenticate_workspace_worktree(ws)
        if ws.pr is not None:
            _verify_workspace_pr_before_mutation(ws)
        git.commit(commit_msg)
        _advance_workspace_worktree_head(ws, previous_head, git.current_head_oid_strict())

    elif step_key == "pushed":
        if local:
            return  # Local mode — skip push
        from src.workspace import git

        _authenticate_workspace_worktree(ws)

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
            _authenticate_workspace_worktree(ws)
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
            _authenticate_workspace_worktree(ws)
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

    # Authenticate before trusting the persisted path, then pivot and repeat
    # immediately. A marker directory is not ownership proof.
    if not is_local_mode():
        from src.workspace.worktree_auth import pivot_to_authenticated_worktree

        pivot_to_authenticated_worktree(ws)

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
                if not is_local_mode() and ws.issue and ws.pr:
                    from src.workspace import git

                    run_id = os.environ.get("JOBSEEK_CODEX_RUN_ID", "interactive")
                    marker = f"<!-- resolver-pr-lease-blocked:{run_id}:{ws.pr} -->"
                    with contextlib.suppress(Exception):
                        git.comment_on_issue_once(
                            ws.issue,
                            marker,
                            (
                                f"{marker}\nResolver safety audit: stopped before "
                                f"`{step_key}` for PR #{ws.pr}; no unleased branch overwrite "
                                f"was attempted. Reason: {e}"
                            ),
                        )
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
