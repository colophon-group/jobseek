"""Command line interface for reproducible runtime-cost evidence."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from src.runtime_cost.model import ModelError, project_runtime_cost
from src.runtime_cost.prometheus import PrometheusClient, capture_prometheus_measurement

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "runtime-cost" / "schemas"


def _load(path: Path, schema_name: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ModelError(f"cannot read JSON from {path}: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ModelError(f"{path} must contain one JSON object")
    schema = json.loads((SCHEMA_DIR / schema_name).read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(value), key=lambda error: list(error.absolute_path))
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.absolute_path) or "<root>"
        raise ModelError(f"{path} violates {schema_name} at {location}: {first.message}")
    return value


def _write(path: Path | None, value: dict) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path is None:
        sys.stdout.write(payload)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload)


def _parse_time(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(UTC).replace(microsecond=0)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ModelError("--end-at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ModelError("--end-at must include a timezone")
    return parsed.astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.runtime_cost")
    sub = parser.add_subparsers(dest="command", required=True)

    capture = sub.add_parser("capture-prometheus", help="capture a read-only Python baseline")
    capture.add_argument("--targets", type=Path, required=True)
    capture.add_argument("--prometheus-url", required=True)
    capture.add_argument("--username-env", default="GRAFANA_PROM_USERNAME")
    capture.add_argument("--password-env", default="GRAFANA_PROM_PASSWORD")
    capture.add_argument("--window-seconds", type=int, default=86400)
    capture.add_argument("--end-at")
    capture.add_argument("--source-revision", required=True)
    capture.add_argument("--out", type=Path)

    project = sub.add_parser("project", help="size and price one runtime implementation")
    project.add_argument("--workload", type=Path, required=True)
    project.add_argument("--measurement", type=Path, required=True)
    project.add_argument("--pricing", type=Path, required=True)
    project.add_argument("--out", type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "capture-prometheus":
            username = os.environ.get(args.username_env)
            password = os.environ.get(args.password_env)
            client = PrometheusClient(args.prometheus_url, username, password)
            result = capture_prometheus_measurement(
                _load(args.targets, "capture-targets-v1.schema.json"),
                query=client.query,
                end_at=_parse_time(args.end_at),
                window_seconds=args.window_seconds,
                source_revision=args.source_revision,
            )
            _write(args.out, result)
            return 0
        result = project_runtime_cost(
            _load(args.workload, "workload-v1.schema.json"),
            _load(args.measurement, "measurement-v1.schema.json"),
            _load(args.pricing, "pricing-v1.schema.json"),
        )
        _write(args.out, result)
        return 0
    except ModelError as exc:
        print(f"runtime-cost: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
