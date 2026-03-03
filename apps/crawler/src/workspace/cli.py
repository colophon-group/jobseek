"""Click CLI entry point for the ``ws`` workspace tool."""

from __future__ import annotations

import click

from src.workspace.commands.config import add_board, del_board, set_
from src.workspace.commands.crawl import (
    probe_monitors,
    probe_scraper,
    run_monitor,
    run_scraper,
    select_monitor,
    select_scraper,
)
from src.workspace.commands.help import help_cmd
from src.workspace.commands.lifecycle import del_, new, reject, status, submit, use, validate


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
ws.add_command(help_cmd, name="help")


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


# ── `ws run` group ─────────────────────────────────────────────────────

@ws.group(name="run")
def run_group():
    """Run monitor or scraper tests."""

run_group.add_command(run_monitor, name="monitor")
run_group.add_command(run_scraper, name="scraper")


def main():
    ws()
