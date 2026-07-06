from __future__ import annotations

import sys


def tty_message(message: str) -> None:
    """Show operator-facing text only for interactive terminal runs."""
    if not sys.stderr.isatty():
        return

    sys.stderr.write(message)
    if not message.endswith("\n"):
        sys.stderr.write("\n")
    sys.stderr.flush()
