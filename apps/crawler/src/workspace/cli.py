"""Click CLI entry point for the ``ws`` workspace tool."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click

from src.workspace import output as out
from src.workspace.commands.config import add_board, del_board, discover, logos, set_
from src.workspace.commands.crawl import (
    feedback_cmd,
    probe_api,
    probe_deep,
    probe_monitors,
    probe_scraper,
    reject_config,
    run_monitor,
    run_scraper,
    select_config,
    select_monitor,
    select_scraper,
)
from src.workspace.commands.help import help_cmd
from src.workspace.commands.lifecycle import (
    del_,
    new,
    reject,
    resume,
    status,
    submit,
    use,
    validate,
)
from src.workspace.commands.task import task
from src.workspace.errors import WorkspaceError


@click.group()
def ws():
    """Workspace CLI for managing company additions."""


# ── Top-level commands ──────────────────────────────────────────────────

ws.add_command(new)
ws.add_command(use)
ws.add_command(set_, name="set")
ws.add_command(submit)
ws.add_command(reject)
ws.add_command(status)
ws.add_command(validate)
ws.add_command(resume)
ws.add_command(help_cmd, name="help")
ws.add_command(logos)
ws.add_command(discover)
ws.add_command(feedback_cmd, name="feedback")
ws.add_command(reject_config, name="reject-config")
ws.add_command(task, name="task")


# ── `ws add` group ──────────────────────────────────────────────────────


@ws.group(name="add")
def add_group():
    """Add resources to a workspace."""


add_group.add_command(add_board, name="board")


# ── `ws probe` group ────────────────────────────────────────────────────


@ws.group(name="probe")
def probe_group():
    """Probe monitor or scraper types for the active board."""


probe_group.add_command(probe_monitors, name="monitor")
probe_group.add_command(probe_scraper, name="scraper")
probe_group.add_command(probe_deep, name="deep")
probe_group.add_command(probe_api, name="api")


# ── `ws del` group ──────────────────────────────────────────────────────


@ws.group(name="del", invoke_without_command=True)
@click.argument("slug", required=False)
@click.pass_context
def del_group(ctx, slug):
    """Delete a workspace or its resources."""
    if ctx.invoked_subcommand is None:
        # Resolve slug from active workspace if not provided
        if slug is None:
            from src.workspace.state import resolve_slug

            slug = resolve_slug(None)
        ctx.invoke(del_, slug=slug)


del_group.add_command(del_board, name="board")


# ── `ws select` group ──────────────────────────────────────────────────


@ws.group(name="select")
def select_group():
    """Select monitor or scraper type."""


select_group.add_command(select_monitor, name="monitor")
select_group.add_command(select_scraper, name="scraper")
select_group.add_command(select_config, name="config")


# ── `ws run` group ─────────────────────────────────────────────────────


@ws.group(name="run")
def run_group():
    """Run monitor or scraper tests."""


run_group.add_command(run_monitor, name="monitor")
run_group.add_command(run_scraper, name="scraper")


def _detect_repo_root() -> Path | None:
    """Detect the jobseek repo root in priority order.

    1. ``WS_REPO_ROOT`` env var (explicit override)
    2. CWD inside a git repo that contains ``apps/crawler/data/``
    3. Managed clone at ``~/.jobseek/repo/``
    """
    # 1. Env var override
    env = os.environ.get("WS_REPO_ROOT")
    if env:
        p = Path(env)
        if (p / "apps" / "crawler" / "data").exists():
            return p

    # 2. CWD inside repo
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        root = Path(result.stdout.strip())
        if (root / "apps" / "crawler" / "data").exists():
            return root
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 3. Managed clone
    managed = Path.home() / ".jobseek" / "repo"
    if (managed / "apps" / "crawler" / "data").exists():
        return managed

    return None


def main():
    from src.shared.constants import set_repo_root

    repo_root = _detect_repo_root()
    if repo_root:
        set_repo_root(repo_root)

    try:
        ws(standalone_mode=False)
    except click.exceptions.Exit:
        pass
    except click.ClickException as e:
        e.show()
        sys.exit(e.exit_code)
    except WorkspaceError as e:
        out.die(str(e))
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)
