"""Artifact storage — save and list debug artifacts from monitor/scraper runs."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from src.workspace.state import artifacts_dir


def _run_dir(slug: str, alias: str, category: str) -> Path:
    """Create and return a timestamped run directory."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    d = artifacts_dir(slug, alias) / category / f"run-{ts}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def monitor_run_dir(slug: str, alias: str) -> Path:
    """Create and return a timestamped monitor run directory."""
    return _run_dir(slug, alias, "monitor")


def scraper_run_dir(slug: str, alias: str) -> Path:
    """Create and return a timestamped scraper run directory."""
    return _run_dir(slug, alias, "scraper")


def save_jobs(run_dir: Path, jobs: list[dict]) -> None:
    """Save processed job data to a run directory."""
    (run_dir / "jobs.json").write_text(json.dumps(jobs, indent=2, default=str))


def probe_run_dir(slug: str, alias: str) -> Path:
    """Create and return a timestamped probe run directory."""
    return _run_dir(slug, alias, "probe")


def scraper_probe_run_dir(slug: str, alias: str) -> Path:
    """Create and return a timestamped scraper-probe run directory."""
    return _run_dir(slug, alias, "scraper-probe")


def deep_probe_run_dir(slug: str, alias: str) -> Path:
    """Create and return a timestamped deep-probe run directory."""
    return _run_dir(slug, alias, "deep-probe")


def api_probe_run_dir(slug: str, alias: str) -> Path:
    """Create and return a timestamped api-probe run directory."""
    return _run_dir(slug, alias, "api-probe")


def save_probe(run_dir: Path, results: list[dict]) -> None:
    """Save probe detection results to a run directory."""
    (run_dir / "probe.json").write_text(json.dumps(results, indent=2, default=str))


def save_quality(run_dir: Path, quality: dict) -> None:
    """Save quality report to a run directory."""
    (run_dir / "quality.json").write_text(json.dumps(quality, indent=2, default=str))


def save_http_log(run_dir: Path, entries: list[dict]) -> None:
    """Save HTTP request/response log to a run directory."""
    if entries:
        (run_dir / "http_log.json").write_text(json.dumps(entries, indent=2, default=str))


def save_events(run_dir: Path, events: list[dict]) -> None:
    """Save captured structlog events to a run directory."""
    if events:
        lines = [json.dumps(e, default=str) for e in events]
        (run_dir / "events.jsonl").write_text("\n".join(lines) + "\n")


def save_results(run_dir: Path, results: list[dict]) -> None:
    """Save extracted scraper results to a run directory."""
    for result in results:
        job_id = result.get("id", "unknown")
        (run_dir / f"{job_id}.json").write_text(json.dumps(result, indent=2, default=str))


def capture_structlog() -> list[dict[str, Any]]:
    """Configure structlog to capture events to a list.

    Returns the capture list. Each run command should call this once
    before the async run, then save the list via ``save_events()``.

    Safe for CLI usage where each invocation is a fresh process.
    """
    events: list[dict[str, Any]] = []

    def _capture(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        events.append(dict(event_dict))
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _capture,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    return events
