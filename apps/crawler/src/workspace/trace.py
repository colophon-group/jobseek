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
# Match ws commands at start of line, after && or ;, or after alias assignment
_WS_CMD_RE = re.compile(
    r"(?:^|&&|;|\|)\s*(?:uv run )?ws\s+"
    r"(new|set|add|del|use|probe|select|run|feedback|submit|validate|task|reject|resume|status|help)\b"
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


def _parse_ts(ts_str: str) -> datetime | None:
    """Parse an ISO timestamp string, returning None on failure."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _timestamps_match(log_ts: str, transcript_ts: str, tolerance_s: float = 10.0) -> bool:
    """Check if two ISO timestamps are within tolerance of each other."""
    a = _parse_ts(log_ts)
    b = _parse_ts(transcript_ts)
    if not a or not b:
        return False
    return abs((a - b).total_seconds()) <= tolerance_s


def discover_transcript(slug: str) -> Path | None:
    """Find the Claude transcript for the most recent ws run of this slug.

    Strategy: match log.yaml timestamps against Bash tool_use timestamps
    in transcripts. The log records exact timestamps for each ws command;
    the transcript records timestamps for each tool call. Multiple matching
    timestamps confirm the correct transcript.

    Note: the ``ws task complete`` call itself may not be in the transcript
    yet (it's still executing), so we match against the second-to-last
    log entry (typically ``submit``) and earlier entries.
    """
    log_cmds = _log_ws_commands(slug)
    if not log_cmds:
        return None

    # Get timestamp range from log entries
    first_ts = log_cmds[0][0]
    last_ts = log_cmds[-1][0]
    first_dt = _parse_ts(first_ts)
    last_dt = _parse_ts(last_ts)
    if not first_dt or not last_dt:
        return None

    # Use the last N log timestamps for matching (skip 'complete' since
    # it may not be in the transcript yet — it's the currently executing call)
    match_entries = [(ts, cmd) for ts, cmd in log_cmds if cmd != "complete"]
    match_timestamps = [ts for ts, _ in match_entries[-6:]]

    # Scan all transcripts. Rough mtime filter with generous 24h padding.
    cutoff = first_dt - __import__("datetime").timedelta(hours=24)
    candidates: list[tuple[Path, float]] = []
    for t_path in _find_all_transcripts():
        try:
            mtime = datetime.fromtimestamp(t_path.stat().st_mtime, tz=UTC)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        candidates.append((t_path, abs((mtime - last_dt).total_seconds())))

    candidates.sort(key=lambda x: x[1])

    # Slug pattern for verification
    slug_re = re.compile(rf"\b{re.escape(slug)}\b")

    best_match: tuple[Path, int] | None = None

    for t_path, _ in candidates[:50]:
        # Extract ws commands from transcript tail (large tail for long sessions)
        tail = _tail_jsonl(t_path, 2000)
        tail_ws = _extract_ws_commands(tail)

        if not tail_ws:
            continue

        # Verify slug appears in ws commands OR in any text content.
        # Agents often omit the explicit slug (using active workspace),
        # so also search text output, tool results, etc.
        slug_found = any(slug_re.search(cmd) for _, cmd in tail_ws)
        if not slug_found:
            tail_text = " ".join(
                json.dumps(r.get("message", {}))
                for r in tail[-500:]  # last 500 records for speed
            )
            slug_found = bool(slug_re.search(tail_text))
        if not slug_found:
            continue

        # Count how many log timestamps match transcript ws timestamps
        transcript_timestamps = [ts for ts, _ in tail_ws]
        hits = sum(
            1
            for log_ts in match_timestamps
            if any(_timestamps_match(log_ts, t_ts) for t_ts in transcript_timestamps)
        )

        # Need at least 2 matching timestamps (or all if fewer)
        min_hits = min(2, len(match_timestamps))
        if hits >= min_hits and (best_match is None or hits > best_match[1]):
            best_match = (t_path, hits)

    if best_match:
        return best_match[0]

    # Fallback: mtime closest to complete timestamp + slug in any text.
    # For long sessions the tail may miss early ws commands.
    complete_ts = log_cmds[-1][0]
    complete_dt = _parse_ts(complete_ts)
    if complete_dt:
        for t_path, mtime_diff in candidates[:10]:
            if mtime_diff > 300:  # mtime within 5 min of complete
                continue
            tail = _tail_jsonl(t_path, 500)
            tail_text = " ".join(json.dumps(r.get("message", {})) for r in tail)
            if slug_re.search(tail_text):
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


def _clean_records(records: list[dict]) -> list[dict]:
    """Strip Claude Code session metadata from records to reduce noise.

    Removes the ``slug`` field (which contains the worktree name, not the
    company slug) and other session-level fields that are redundant across
    every record.
    """
    drop_keys = {"slug", "sessionId", "version", "cwd", "entrypoint", "promptId"}
    cleaned = []
    for rec in records:
        out = {k: v for k, v in rec.items() if k not in drop_keys}
        cleaned.append(out)
    return cleaned


def _build_trace(slug: str) -> tuple[dict, list[dict]] | None:
    """Discover transcript and build (header, records) for a slug.

    Returns None if no matching transcript found.
    """
    transcript_path = discover_transcript(slug)
    if not transcript_path:
        return None

    scoped = extract_scoped_trace(transcript_path, slug)
    if not scoped:
        return None

    from src.workspace.state import list_boards, load_workspace

    ws = load_workspace(slug)
    boards = list_boards(slug)
    board_slugs = [b.slug for b in boards] if boards else []

    header = {
        "_trace_header": True,
        "slug": slug,
        "company_name": ws.name or "",
        "board_slugs": board_slugs,
        "date": datetime.now(UTC).strftime("%Y-%m-%d"),
        "issue": ws.issue,
        "record_count": len(scoped),
    }

    return header, _clean_records(scoped)


def export_trace(slug: str, output_dir: Path) -> Path | None:
    """Discover, scope, and export trace to the single traces.jsonl file.

    Appends a header line + scoped records to ``{output_dir}/traces.jsonl``.
    Returns the output path, or None if no matching transcript found.
    """
    result = _build_trace(slug)
    if not result:
        return None

    header, scoped = result

    # Append to single traces.jsonl file
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "traces.jsonl"

    with open(out_path, "a") as f:
        f.write(json.dumps(header, default=str) + "\n")
        for rec in scoped:
            f.write(json.dumps(rec, default=str) + "\n")

    return out_path


_HF_REPO = "viktoroo/jobseek-agent-traces"


def upload_trace_to_hf(slug: str) -> str | None:
    """Discover transcript and upload trace to Hugging Face dataset.

    Uploads as ``traces/{slug}/{date}.jsonl`` to support multiple traces
    per company (e.g. reconfigurations).  If the same slug+date already
    exists, appends a numeric suffix (``-2``, ``-3``, …).

    Requires ``HF_TOKEN`` environment variable.
    Returns the HF URL, or None if no transcript found.
    """
    import os

    if not os.environ.get("HF_TOKEN"):
        raise RuntimeError("HF_TOKEN environment variable not set — cannot upload trace")

    result = _build_trace(slug)
    if not result:
        return None

    header, scoped = result

    import io

    buf = io.BytesIO()
    buf.write((json.dumps(header, default=str) + "\n").encode())
    for rec in scoped:
        buf.write((json.dumps(rec, default=str) + "\n").encode())
    buf.seek(0)

    from huggingface_hub import HfApi

    api = HfApi()
    date = header["date"]

    # Check for existing file and add suffix if needed
    base_path = f"traces/{slug}/{date}"
    path_in_repo = f"{base_path}.jsonl"
    try:
        existing = api.list_repo_tree(_HF_REPO, repo_type="dataset", path_in_repo=f"traces/{slug}")
        existing_names = {f.path for f in existing if hasattr(f, "path")}
        n = 2
        while path_in_repo in existing_names:
            path_in_repo = f"{base_path}-{n}.jsonl"
            n += 1
    except Exception:
        pass  # directory may not exist yet

    api.upload_file(
        path_or_fileobj=buf,
        path_in_repo=path_in_repo,
        repo_id=_HF_REPO,
        repo_type="dataset",
        commit_message=f"Add agent trace for {slug}",
    )
    return f"https://huggingface.co/datasets/{_HF_REPO}/blob/main/{path_in_repo}"
