#!/usr/bin/env python3
"""Mirror stdin to stdout while retaining only a bounded parseable tail."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

DEFAULT_MAX_BYTES = 16 * 1024 * 1024


def stream_bounded_tail(output: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> int:
    if max_bytes < 1024:
        raise ValueError("max_bytes must be at least 1024")
    tail = bytearray()
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while chunk := stdin.read(64 * 1024):
        stdout.write(chunk)
        stdout.flush()
        tail.extend(chunk)
        if len(tail) > max_bytes:
            del tail[: len(tail) - max_bytes]
    if len(tail) == max_bytes:
        newline = tail.find(b"\n")
        if newline >= 0:
            del tail[: newline + 1]

    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(tail)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return len(tail)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = parser.parse_args()
    stream_bounded_tail(args.output, max_bytes=args.max_bytes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
