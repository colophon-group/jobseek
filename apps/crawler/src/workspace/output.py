"""Terminal output helpers with category-prefixed lines."""

from __future__ import annotations

import sys


# Column width for the category prefix
_CAT_WIDTH = 14


def info(category: str, msg: str) -> None:
    """Print an info line: ``  category   ✓ msg``."""
    print(f"  {category:<{_CAT_WIDTH}}\u2713 {msg}")


def warn(category: str, msg: str) -> None:
    """Print a warning line: ``  category   ⚠ msg``."""
    print(f"  {category:<{_CAT_WIDTH}}\u26a0 {msg}")


def error(category: str, msg: str) -> None:
    """Print an error line to stderr: ``  category   ✗ msg``."""
    print(f"  {category:<{_CAT_WIDTH}}\u2717 {msg}", file=sys.stderr)


def plain(category: str, msg: str) -> None:
    """Print a plain line without status symbol."""
    print(f"  {category:<{_CAT_WIDTH}}{msg}")


def next_step(cmd: str) -> None:
    """Print a 'Next:' hint."""
    print(f"  {'':>{_CAT_WIDTH}}Next: {cmd}")


def table(headers: list[str], rows: list[list[str]]) -> None:
    """Print a simple aligned table."""
    if not rows:
        return

    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], len(cell))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print("  " + fmt.format(*headers))
    print("  " + "  ".join("\u2500" * w for w in widths))
    for row in rows:
        padded = row + [""] * (len(headers) - len(row))
        print("  " + fmt.format(*padded))


def die(msg: str) -> None:
    """Print error and exit with code 1."""
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)
