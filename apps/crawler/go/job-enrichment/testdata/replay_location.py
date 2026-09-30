"""Replay a protected read-only production index and expected location cases."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import time
from pathlib import Path

from src.runtime.location_go import GoLocationIndex

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("snapshot", type=Path)
parser.add_argument("--binary", required=True)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
if args.snapshot.stat().st_mode & 0o077:
    raise SystemExit("snapshot must have mode 0600")
snapshot = json.loads(args.snapshot.read_text())
index = GoLocationIndex()
index.client.binary = args.binary
raw = base64.b64decode(snapshot["index_base64"], validate=True)
index.index_path.write_bytes(raw)
index.index_path.chmod(0o600)
errors = []
started = time.monotonic()
try:
    for i, case in enumerate(snapshot["cases"]):
        actual, _, _ = index.resolve(
            case["raw"], case["fallback"], case["language"], tracking=False, negative=set()
        )
        if [{"location_id": lid, "location_type": kind} for lid, kind in actual] != case[
            "expected"
        ]:
            errors.append({"case": i})
    for i, case in enumerate(snapshot["details"]):
        if (
            index.display_name(case["id"]) != case["name"]
            or sorted(index.ancestors(case["id"])) != case["ancestors"]
        ):
            errors.append({"detail": i})
finally:
    index.close()
result = {
    "source_utc": snapshot["utc"],
    "index_sha256": hashlib.sha256(raw).hexdigest(),
    "binary_sha256": hashlib.sha256(Path(args.binary).read_bytes()).hexdigest(),
    "index_bytes": len(raw),
    "entries": snapshot["entry_count"],
    "resolution_cases": len(snapshot["cases"]),
    "detail_cases": len(snapshot["details"]),
    "drift_count": len(errors),
    "elapsed_seconds": round(time.monotonic() - started, 3),
    "errors": errors,
}
if args.output:
    # Operator-selected output is protected, even when it already exists.
    args.output.touch(mode=0o600, exist_ok=True)
    args.output.chmod(0o600)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
print({key: value for key, value in result.items() if key != "errors"})
if errors:
    raise SystemExit("production taxonomy replay drift")
