"""Action log — append/read/format entries for transcript generation.

Each workspace has a log.yaml for workspace-level actions and each board
YAML has an embedded ``log`` list for board-level actions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def format_crawl_stats(boards: dict[str, dict[str, Any]]) -> str:
    """Generate crawl stats comment from board run data.

    Returns the full markdown comment including the hidden JSON marker.
    """
    import json

    total_jobs = 0
    max_monitor_time = 0.0
    max_scraper_time = 0.0
    monitor_type = None
    scraper_type = None
    total_titles = None
    total_descs = None
    total_locations = None
    sample_count = None

    for _alias, board in boards.items():
        mr = board.get("monitor_run") or {}
        sr = board.get("scraper_run") or {}
        total_jobs += mr.get("jobs", 0)
        max_monitor_time = max(max_monitor_time, mr.get("time", 0.0))
        max_scraper_time = max(max_scraper_time, sr.get("avg_time", 0.0))
        monitor_type = board.get("monitor_type") or monitor_type
        scraper_type = board.get("scraper_type") or scraper_type

        if sr.get("titles") is not None:
            total_titles = (total_titles or 0) + sr["titles"]
            sample_count = (sample_count or 0) + sr.get("count", 0)
        if sr.get("descriptions") is not None:
            total_descs = (total_descs or 0) + sr["descriptions"]
        if sr.get("locations") is not None:
            total_locations = (total_locations or 0) + sr["locations"]

    stats = {
        "jobs": total_jobs,
        "monitor_time": max_monitor_time,
        "scraper_time": max_scraper_time,
        "monitor_type": monitor_type,
        "scraper_type": scraper_type,
        "titles": total_titles,
        "descriptions": total_descs,
        "locations": total_locations,
    }
    stats_json = json.dumps({k: v for k, v in stats.items() if v is not None})

    rows = [
        f"| Jobs | {total_jobs} |",
        f"| Monitor | `{monitor_type}` · {max_monitor_time}s |",
    ]
    if scraper_type:
        rows.append(f"| Scraper | `{scraper_type}` · {max_scraper_time}s |")
    if total_titles is not None and sample_count:
        rows.append(f"| Titles | {total_titles}/{sample_count} |")
    if total_descs is not None and sample_count:
        rows.append(f"| Descriptions | {total_descs}/{sample_count} |")
    if total_locations is not None and sample_count:
        rows.append(f"| Locations | {total_locations}/{sample_count} |")

    table = "\n".join(rows)
    return (
        f"<!-- crawl-stats {stats_json} -->\n"
        f"| Metric | Value |\n"
        f"|---|---|\n"
        f"{table}"
    )
