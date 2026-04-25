"""Create / update Typesense collections and aliases for jobseek.

Thin wrapper around ``src.typesense_schema``. The same logic is exposed
in the crawler image as ``crawler setup-typesense`` (called by deploy.sh).

Run from the crawler directory so that ``src.config`` resolves:

    cd apps/crawler && uv run python ../../scripts/typesense-setup.py

Flags:
    --force   Drop existing collections and recreate from scratch.
"""

from __future__ import annotations

import argparse

from src.typesense_schema import run_setup


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up Typesense collections for jobseek")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Drop existing collections and recreate from scratch",
    )
    args = parser.parse_args()

    run_setup(force=args.force)


if __name__ == "__main__":
    main()
