"""Qualify existing workspace captures for isolated, network-free detail replay.

This does not collect new pages or qualify monitor/browser workload. Raw HTML
and extracted JSON are separate artifacts. The saved HTML is the workspace's
decoded response, not original compressed wire bytes. Configuration is the
current recorded configuration, bound by run time and reproduced output; the
workspace does not retain an immutable original configuration snapshot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from admission.native_lane.workload import canonical

MAX_HTML = 8 * 1024 * 1024
MAX_CORPUS = 128 * 1024 * 1024


class CaptureRejected(ValueError):
    """A fixed reason code; never include source paths, URLs or page content."""


def bound_config(board: dict[str, Any], run_name: str) -> tuple[str, dict[str, Any]]:
    try:
        started = datetime.strptime(run_name, "run-%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        raise CaptureRejected("run_timestamp") from None
    matches = []
    for config in board.get("configs", {}).values():
        stamp = (config.get("scraper_run") or {}).get("ran_at")
        try:
            finished = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            elapsed = (finished - started).total_seconds()
        except (ValueError, TypeError):
            continue
        if 0 <= elapsed < 3600:
            matches.append(config)
    if len(matches) != 1:
        raise CaptureRejected("config_run_binding")
    selected = matches[0]
    kind = selected.get("scraper_type")
    config = selected.get("scraper_config") or {}
    if kind not in ("dom", "json-ld"):
        raise CaptureRejected("unsupported_scraper")
    if config.get("render"):
        raise CaptureRejected("browser_dependencies_not_captured")
    # Direct replay must not silently drop actions, linked requests or transport
    # behavior. These two complete parse contracts require only this response.
    if set(config) - {"steps", "render"} or (kind == "json-ld" and config):
        raise CaptureRejected("additional_configuration")
    return kind, config


def qualify(
    body: bytes,
    expected: dict[str, Any],
    requests: list[dict[str, Any]],
    kind: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    if not 0 < len(body) <= MAX_HTML:
        raise CaptureRejected("response_size")
    try:
        html = body.decode("utf-8")
    except UnicodeError:
        raise CaptureRejected("response_encoding") from None
    if not re.search(r"<!doctype\s+html\b|<html\b", html, re.I):
        raise CaptureRejected("not_full_response_document")
    url = expected.get("url")
    if not isinstance(url, str):
        raise CaptureRejected("source_url")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise CaptureRejected("source_url")
    if not any(
        entry.get("url") == url
        and entry.get("method") == "GET"
        and entry.get("status") == 200
        and "text/html" in str(entry.get("content_type", "")).lower()
        for entry in requests
    ):
        raise CaptureRejected("successful_source_request_missing")
    if kind == "json-ld":
        from src.core.jsonld import parse_html
    elif kind == "dom":
        from src.core.scrapers.dom import parse_html
        from src.runtime import dom_go_parse

        if dom_go_parse.enabled():
            raise CaptureRejected("python_oracle_not_selected")
    else:
        raise CaptureRejected("unsupported_scraper")
    from src.core.job_content import enrich_description

    try:
        content = parse_html(html, config)
        enrich_description(content)
        actual = asdict(content)
    except Exception:
        raise CaptureRejected("offline_parser_failed") from None
    if not actual["title"] or not actual["description"]:
        raise CaptureRejected("incomplete_saved_detail")
    if any(expected.get(key) != value for key, value in actual.items()):
        raise CaptureRejected("recorded_output_mismatch")
    return {
        "source_url": url,
        "scraper_type": kind,
        "scraper_config": config,
        "html_sha256": hashlib.sha256(body).hexdigest(),
        "html_bytes": len(body),
        "expected": actual,
    }


def prepare(workspace: Path, destination: Path) -> dict[str, Any]:
    workspace = workspace.resolve(strict=True)
    # A fresh directory prevents overwriting another task's inputs or changing
    # the bytes under an in-progress comparison. No source artifact is edited.
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    counts: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    records = []
    seen = set()
    total_bytes = 0
    for page in sorted(workspace.rglob("*.html")):
        sidecar = page.with_suffix(".json")
        if not sidecar.is_file():
            counts["unpaired_html"] += 1
            continue
        counts["paired_html"] += 1
        try:
            for source in (page, sidecar, page.parent / "http_log.json"):
                if not source.resolve().is_relative_to(workspace):
                    raise CaptureRejected("source_escape")
            relative = page.relative_to(workspace).parts
            if len(relative) < 6 or relative[1] != "artifacts" or relative[3] != "scraper":
                raise CaptureRejected("artifact_layout")
            board_file = workspace / relative[0] / "boards" / (relative[2] + ".yaml")
            if not board_file.resolve().is_relative_to(workspace):
                raise CaptureRejected("source_escape")
            if not board_file.is_file():
                raise CaptureRejected("board_record_missing")
            board = yaml.safe_load(board_file.read_text())
            kind, config = bound_config(board, page.parent.name)
            if page.stat().st_size > MAX_HTML:
                raise CaptureRejected("response_size")
            body = page.read_bytes()
            record = qualify(
                body,
                json.loads(sidecar.read_text()),
                json.loads((page.parent / "http_log.json").read_text()),
                kind,
                config,
            )
            fingerprint = hashlib.sha256(canonical(record)).hexdigest()
            if fingerprint in seen:
                counts["duplicate_input"] += 1
                continue
            if total_bytes + len(body) > MAX_CORPUS:
                raise CaptureRejected("corpus_size")
            seen.add(fingerprint)
            filename = f"{len(records):04d}.html"
            target = destination / filename
            with target.open("xb") as output:
                target.chmod(0o600)
                output.write(body)
            record["html_file"] = filename
            records.append(record)
            total_bytes += len(body)
            kinds[kind] += 1
            counts["qualified"] += 1
        except CaptureRejected as error:
            counts[str(error)] += 1
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            counts["invalid_artifact"] += 1
    manifest = {
        "schema": "jobseek.captured-direct-inputs/v1",
        "scope": "saved decoded HTTP detail responses; no monitor or browser capture claim",
        "configuration_provenance": (
            "current record with run-time binding and exact saved-output reproduction"
        ),
        "cases": records,
    }
    body = canonical(manifest)
    target = destination / "manifest.json"
    with target.open("xb") as output:
        target.chmod(0o600)
        output.write(body)
    return {
        "schema": manifest["schema"],
        "counts": dict(sorted(counts.items())),
        "qualified_types": dict(sorted(kinds.items())),
        "qualified_html_bytes": total_bytes,
        "manifest_sha256": hashlib.sha256(body).hexdigest(),
        "scope": manifest["scope"],
        "configuration_provenance": manifest["configuration_provenance"],
        "network_requests": 0,
        "whole_lane_measurement": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.workspace, args.destination), sort_keys=True))


if __name__ == "__main__":
    main()
