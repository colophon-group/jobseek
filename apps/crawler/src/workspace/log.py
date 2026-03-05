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
    """Generate crawl stats comment with per-board rows.

    Returns the full markdown comment including the hidden JSON marker.
    Each board gets its own row; a Total row is appended for multi-board.

    The ``<!-- crawl-stats {json} -->`` marker contains ``jobs`` (int)
    and ``monitor_time`` (float, sum across boards) for CI label-pr.sh.
    """
    import json

    total_jobs = 0
    total_monitor_time = 0.0
    rows: list[str] = []

    for alias, board in boards.items():
        cfg = _get_active_cfg(board)
        mr = cfg.get("run") or {}
        jobs = mr.get("jobs", 0)
        mon_time = mr.get("time", 0.0)
        total_jobs += jobs
        total_monitor_time += mon_time

        mtype = cfg.get("monitor_type", "?")

        # Cost
        cost = (cfg.get("cost") or {}).get("monitor_per_cycle")
        cost_str = f"~{cost}s" if cost is not None else "—"

        # Verdict
        fb = cfg.get("feedback") or {}
        verdict = fb.get("verdict")
        verdict_str = f"**{verdict}**" if verdict else "—"

        slug = board.get("slug", alias)
        rows.append(f"| {slug} | `{mtype}` | {jobs} | {cost_str} | {verdict_str} |")

    # Total row for multi-board
    if len(boards) > 1:
        rows.append(f"| **Total** | | **{total_jobs}** | | |")

    stats_json = json.dumps({"jobs": total_jobs, "monitor_time": total_monitor_time})

    header = "| Board | Monitor | Jobs | Cost | Verdict |\n|---|---|---|---|---|"
    table = "\n".join(rows)
    return f"<!-- crawl-stats {stats_json} -->\n{header}\n{table}"
