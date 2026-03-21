#!/usr/bin/env python3
"""Export Claude Code transcript for a ws workspace run.

Usage:
    uv run python scripts/export_trace.py <slug>
    uv run python scripts/export_trace.py --latest
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add parent to path so src is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.shared.constants import get_data_dir, get_workspace_dir
from src.workspace.state import list_workspaces
from src.workspace.trace import export_trace


def main():
    parser = argparse.ArgumentParser(description="Export Claude Code transcript for a ws run")
    parser.add_argument("slug", nargs="?", help="Company slug to export")
    parser.add_argument(
        "--latest", action="store_true", help="Export the most recently modified workspace"
    )
    parser.add_argument(
        "--all", action="store_true", help="Export all workspaces that don't have traces yet"
    )
    args = parser.parse_args()

    output_dir = get_data_dir().parent / "traces"

    if args.latest:
        workspaces = list_workspaces()
        if not workspaces:
            print("No workspaces found")
            sys.exit(1)
        # Pick most recently modified
        ws_dir = get_workspace_dir()
        by_mtime = sorted(
            workspaces, key=lambda s: (ws_dir / s / "workspace.yaml").stat().st_mtime, reverse=True
        )
        slug = by_mtime[0]
        print(f"Exporting latest workspace: {slug}")
        path = export_trace(slug, output_dir)
        if path:
            print(f"Exported: {path}")
        else:
            print("No matching transcript found")
            sys.exit(1)

    elif args.all:
        workspaces = list_workspaces()
        exported = 0
        for slug in workspaces:
            # Skip if trace already exists
            slug_dir = output_dir / slug
            if slug_dir.exists() and any(slug_dir.iterdir()):
                continue
            path = export_trace(slug, output_dir)
            if path:
                print(f"Exported: {path}")
                exported += 1
        print(f"Exported {exported} trace(s)")

    elif args.slug:
        path = export_trace(args.slug, output_dir)
        if path:
            print(f"Exported: {path}")
        else:
            print(f"No matching transcript found for {args.slug}")
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
