"""Configuration commands: set, add board, del board."""

from __future__ import annotations

import click

from src.shared.constants import SLUG_RE
from src.workspace import log as action_log
from src.workspace import output as out
from src.workspace.state import (
    Board,
    board_yaml_path,
    list_boards,
    load_workspace,
    resolve_slug,
    resolve_two_args,
    save_board,
    save_workspace,
    workspace_exists,
    ws_log_path,
)


@click.command(name="set")
@click.argument("slug", required=False)
@click.option("--name", help="Company display name")
@click.option("--website", help="Company homepage URL")
@click.option("--logo-url", help="Logo image URL")
@click.option("--icon-url", help="Icon image URL")
def set_(
    slug: str | None,
    name: str | None,
    website: str | None,
    logo_url: str | None,
    icon_url: str | None,
):
    """Set company metadata in workspace."""
    slug = resolve_slug(slug)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    ws = load_workspace(slug)
    updates = []

    if name is not None:
        ws.name = name
        updates.append(f"name={name!r}")
    if website is not None:
        ws.website = website
        updates.append(f"website={website!r}")
        _check_url("website", website)
    if logo_url is not None:
        ws.logo_url = logo_url
        updates.append("logo_url")
        _check_image("logo_url", logo_url)
    if icon_url is not None:
        ws.icon_url = icon_url
        updates.append("icon_url")
        _check_image("icon_url", icon_url)

    if not updates:
        out.die("Nothing to set. Provide at least one --option.")

    save_workspace(ws)

    action_log.append(
        ws_log_path(slug),
        "set",
        True,
        f"Set {', '.join(updates)}",
    )
    out.info("workspace", f"Set {', '.join(updates)}")


def _check_url(label: str, url: str) -> None:
    """Advisory URL reachability check."""
    try:
        import httpx

        resp = httpx.head(url, follow_redirects=True, timeout=10)
        if resp.status_code < 400:
            final = str(resp.url)
            if final != url:
                out.warn(label, f"Redirects to {final}")
            else:
                out.info(label, f"Reachable ({resp.status_code})")
        else:
            out.warn(label, f"HTTP {resp.status_code}")
    except Exception as e:
        out.warn(label, f"Could not reach: {e}")


def _check_image(label: str, url: str) -> None:
    """Advisory image probe — check content type and size."""
    try:
        import httpx

        resp = httpx.get(url, follow_redirects=True, timeout=10)
        ct = resp.headers.get("content-type", "")
        size = len(resp.content)
        if "image" in ct or "svg" in ct:
            out.info(label, f"{ct}, {size:,} bytes")
        else:
            out.warn(label, f"Not an image: {ct}, {size:,} bytes")
    except Exception as e:
        out.warn(label, f"Could not fetch: {e}")


@click.command(name="board")
@click.argument("slug_or_alias")
@click.argument("alias", required=False)
@click.option("--url", required=True, help="Board URL")
def add_board(slug_or_alias: str, alias: str | None, url: str):
    """Add a board to workspace."""
    slug, alias = resolve_two_args(slug_or_alias, alias)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    # Check for double-prefix
    if alias.startswith(f"{slug}-"):
        board_slug = alias
        out.warn(
            "board",
            f"Alias {alias!r} already prefixed — board slug will be {alias!r}. "
            f"Did you mean {alias.removeprefix(f'{slug}-')!r}?",
        )
    else:
        board_slug = f"{slug}-{alias}"

    if not SLUG_RE.match(board_slug):
        out.die(f"Invalid board slug: {board_slug!r}")

    ws = load_workspace(slug)
    board = Board(alias=alias, slug=board_slug, url=url)
    save_board(slug, board)

    # Auto-activate
    ws.active_board = alias
    ws.progress["board_added"] = True
    save_workspace(ws)

    action_log.append(
        ws_log_path(slug),
        "add board",
        True,
        f"Added board {alias} — {url}",
    )

    # Append to board's embedded log
    action_log.append_to_list(board.log, "add board", True, f"Added board {alias} — {url}")
    save_board(slug, board)

    out.info("board", f"Added board {board_slug} — {url}")
    out.plain("board", f"Active board: {board_slug}")
    out.next_step("ws probe monitor")


@click.command(name="board")
@click.argument("slug_or_alias")
@click.argument("alias", required=False)
def del_board(slug_or_alias: str, alias: str | None):
    """Remove a board from workspace."""
    slug, alias = resolve_two_args(slug_or_alias, alias)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    path = board_yaml_path(slug, alias)
    if not path.exists():
        out.die(f"Board {alias!r} not found in workspace {slug!r}")

    path.unlink()
    out.info("board", f"Removed board {alias!r}")

    # Switch active board if needed
    ws = load_workspace(slug)
    if ws.active_board == alias:
        remaining = list_boards(slug)
        ws.active_board = remaining[0].alias if remaining else ""
        save_workspace(ws)
        if ws.active_board:
            out.plain("board", f"Active board switched to: {ws.active_board}")
        else:
            out.plain("board", "No boards remaining")
