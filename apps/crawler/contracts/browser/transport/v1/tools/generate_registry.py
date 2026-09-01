#!/usr/bin/env python3
"""Generate or verify the frozen Chromium transport v1 registry."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

CRAWLER_ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(CRAWLER_ROOT))

from src.shared.browser_transport import (  # noqa: E402
    audit_metric_series,
    canonical_registry_bytes,
    load_registry,
    registry_digest,
)

REGISTRY_DIR = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REGISTRY_DIR / "registry.json"
DIGEST_PATH = REGISTRY_DIR / "registry.sha256"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    payload = canonical_registry_bytes()
    digest_payload = f"{registry_digest()}  registry.json\n".encode()
    if args.check:
        if not REGISTRY_PATH.exists() or REGISTRY_PATH.read_bytes() != payload:
            print("registry.json differs from canonical output", file=sys.stderr)
            return 1
        if not DIGEST_PATH.exists() or DIGEST_PATH.read_bytes() != digest_payload:
            print("registry.sha256 differs from canonical output", file=sys.stderr)
            return 1
        registry = load_registry(REGISTRY_PATH)
        audit_metric_series(registry)
        return 0

    REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_bytes(payload)
    DIGEST_PATH.write_bytes(digest_payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
