"""Action log — append/read/format entries for transcript generation.

Each workspace has a log.yaml for workspace-level actions and each board
YAML has an embedded ``log`` list for board-level actions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def append(log_path: Path, cmd: str, ok: bool, msg: str) -> None:
    """Append an action entry to a YAML log file."""
    entries = read(log_path)
    entries.append({"ts": _now_iso(), "cmd": cmd, "ok": ok, "msg": msg})
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(yaml.dump(entries, default_flow_style=False, sort_keys=False))


def read(log_path: Path) -> list[dict[str, Any]]:
    """Read entries from a YAML log file. Returns empty list if missing."""
    if not log_path.exists():
        return []
    data = yaml.safe_load(log_path.read_text())
    return data if isinstance(data, list) else []


def append_to_list(entries: list[dict[str, Any]], cmd: str, ok: bool, msg: str) -> None:
    """Append an entry to an in-memory log list (for board YAMLs)."""
    entries.append({"ts": _now_iso(), "cmd": cmd, "ok": ok, "msg": msg})


def format_transcript(
    ws_log: list[dict[str, Any]],
    board_logs: dict[str, list[dict[str, Any]]],
) -> str:
    """Merge workspace and board logs chronologically into a numbered transcript.

    Returns the body of a ``<details>`` block (lines only, no wrapper).
    """
    all_entries: list[dict[str, Any]] = []
    for entry in ws_log:
        all_entries.append(entry)
    for _alias, entries in sorted(board_logs.items()):
        for entry in entries:
            all_entries.append(entry)

    # Sort by timestamp
    all_entries.sort(key=lambda e: e.get("ts", ""))

    lines: list[str] = []
    for i, entry in enumerate(all_entries, start=1):
        status = "\u2705" if entry.get("ok") else "\u274c"
        msg = entry.get("msg", "")
        cmd = entry.get("cmd", "")
        lines.append(f"{i}. {status} {cmd} \u2014 {msg}")

    return "\n".join(lines)


def _format_field_quality(value: Any) -> str:
    """Format a feedback field value for display.

    Handles both dict format (``{'coverage': '63/63', 'quality': 'clean'}``)
    and plain string format.
    """
    if isinstance(value, dict):
        cov = value.get("coverage", "")
        qual = value.get("quality", "")
        return f"{cov} ({qual})" if cov else qual
    return str(value)


def _get_active_cfg(board: dict[str, Any]) -> dict[str, Any]:
    """Extract the active config entry from a v2 board dict."""
    active = board.get("active_config")
    if not active:
        return {}
    return (board.get("configs") or {}).get(active, {})


def format_crawl_stats(boards: dict[str, dict[str, Any]]) -> str:
    """Generate crawl stats comment from board run data.

    Returns the full markdown comment including the hidden JSON marker,
    field coverage tiers, and verdict from feedback.

    Reads from the v2 board structure where run data, monitor/scraper
    type, cost, and feedback are inside ``configs[active_config]``.
    """
    import json

    total_jobs = 0
    max_monitor_time = 0.0
    max_scraper_time = 0.0
    monitor_type = None
    scraper_type = None
    configs_tried = 0
    verdict = None
    feedback_fields: dict[str, Any] = {}

    for _alias, board in boards.items():
        cfg = _get_active_cfg(board)
        mr = cfg.get("run") or {}
        sr = cfg.get("scraper_run") or {}
        total_jobs += mr.get("jobs", 0)
        max_monitor_time = max(max_monitor_time, mr.get("time", 0.0))
        max_scraper_time = max(max_scraper_time, sr.get("avg_time", 0.0))
        monitor_type = cfg.get("monitor_type") or monitor_type
        scraper_type = cfg.get("scraper_type") or scraper_type

        # Count configs tried
        configs_tried += len(board.get("configs") or {})

        # Get feedback from active config
        fb = cfg.get("feedback")
        if fb:
            verdict = fb.get("verdict")
            feedback_fields = fb.get("fields", {})

    # Cost from active config
    cost_str = None
    for _alias, board in boards.items():
        cfg = _get_active_cfg(board)
        cost = cfg.get("cost", {})
        mon = cost.get("monitor_per_cycle")
        if mon is not None:
            from src.core.monitors import is_rich_monitor

            m_type = cfg.get("monitor_type")
            m_config = cfg.get("monitor_config")
            if is_rich_monitor(m_type, m_config):
                cost_str = f"~{mon}s (API — no scraper)"
            else:
                cost_str = f"~{mon}s/cycle + scraper"

    stats = {
        "jobs": total_jobs,
        "monitor_time": max_monitor_time,
        "scraper_time": max_scraper_time,
    }
    stats_json = json.dumps({k: v for k, v in stats.items() if v is not None})

    rows = [
        f"| Jobs | {total_jobs} |",
        f"| Monitor | `{monitor_type}` · {max_monitor_time}s (measured) |",
    ]
    if scraper_type:
        rows.append(f"| Scraper | `{scraper_type}` · {max_scraper_time}s |")
    if cost_str:
        rows.append(f"| Cost/cycle | {cost_str} |")
    if configs_tried > 1:
        rows.append(f"| Configs tried | {configs_tried} |")
    if verdict:
        rows.append(f"| Verdict | **{verdict}** |")

    table = "\n".join(rows)
    return f"<!-- crawl-stats {stats_json} -->\n| Metric | Value |\n|---|---|\n{table}"
