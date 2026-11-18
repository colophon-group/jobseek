"""Freeze real Python CSRF validation with synthetic, non-production values."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.monitors.dayforce import _csrf_headers


def main() -> None:
    cases = []
    for name, headers in [
        ("minimum", {"x-csrf-token": "A" * 32}),
        ("maximum", {"X-Csrf-Token": "b" * 512}),
        ("alphabet", {"X-CSRF-TOKEN": "A0._~-" * 6, "Cookie": "synthetic-only"}),
        ("missing", {}),
        ("short", {"x-csrf-token": "A" * 31}),
        ("oversized", {"x-csrf-token": "A" * 513}),
        ("unicode", {"x-csrf-token": "é" * 32}),
        ("line-break", {"x-csrf-token": "A" * 32 + "\r\n"}),
        ("space", {"x-csrf-token": "A" * 32 + " "}),
        ("percent", {"x-csrf-token": "A" * 32 + "%"}),
        ("non-string", {"x-csrf-token": 123}),
        ("null", {"x-csrf-token": None}),
    ]:
        try:
            result = _csrf_headers(headers)
            error = False
        except (ValueError, TypeError):
            result, error = None, True
        cases.append({"name": name, "headers": headers, "result": result, "error": error})
    Path(__file__).with_name("python_dayforce_headers.json").write_text(
        json.dumps(cases, indent=2) + "\n"
    )
    print(f"froze {len(cases)} actual Python CSRF-header cases")


if __name__ == "__main__":
    main()
