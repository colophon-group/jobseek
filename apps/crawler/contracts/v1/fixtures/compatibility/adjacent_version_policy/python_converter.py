#!/usr/bin/env python3
"""Fixture-only adjacent-version converter; never imported by crawler runtime."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _convert(direction: str, payload: dict[str, Any]) -> dict[str, Any]:
    converted = dict(payload)
    losses: list[dict[str, str]] = []
    if direction == "old_to_new" and "legacy_note" in converted:
        converted.pop("legacy_note")
        losses.append(
            {
                "path": "$.legacy_note",
                "reason": "field removed in adjacent test version",
            }
        )
    if direction == "new_to_old" and "future_hint" in converted:
        converted.pop("future_hint")
        losses.append(
            {
                "path": "$.future_hint",
                "reason": "field unavailable in adjacent test version",
            }
        )
    return {"losses": losses, "payload": converted}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--direction", choices=("old_to_new", "new_to_old"), required=True)
    arguments = parser.parse_args()
    document = json.load(sys.stdin)
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("converter input must contain nonempty cases")
    results = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str):
            raise ValueError("converter case must have a string id")
        payload = case.get("payload")
        if not isinstance(payload, dict):
            raise ValueError("converter case payload must be an object")
        results.append({"id": case["id"], **_convert(arguments.direction, payload)})
    rendered = json.dumps(
        {"results": results},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    sys.stdout.write(rendered + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
