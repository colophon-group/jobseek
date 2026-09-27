"""Create / update Typesense collections and aliases for jobseek.

Thin compatibility wrapper that execs the Go schema setup runtime. The
crawler deployment invokes the same executable directly.

Run from the crawler directory so that ``src.config`` resolves:

    cd apps/crawler && uv run python ../../scripts/typesense-setup.py

Flags:
    --force   Drop existing collections and recreate from scratch.
"""

from __future__ import annotations

import argparse
import os

import dotenv


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Set up Typesense collections for jobseek"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Drop existing collections and recreate from scratch",
    )
    args = parser.parse_args()

    dotenv.load_dotenv(".env.local")
    dotenv.load_dotenv(".env")
    command = ["go-typesense-exporter", "--setup-schemas"]
    if args.force:
        command.append("--force")
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
