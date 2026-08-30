"""CLI for deterministic offline crawler migration gate evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from jsonschema import Draft202012Validator, FormatChecker

from src.migration_gates.model import GateModelError, evaluate_promotion

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "migration-gates" / "schemas"


def _reject_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON number {value} is forbidden")


def _load(path: Path, schema_name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(), parse_constant=_reject_constant)
        schema = json.loads((SCHEMA_DIR / schema_name).read_text(), parse_constant=_reject_constant)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise GateModelError(f"cannot load {path}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise GateModelError(f"{path} must contain one JSON object")
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.absolute_path) or "<root>"
        raise GateModelError(f"{path} violates {schema_name} at {location}: {first.message}")
    return value


def _write(path: Path | None, value: dict[str, Any]) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path is None:
        sys.stdout.write(payload)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.migration_gates")
    subparsers = parser.add_subparsers(dest="command", required=True)
    evaluate = subparsers.add_parser("evaluate", help="evaluate sanitized aggregate evidence")
    evaluate.add_argument("--policy", type=Path, required=True)
    evaluate.add_argument("--evidence", type=Path, required=True)
    evaluate.add_argument("--out", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        policy = _load(args.policy, "promotion-policy-v1.schema.json")
        evidence = _load(args.evidence, "promotion-evidence-v1.schema.json")
        decision = evaluate_promotion(policy, evidence)
        decision_schema = json.loads((SCHEMA_DIR / "promotion-decision-v1.schema.json").read_text())
        Draft202012Validator(decision_schema).validate(decision)
        _write(args.out, decision)
        return {"promote": 0, "hold": 3, "freeze": 4}[decision["decision"]]
    except GateModelError as exc:
        print(f"migration-gates: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
