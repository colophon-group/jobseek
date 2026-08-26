from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
REQUIRED_KEYS = {
    "credential_suffixes",
    "email_names",
    "secret_names",
    "sensitive_headers",
}


def load_registry() -> dict[str, list[str]]:
    registry = json.loads((ROOT / "privacy_registry.json").read_text())
    if set(registry) != REQUIRED_KEYS:
        raise ValueError("privacy registry keys differ from the closed v1 schema")
    for key, values in registry.items():
        if (
            not isinstance(values, list)
            or not values
            or values != sorted(values)
            or len(values) != len(set(values))
            or any(
                not isinstance(value, str) or not value or value != value.lower()
                for value in values
            )
        ):
            raise ValueError(f"invalid privacy registry values for {key}")
    return registry


def render_python(registry: dict[str, list[str]]) -> str:
    lines = [
        "# Code generated from privacy_registry.json by tools/generate_privacy.py. DO NOT EDIT.",
        "",
    ]
    for key, target in (
        ("credential_suffixes", "CREDENTIAL_SUFFIXES"),
        ("email_names", "EMAIL_NAMES"),
        ("secret_names", "SECRET_NAMES"),
        ("sensitive_headers", "SENSITIVE_HEADERS"),
    ):
        values = ", ".join(repr(value) for value in registry[key])
        constructor = "tuple" if key == "credential_suffixes" else "frozenset"
        lines.append(f"{target} = {constructor}(({values},))")
    lines.append("")
    return "\n".join(lines)


def render_go(registry: dict[str, list[str]]) -> str:
    lines = [
        "// Code generated from privacy_registry.json by tools/generate_privacy.py. DO NOT EDIT.",
        "",
        "package conformance",
        "",
    ]
    suffixes = ", ".join(json.dumps(value) for value in registry["credential_suffixes"])
    lines.extend([f"var credentialSuffixes = []string{{{suffixes}}}", ""])
    for key, target in (
        ("email_names", "emailNames"),
        ("secret_names", "secretNames"),
        ("sensitive_headers", "sensitiveHeaders"),
    ):
        lines.append(f"var {target} = map[string]bool{{")
        lines.extend(f"\t{json.dumps(value)}: true," for value in registry[key])
        lines.extend(["}", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-out", type=Path, required=True)
    parser.add_argument("--go-out", type=Path, required=True)
    args = parser.parse_args()
    registry = load_registry()
    args.python_out.write_text(render_python(registry))
    args.go_out.write_text(render_go(registry))


if __name__ == "__main__":
    main()
