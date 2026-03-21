"""Trace capture — discover, scope, and export Claude Code transcripts.

Finds the transcript for a ws workspace run by correlating ws command
timestamps from log.yaml with Bash tool_use records in the transcript.
Exports a scoped, flattened JSONL with main + subagent records merged.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from src.workspace import log as action_log
from src.workspace.state import ws_log_path

_CLAUDE_DIR = Path.home() / ".claude" / "projects"
_WS_CMD_RE = re.compile(
    r"\bws\s+(new|set|add|del|use|probe|select|run|feedback|submit|validate|task|reject|resume|status|help)\b"
)


def _find_all_transcripts() -> list[Path]:
    """Find all JSONL transcript files across all Claude project dirs."""
    if not _CLAUDE_DIR.exists():
        return []
    transcripts = []
    for project_dir in _CLAUDE_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for f in project_dir.iterdir():
            if f.suffix == ".jsonl" and f.is_file():
                transcripts.append(f)
    return transcripts


def _read_jsonl(path: Path, max_lines: int = 0) -> list[dict]:
    """Read JSONL file, optionally limiting to first N lines."""
    records = []
    with open(path) as f:
        for i, line in enumerate(f):
            if max_lines and i >= max_lines:
                break
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _tail_jsonl(path: Path, n: int = 200) -> list[dict]:
    """Read the last N lines of a JSONL file."""
    lines: list[str] = []
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        # Read chunks from the end
        chunk_size = min(size, n * 2000)  # estimate ~2KB per line
        f.seek(max(0, size - chunk_size))
        data = f.read().decode("utf-8", errors="replace")
        lines = data.strip().split("\n")
        lines = lines[-n:]
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def _extract_ws_commands(records: list[dict]) -> list[tuple[str, str]]:
    """Extract (timestamp, ws_command_string) from Bash tool_use records."""
    commands = []
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message", {})
        for content in msg.get("content", []):
            if content.get("type") == "tool_use" and content.get("name") == "Bash":
                cmd = content.get("input", {}).get("command", "")
                if _WS_CMD_RE.search(cmd):
                    ts = rec.get("timestamp", "")
                    commands.append((ts, cmd))
    return commands


def _log_ws_commands(slug: str) -> list[tuple[str, str]]:
    """Extract (timestamp, command_name) from workspace log.yaml."""
    log_path = ws_log_path(slug)
    entries = action_log.read(log_path)
    return [(e.get("ts", ""), e.get("cmd", "")) for e in entries]


def _flatten_transcript(main_path: Path) -> list[dict]:
    """Merge main transcript with all subagent transcripts, sorted by timestamp."""
    records = _read_jsonl(main_path)

    # Find subagent dir
    session_id = main_path.stem
    subagent_dir = main_path.parent / session_id / "subagents"
    if subagent_dir.is_dir():
        for f in sorted(subagent_dir.iterdir()):
            if f.suffix == ".jsonl":
                agent_id = f.stem.removeprefix("agent-")
                # Read agent type from meta
                meta_path = f.with_suffix(".meta.json")
                agent_type = ""
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                        agent_type = meta.get("agentType", "")
                    except (json.JSONDecodeError, OSError):
                        pass
                sub_records = _read_jsonl(f)
                for rec in sub_records:
                    rec["_scope"] = f"subagent:{agent_id}"
                    if agent_type:
                        rec["_agentType"] = agent_type
                records.extend(sub_records)

    # Add _scope to main records that don't have it
    for rec in records:
        if "_scope" not in rec:
            rec["_scope"] = "main"

    # Sort by timestamp (records without timestamp go to the start)
    records.sort(key=lambda r: r.get("timestamp", ""))
    return records


def _is_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """Check if needle command names appear as a subsequence in haystack."""
    it = iter(haystack)
    return all(cmd in it for cmd in needle)


def _extract_cmd_name(ws_cmd_str: str) -> str:
    """Extract the ws subcommand name from a full command string."""
    m = _WS_CMD_RE.search(ws_cmd_str)
    return m.group(1) if m else ""


def discover_transcript(slug: str) -> Path | None:
    """Find the Claude transcript for the most recent ws run of this slug.

    Uses timestamp correlation and command sequence matching between
    log.yaml and transcript Bash tool_use records.
    """
    log_cmds = _log_ws_commands(slug)
    if not log_cmds:
        return None

    # Get the complete_ts from log (last entry should be 'complete')
    complete_ts = log_cmds[-1][0] if log_cmds else ""
    if not complete_ts:
        return None

    try:
        complete_dt = datetime.fromisoformat(complete_ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None

    # Take last N command names from log for sequence matching
    last_n_cmds = [cmd for _, cmd in log_cmds[-8:]]

    # Scan all transcripts, rough-filter by mtime
    candidates: list[tuple[Path, float]] = []
    for t_path in _find_all_transcripts():
        try:
            mtime = datetime.fromtimestamp(t_path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if abs((mtime - complete_dt).total_seconds()) > 120:
            continue
        candidates.append((t_path, abs((mtime - complete_dt).total_seconds())))

    # Sort by closeness to complete timestamp
    candidates.sort(key=lambda x: x[1])

    for t_path, _ in candidates:
        # Flatten main + subagents and extract ws commands
        flat = _flatten_transcript(t_path)
        transcript_ws = _extract_ws_commands(flat)
        transcript_cmd_names = [_extract_cmd_name(cmd) for _, cmd in transcript_ws]

        # Check if last N log commands appear as subsequence
        if _is_subsequence(last_n_cmds, transcript_cmd_names):
            return t_path

    return None


def extract_scoped_trace(transcript_path: Path, slug: str) -> list[dict]:
    """Parse transcript, trim to ws work scope, merge subagent records.

    Scoping: include only records from the first ws-related command
    onward. This excludes personal conversation before ws work.
    """
    flat = _flatten_transcript(transcript_path)

    # Find first ws-related record
    first_ws_ts = None
    for rec in flat:
        if rec.get("type") != "assistant":
            continue
        msg = rec.get("message", {})
        for content in msg.get("content", []):
            if content.get("type") == "tool_use" and content.get("name") == "Bash":
                cmd = content.get("input", {}).get("command", "")
                if _WS_CMD_RE.search(cmd):
                    first_ws_ts = rec.get("timestamp", "")
                    break
        if first_ws_ts:
            break

    if not first_ws_ts:
        return flat  # No ws commands found, return everything

    # Also find the user prompt that preceded the first ws command
    # by looking for the parentUuid chain
    first_ws_uuid = None
    for rec in flat:
        if rec.get("timestamp") == first_ws_ts and rec.get("type") == "assistant":
            first_ws_uuid = rec.get("parentUuid")
            break

    # Find the earliest relevant timestamp
    scope_start = first_ws_ts
    if first_ws_uuid:
        for rec in flat:
            if rec.get("uuid") == first_ws_uuid:
                ts = rec.get("timestamp", "")
                if ts and ts < scope_start:
                    scope_start = ts
                break

    # Filter to scoped records
    return [r for r in flat if r.get("timestamp", "") >= scope_start or not r.get("timestamp")]


def export_trace(slug: str, output_dir: Path) -> Path | None:
    """Discover, scope, and export trace for a company slug.

    Returns the output path, or None if no matching transcript found.
    """
    transcript_path = discover_transcript(slug)
    if not transcript_path:
        return None

    scoped = extract_scoped_trace(transcript_path, slug)
    if not scoped:
        return None

    # Write output
    ts = datetime.now(UTC).strftime("%Y%m%d")
    slug_dir = output_dir / slug
    slug_dir.mkdir(parents=True, exist_ok=True)
    out_path = slug_dir / f"{ts}.jsonl"

    # Avoid overwriting — add suffix if exists
    counter = 1
    while out_path.exists():
        counter += 1
        out_path = slug_dir / f"{ts}-{counter}.jsonl"

    with open(out_path, "w") as f:
        for rec in scoped:
            f.write(json.dumps(rec, default=str) + "\n")

    return out_path
