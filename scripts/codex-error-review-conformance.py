#!/usr/bin/env python3
"""Verify the unprivileged error-review evidence boundary inside systemd."""

from __future__ import annotations

import argparse
import grp
import json
import os
import re
from pathlib import Path

FORBIDDEN_PATHS = (
    Path("/home/deploy/.env"),
    Path("/run/docker.sock"),
    Path("/var/run/docker.sock"),
    Path("/run/containerd/containerd.sock"),
)
SECRET_SHAPES = re.compile(
    r"(?i)(authorization\s*[:=]|bearer\s+|basic\s+[A-Za-z0-9+/=]{16,}|"
    r"(?:token|password|secret|api[_-]?key)\s*[:=]\s*(?!<redacted>))"
)


class ConformanceError(RuntimeError):
    """The systemd service boundary differs from the committed contract."""


def verify_boundary(bundle: Path, credential: Path) -> None:
    if os.geteuid() == 0:
        raise ConformanceError("conformance check must run as the service user")
    group_names = {grp.getgrgid(gid).gr_name for gid in os.getgroups()}
    if "docker" in group_names:
        raise ConformanceError("service user unexpectedly belongs to docker group")
    if os.access(credential, os.R_OK):
        raise ConformanceError("service user can read the root-only metrics credential")
    for path in FORBIDDEN_PATHS:
        if os.access(path, os.R_OK) or os.access(path, os.W_OK):
            raise ConformanceError(f"service user can access forbidden path {path}")

    manifest_path = bundle / "manifest.json"
    metrics_path = bundle / "metrics" / "historical-prometheus.json"
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
        metrics_text = metrics_path.read_text(encoding="utf-8")
        manifest = json.loads(manifest_text)
        metrics = json.loads(metrics_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformanceError("normalized evidence is not readable valid JSON") from exc

    if SECRET_SHAPES.search(manifest_text) or SECRET_SHAPES.search(metrics_text):
        raise ConformanceError("normalized evidence contains a credential-shaped value")
    summary = manifest.get("historical_metrics")
    if not isinstance(summary, dict) or summary.get("query_count") != len(
        metrics.get("queries", [])
    ):
        raise ConformanceError("manifest and historical metrics evidence disagree")
    if metrics.get("schema_version") != 1 or not isinstance(metrics.get("required_complete"), bool):
        raise ConformanceError("historical metrics evidence lacks conformance metadata")
    if not metrics.get("queries"):
        raise ConformanceError("historical metrics evidence contains no allowlisted queries")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bundle",
        type=Path,
        default=Path("/srv/jobseek-codex/inputs/error-review/latest"),
    )
    parser.add_argument(
        "--credential",
        type=Path,
        default=Path("/etc/jobseek-codex/error-review-metrics.env"),
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    verify_boundary(args.bundle, args.credential)
    print("error-review evidence boundary conforms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
