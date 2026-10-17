#!/usr/bin/env python3
"""Assemble a private registry; the pinned renderer validates endpoint policy."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def registry_bytes(environment: dict[str, str]) -> bytes:
    provider = environment.get("PROXY_PROVIDER", "").strip().lower() or "none"
    if provider not in {"none", "webshare"}:
        raise ValueError("invalid browser proxy provider")
    entries: list[str] = []
    if provider == "webshare":
        raw = environment.get("WEBSHARE_PROXY_URLS", "")
        if len(raw.encode()) > 1 << 20:
            raise ValueError("browser proxy registry exceeds limit")
        if raw:
            entries = json.loads(raw)
            if not isinstance(entries, list):
                raise ValueError("invalid browser proxy registry")
        if not entries and environment.get("WEBSHARE_PROXY_URL"):
            entries = [environment["WEBSHARE_PROXY_URL"]]
        if not 1 <= len(entries) <= 64 or any(
            not isinstance(entry, str) or not entry.strip() or len(entry.encode()) > 8192
            for entry in entries
        ):
            raise ValueError("invalid browser proxy registry")
        # The existing native worker normalizes protected settings this way.
        entries = [entry.strip() for entry in entries]
    encoded = (json.dumps(entries, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > 1 << 20:
        raise ValueError("browser proxy registry exceeds limit")
    return encoded


def write_registry(path: Path, environment: dict[str, str]) -> None:
    encoded = registry_bytes(environment)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(encoded)
        output.flush()
        os.fsync(output.fileno())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        write_registry(args.output, dict(os.environ))
    except (ValueError, TypeError, OSError):
        # Exceptions from JSON and filesystem libraries may echo private input.
        raise SystemExit("browser proxy registry preparation failed") from None


if __name__ == "__main__":
    main()
