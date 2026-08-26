from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
ENCODINGS = {
    "canonical_json": (
        2,
        "runtimev1.ExtensionEncoding_EXTENSION_ENCODING_CANONICAL_JSON",
    )
}


def load_rules() -> list[dict[str, object]]:
    rules = json.loads((ROOT / "extension_registry.json").read_text())
    schema_ids = [rule["schema_id"] for rule in rules]
    if len(schema_ids) != len(set(schema_ids)):
        raise ValueError("extension schema IDs must be unique")
    for rule in rules:
        if (
            set(rule) != {"contexts", "encoding", "schema_id", "schema_version", "validator"}
            or rule["encoding"] not in ENCODINGS
            or not rule["contexts"]
            or rule["schema_version"] != 1
        ):
            raise ValueError(f"invalid extension registry rule: {rule!r}")
    return rules


def render_python(rules: list[dict[str, object]]) -> str:
    lines = [
        "# Code generated from extension_registry.json by "
        "tools/generate_extensions.py. DO NOT EDIT.",
        "",
        "EXTENSION_RULES = {",
    ]
    for rule in rules:
        contexts = ", ".join(repr(value) for value in rule["contexts"])
        encoding, _ = ENCODINGS[rule["encoding"]]
        lines.extend(
            [
                f"    {rule['schema_id']!r}: {{",
                f"        'version': {rule['schema_version']},",
                f"        'encoding': {encoding},",
                f"        'contexts': frozenset(({contexts},)),",
                f"        'validator': {rule['validator']!r},",
                "    },",
            ]
        )
    lines.extend(["}", ""])
    return "\n".join(lines)


def render_go(rules: list[dict[str, object]]) -> str:
    lines = [
        "// Code generated from extension_registry.json by "
        "tools/generate_extensions.py. DO NOT EDIT.",
        "",
        "package conformance",
        "",
        'import runtimev1 "github.com/colophon-group/jobseek/apps/crawler/contracts/v1/gen/go"',
        "",
        "type extensionRule struct {",
        "\tversion uint32",
        "\tencoding runtimev1.ExtensionEncoding",
        "\tcontexts map[string]bool",
        "\tvalidator string",
        "}",
        "",
        "var extensionRules = map[string]extensionRule{",
    ]
    for rule in rules:
        contexts = ", ".join(f"{json.dumps(value)}: true" for value in rule["contexts"])
        _, encoding = ENCODINGS[rule["encoding"]]
        lines.append(
            f"\t{json.dumps(rule['schema_id'])}: "
            f"{{version: {rule['schema_version']}, encoding: {encoding}, "
            f"contexts: map[string]bool{{{contexts}}}, "
            f"validator: {json.dumps(rule['validator'])} }},"
        )
    lines.extend(["}", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python-out", type=Path, required=True)
    parser.add_argument("--go-out", type=Path, required=True)
    args = parser.parse_args()
    rules = load_rules()
    args.python_out.write_text(render_python(rules))
    args.go_out.write_text(render_go(rules))


if __name__ == "__main__":
    main()
