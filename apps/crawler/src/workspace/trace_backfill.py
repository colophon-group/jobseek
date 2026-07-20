"""Quality-gated export of Codex resolver root and subagent sessions."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.workspace.trace import detect_credentials

SCHEMA_VERSION = "jobseek-codex-training-bundle/v2"
DEFAULT_HF_REPO = "viktoroo/jobseek-agent-traces"
DEFAULT_HF_PREFIX = "training-bundles/v2"
_WORKTREE_RE = re.compile(r"/srv/jobseek-codex/worktrees/company-request-[^/\s\"']+")
_DOCUMENTATION_URL_CREDENTIAL_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*)://user:pass@", re.I)
_FERNET_RE = re.compile(r"^gAAAAA[A-Za-z0-9_-]{40,}={0,2}$")
_TRACK_RE = re.compile(r"<track-([abc])>\s*(.*?)\s*</track-\1>", re.I | re.S)
_RUN_ID_FROM_CWD_RE = re.compile(r"company-request-\d+-(issue-\d+-\d+-[A-Za-z0-9]+)(?:/|$)")
_DROP_TOP_LEVEL_TYPES = {"turn_context", "world_state"}
_DROP_PAYLOAD_TYPES = {"reasoning", "token_count"}
_DUPLICATE_EVENT_TYPES = {"agent_message", "user_message"}
_MAX_JSONL_RECOVERY_LINES = 200
_MAX_JSONL_RECOVERY_BYTES = 2 * 1024 * 1024
_EXPORTABLE_STATES = {
    "completed",
    "failed",
    "timeout",
    "submitted",
    "rejected",
    "escalated",
    "retryable",
    "interrupted",
}


@dataclass(frozen=True)
class SessionSource:
    path: Path
    metadata: dict[str, Any]

    @property
    def thread_id(self) -> str:
        value = self.metadata.get("id") or self.metadata.get("session_id")
        return str(value or self.path.stem)

    @property
    def parent_thread_id(self) -> str | None:
        value = self.metadata.get("parent_thread_id")
        return str(value) if value else None

    @property
    def is_root(self) -> bool:
        return self.metadata.get("source") == "exec"

    @property
    def role(self) -> str:
        if self.is_root:
            return "main"
        value = self.metadata.get("agent_path")
        return Path(value).name if isinstance(value, str) and value else "subagent"


@dataclass
class ThreadProjection:
    source: SessionSource
    lines: list[dict[str, Any]]
    task_contract: str | None
    invalid_source_lines: int = 0
    recovered_source_records: int = 0
    dropped_reasoning_records: int = 0
    dropped_context_records: int = 0
    removed_encrypted_fields: int = 0
    unresolved_encrypted_calls: int = 0
    assistant_messages: int = 0
    user_messages: int = 0
    final_answers: int = 0
    tool_calls: set[str] = field(default_factory=set)
    tool_outputs: set[str] = field(default_factory=set)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_sha256(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int, int]:
    """Read JSONL, repairing legacy records split by raw newlines in strings.

    Older Codex builds occasionally emitted a literal newline inside a JSON
    string.  The next physical line is then a continuation of the same record,
    not a separate JSONL record.  Join only when the combined payload parses
    as exactly one JSON object; arbitrary malformed input remains invalid and
    is quarantined by the caller.
    """
    records: list[dict[str, Any]] = []
    invalid = 0
    recovered = 0
    lines = path.read_text(errors="replace").splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            candidate = line
            restored = False
            scan = index + 1
            while (
                scan < min(len(lines), index + _MAX_JSONL_RECOVERY_LINES)
                and len(candidate) < _MAX_JSONL_RECOVERY_BYTES
            ):
                candidate += "\\n" + lines[scan]
                try:
                    value = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    # A following complete record produced trailing data, so
                    # the original fragment cannot be repaired by joining.
                    if exc.msg == "Extra data":
                        break
                    scan += 1
                    continue
                if isinstance(value, dict):
                    records.append(value)
                    recovered += 1
                    index = scan + 1
                    restored = True
                break
            if not restored:
                invalid += 1
                index += 1
            continue
        if isinstance(value, dict):
            records.append(value)
        else:
            invalid += 1
        index += 1
    return records, invalid, recovered


def _text_values(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from _text_values(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _text_values(child)


def _normalize_string(value: str) -> str:
    normalized = _WORKTREE_RE.sub("<WORKTREE>", value)
    return _DOCUMENTATION_URL_CREDENTIAL_RE.sub(
        r"\g<scheme>://[REDACTED_URL_CREDENTIAL]@", normalized
    )


def _normalize_value(value: Any) -> Any:
    if isinstance(value, str):
        return _normalize_string(value)
    if isinstance(value, list):
        return [_normalize_value(child) for child in value]
    if isinstance(value, dict):
        return {key: _normalize_value(child) for key, child in value.items()}
    return value


def _safe_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {str(exc)[:1000]}"
    if detect_credentials(message):
        return f"{type(exc).__name__}: details redacted by credential scanner"
    return message


def _visible_message_text(payload: dict[str, Any]) -> str:
    pieces = []
    for content in payload.get("content") or []:
        if not isinstance(content, dict):
            continue
        for key in ("text", "input_text", "output_text"):
            value = content.get(key)
            if isinstance(value, str):
                pieces.append(value)
    return "\n".join(pieces)


def _is_injected_user_context(payload: dict[str, Any]) -> bool:
    if payload.get("type") != "message" or payload.get("role") != "user":
        return False
    text = _visible_message_text(payload).lstrip()
    return text.startswith(
        (
            "# AGENTS.md instructions for ",
            "<recommended_plugins>",
            "<permissions instructions>",
            "<app-context>",
            "<skills_instructions>",
            "<apps_instructions>",
            "<plugins_instructions>",
            "<environment_context>",
        )
    )


def _is_static_harness_context(payload: dict[str, Any]) -> bool:
    return payload.get("type") == "message" and payload.get("role") == "developer"


def _strip_encrypted(value: Any) -> tuple[Any, int]:
    removed = 0
    if isinstance(value, list):
        output = []
        for child in value:
            if isinstance(child, dict) and child.get("type") == "encrypted_content":
                removed += 1
                continue
            cleaned, count = _strip_encrypted(child)
            removed += count
            output.append(cleaned)
        return output, removed
    if isinstance(value, dict):
        output = {}
        for key, child in value.items():
            if key == "encrypted_content":
                removed += 1
                continue
            cleaned, count = _strip_encrypted(child)
            removed += count
            output[key] = cleaned
        return output, removed
    return value, removed


def _extract_contracts(root_records: list[dict[str, Any]]) -> dict[str, str]:
    contracts: dict[str, str] = {}
    for record in root_records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") not in {"function_call_output", "custom_tool_call_output"}:
            continue
        for text in _text_values(payload):
            for track, body in _TRACK_RE.findall(text):
                contracts[track.lower()] = _normalize_string(body.strip())
    return contracts


def _contract_for_role(role: str, contracts: dict[str, str]) -> str | None:
    normalized = role.lower().replace("-", "_")
    if any(token in normalized for token in ("enrich", "metadata", "track_a")):
        return contracts.get("a")
    if any(token in normalized for token in ("logo", "track_b")):
        return contracts.get("b")
    if any(token in normalized for token in ("board", "track_c")):
        return contracts.get("c")
    return None


def _parse_arguments(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = payload.get("arguments")
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _replace_arguments(payload: dict[str, Any], arguments: dict[str, Any]) -> None:
    if isinstance(payload.get("arguments"), str):
        payload["arguments"] = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    else:
        payload["arguments"] = arguments


def _contract_for_task_name(task_name: str, contracts: dict[str, str]) -> str | None:
    return _contract_for_role(task_name, contracts)


def _sanitize_payload(
    payload: dict[str, Any], contracts: dict[str, str]
) -> tuple[dict[str, Any] | None, int, int]:
    payload_type = payload.get("type")
    if payload_type in _DROP_PAYLOAD_TYPES:
        return None, 0, 0
    if payload_type in _DUPLICATE_EVENT_TYPES:
        return None, 0, 0
    if _is_injected_user_context(payload) or _is_static_harness_context(payload):
        return None, 0, 0

    cleaned, removed = _strip_encrypted(copy.deepcopy(payload))
    assert isinstance(cleaned, dict)
    unresolved = 0

    if cleaned.get("type") in {"function_call", "custom_tool_call"}:
        name = str(cleaned.get("name") or cleaned.get("tool") or "")
        arguments = _parse_arguments(payload)
        if arguments is not None:
            task_name = str(arguments.get("task_name") or "")
            message = arguments.get("message")
            if isinstance(message, str) and _FERNET_RE.fullmatch(message):
                if name == "spawn_agent":
                    contract = _contract_for_task_name(task_name, contracts)
                    if contract:
                        arguments["message"] = contract
                    else:
                        arguments["message"] = "<UNRESOLVED_ENCRYPTED_TASK>"
                        unresolved += 1
                else:
                    arguments["message"] = "<UNRESOLVED_ENCRYPTED_MESSAGE>"
                    unresolved += 1
            _replace_arguments(cleaned, arguments)

    if cleaned.get("type") == "message":
        content = cleaned.get("content")
        if isinstance(content, list) and not content:
            return None, removed, unresolved

    return _normalize_value(cleaned), removed, unresolved


def project_thread(
    source: SessionSource,
    *,
    contracts: dict[str, str],
    task_contract: str | None,
) -> ThreadProjection:
    records, invalid, recovered = _read_jsonl(source.path)
    projection = ThreadProjection(
        source=source,
        lines=[],
        task_contract=task_contract,
        invalid_source_lines=invalid,
        recovered_source_records=recovered,
    )
    for sequence, record in enumerate(records):
        top_type = record.get("type")
        if top_type == "session_meta":
            projection.dropped_context_records += 1
            continue
        if top_type in _DROP_TOP_LEVEL_TYPES:
            projection.dropped_context_records += 1
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        if payload.get("type") == "reasoning":
            projection.dropped_reasoning_records += 1
        cleaned, removed, unresolved = _sanitize_payload(payload, contracts)
        projection.removed_encrypted_fields += removed
        projection.unresolved_encrypted_calls += unresolved
        if cleaned is None:
            continue

        payload_type = cleaned.get("type")
        if payload_type == "message":
            visible_text = _visible_message_text(cleaned).strip()
            if cleaned.get("role") == "assistant" and visible_text:
                projection.assistant_messages += 1
                if cleaned.get("phase") == "final_answer":
                    projection.final_answers += 1
            elif cleaned.get("role") == "user" and visible_text:
                projection.user_messages += 1
        call_id = cleaned.get("call_id")
        if isinstance(call_id, str):
            if payload_type in {"function_call", "custom_tool_call"}:
                projection.tool_calls.add(call_id)
            elif payload_type in {"function_call_output", "custom_tool_call_output"}:
                projection.tool_outputs.add(call_id)

        projection.lines.append(
            {
                "timestamp": record.get("timestamp"),
                "source_type": top_type,
                "sequence": sequence,
                "payload": cleaned,
            }
        )
    return projection


def discover_sessions(codex_home: Path, run_id: str) -> list[SessionSource]:
    sessions: list[SessionSource] = []
    for path in (codex_home / "sessions").rglob("*.jsonl"):
        try:
            first = json.loads(path.open(errors="replace").readline())
        except (OSError, json.JSONDecodeError):
            continue
        metadata = first.get("payload")
        if not isinstance(metadata, dict):
            continue
        if run_id not in str(metadata.get("cwd") or ""):
            continue
        sessions.append(SessionSource(path=path, metadata=metadata))
    sessions.sort(key=lambda item: (not item.is_root, item.role, item.thread_id))
    return sessions


def index_company_resolver_sessions(codex_home: Path) -> dict[str, list[SessionSource]]:
    """Index retained resolver sessions once for an efficient bulk backfill."""
    indexed: dict[str, list[SessionSource]] = {}
    for path in (codex_home / "sessions").rglob("*.jsonl"):
        try:
            first = json.loads(path.open(errors="replace").readline())
        except (OSError, json.JSONDecodeError):
            continue
        metadata = first.get("payload")
        if not isinstance(metadata, dict):
            continue
        cwd = str(metadata.get("cwd") or "")
        match = _RUN_ID_FROM_CWD_RE.search(cwd)
        if not match:
            continue
        indexed.setdefault(match.group(1), []).append(SessionSource(path=path, metadata=metadata))
    for sessions in indexed.values():
        sessions.sort(key=lambda item: (not item.is_root, item.role, item.thread_id))
    return indexed


def _ledger_run(ledger_path: Path, run_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(ledger_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(f"run not found in ledger: {run_id}")
    return dict(row)


def _write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    with path.open("w") as handle:
        for value in values:
            handle.write(json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n")


def _safe_filename_component(value: str, *, fallback: str) -> str:
    component = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-")
    return component or fallback


def _safe_thread_filename(source: SessionSource) -> str:
    role = _safe_filename_component(source.role, fallback="thread")
    thread_id = _safe_filename_component(source.thread_id, fallback="unknown")
    return f"{role}-{thread_id}.jsonl"


def _session_tree_errors(sessions: list[SessionSource], root_id: str) -> list[str]:
    by_id: dict[str, SessionSource] = {}
    errors: list[str] = []
    for source in sessions:
        if source.thread_id in by_id:
            errors.append(f"duplicate thread id {source.thread_id}")
        by_id[source.thread_id] = source
    for source in sessions:
        if source.thread_id == root_id:
            if source.parent_thread_id:
                errors.append(f"root {root_id} unexpectedly has a parent")
            continue
        parent_id = source.parent_thread_id
        if not parent_id or parent_id not in by_id:
            errors.append(f"thread {source.thread_id} has missing parent {parent_id!r}")
            continue
        seen = {source.thread_id}
        cursor = parent_id
        while cursor != root_id:
            if cursor in seen:
                errors.append(f"thread {source.thread_id} is in a parent cycle")
                break
            seen.add(cursor)
            parent = by_id.get(cursor)
            if parent is None or not parent.parent_thread_id:
                errors.append(f"thread {source.thread_id} does not resolve to root {root_id}")
                break
            cursor = parent.parent_thread_id
    return errors


def _project_codex_exec(trace_path: Path, output_path: Path) -> dict[str, Any]:
    records, invalid, recovered = _read_jsonl(trace_path)
    output = []
    dropped = 0
    removed = 0
    dropped_reasoning = 0
    for sequence, record in enumerate(records):
        item = record.get("item")
        if record.get("type") == "reasoning" or (
            isinstance(item, dict) and item.get("type") == "reasoning"
        ):
            dropped_reasoning += 1
            continue
        cleaned, count = _strip_encrypted(_normalize_value(copy.deepcopy(record)))
        removed += count
        if isinstance(cleaned, dict):
            output.append({"sequence": sequence, **cleaned})
        else:
            dropped += 1
    _write_jsonl(output_path, output)
    return {
        "records": len(output),
        "invalid_source_lines": invalid,
        "recovered_source_records": recovered,
        "dropped_records": dropped,
        "removed_encrypted_fields": removed,
        "dropped_reasoning_records": dropped_reasoning,
    }


def build_bundle(
    *,
    run_id: str,
    runner_root: Path,
    codex_home: Path,
    output_dir: Path,
    sessions: list[SessionSource] | None = None,
) -> dict[str, Any]:
    ledger_path = runner_root / "state" / "ledger.sqlite"
    run = _ledger_run(ledger_path, run_id)
    sessions = sessions if sessions is not None else discover_sessions(codex_home, run_id)
    roots = [session for session in sessions if session.is_root]
    if len(roots) != 1:
        raise RuntimeError(f"expected one root session for {run_id}, found {len(roots)}")
    root = roots[0]
    root_records, _, _ = _read_jsonl(root.path)
    contracts = _extract_contracts(root_records)

    output_dir.mkdir(parents=True, exist_ok=True)
    threads_dir = output_dir / "threads"
    threads_dir.mkdir()
    projections: list[ThreadProjection] = []
    file_entries: list[dict[str, Any]] = []
    merged_events: list[dict[str, Any]] = []
    thread_headers: list[dict[str, Any]] = []

    for source in sessions:
        task_contract = None if source.is_root else _contract_for_role(source.role, contracts)
        projection = project_thread(source, contracts=contracts, task_contract=task_contract)
        projections.append(projection)
        relative_path = Path("threads") / _safe_thread_filename(source)
        destination = output_dir / relative_path
        header = {
            "type": "thread_header",
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "thread_id": source.thread_id,
            "parent_thread_id": source.parent_thread_id,
            "role": source.role,
            "agent_path": source.metadata.get("agent_path"),
            "agent_nickname": source.metadata.get("agent_nickname"),
            "task_contract": task_contract,
            "source_commit": (source.metadata.get("git") or {}).get("commit_hash")
            if isinstance(source.metadata.get("git"), dict)
            else None,
        }
        thread_headers.append(header)
        projected_records: list[dict[str, Any]] = [header]
        if task_contract:
            projected_records.append(
                {
                    "timestamp": projection.lines[0].get("timestamp") if projection.lines else None,
                    "source_type": "reconstructed_task_contract",
                    "sequence": -1,
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": task_contract}],
                        "provenance": "root_rendered_ws_track_contract",
                    },
                }
            )
        projected_records.extend(projection.lines)
        _write_jsonl(destination, projected_records)
        for record in projected_records[1:]:
            merged_events.append(
                {
                    "timestamp": record.get("timestamp"),
                    "thread_id": source.thread_id,
                    "parent_thread_id": source.parent_thread_id,
                    "role": source.role,
                    "thread_sequence": record.get("sequence"),
                    "event": record,
                }
            )
        file_entries.append(
            {
                "path": str(relative_path),
                "thread_id": source.thread_id,
                "parent_thread_id": source.parent_thread_id,
                "role": source.role,
                "source_path": str(source.path),
                "source_sha256": _sha256(source.path),
                "source_bytes": source.path.stat().st_size,
                "sha256": _sha256(destination),
                "bytes": destination.stat().st_size,
                "records": len(projected_records),
            }
        )

    merged_events.sort(
        key=lambda item: (
            str(item.get("timestamp") or ""),
            str(item["thread_id"]),
            int(item.get("thread_sequence") or 0),
        )
    )
    trajectory_path = output_dir / "trajectory.jsonl"
    _write_jsonl(
        trajectory_path,
        [
            {
                "type": "trajectory_header",
                "schema_version": SCHEMA_VERSION,
                "run_id": run_id,
                "root_thread_id": root.thread_id,
                "threads": thread_headers,
            },
            *merged_events,
        ],
    )
    file_entries.append(
        {
            "path": trajectory_path.name,
            "role": "merged_trajectory",
            "sha256": _sha256(trajectory_path),
            "bytes": trajectory_path.stat().st_size,
            "records": len(merged_events) + 1,
        }
    )

    trace_path = Path(run["trace_path"]) if run.get("trace_path") else None
    trace_summary = None
    if trace_path and trace_path.is_file():
        destination = output_dir / "codex-exec.jsonl"
        trace_summary = _project_codex_exec(trace_path, destination)
        file_entries.append(
            {
                "path": destination.name,
                "role": "codex_exec",
                "source_path": str(trace_path),
                "source_sha256": _sha256(trace_path),
                "source_bytes": trace_path.stat().st_size,
                "sha256": _sha256(destination),
                "bytes": destination.stat().st_size,
                "records": trace_summary["records"],
            }
        )

    stderr_path = Path(run["stderr_path"]) if run.get("stderr_path") else None
    stderr_summary = None
    if stderr_path and stderr_path.is_file():
        destination = output_dir / "runner-stderr.log"
        stderr_text = _normalize_string(stderr_path.read_text(errors="replace"))
        destination.write_text(stderr_text)
        stderr_summary = {
            "bytes": destination.stat().st_size,
            "lines": len(stderr_text.splitlines()),
        }
        file_entries.append(
            {
                "path": destination.name,
                "role": "runner_stderr",
                "source_path": str(stderr_path),
                "source_sha256": _sha256(stderr_path),
                "source_bytes": stderr_path.stat().st_size,
                "sha256": _sha256(destination),
                "bytes": destination.stat().st_size,
                "records": stderr_summary["lines"],
            }
        )

    root_id = root.thread_id
    structural_errors = _session_tree_errors(sessions, root_id)
    if run.get("state") not in _EXPORTABLE_STATES:
        structural_errors.append(f"run state {run.get('state')!r} is not terminal")

    missing_contracts = [
        projection.source.thread_id
        for projection in projections
        if not projection.source.is_root and not projection.task_contract
    ]
    unresolved_calls = sum(item.unresolved_encrypted_calls for item in projections)
    invalid_lines = sum(item.invalid_source_lines for item in projections) + int(
        trace_summary["invalid_source_lines"] if trace_summary else 0
    )
    recovered_records = sum(item.recovered_source_records for item in projections) + int(
        trace_summary["recovered_source_records"] if trace_summary else 0
    )
    unmatched_calls = sorted(
        set().union(*(item.tool_calls - item.tool_outputs for item in projections))
    )
    unmatched_outputs = sorted(
        set().union(*(item.tool_outputs - item.tool_calls for item in projections))
    )

    assistant_messages = sum(item.assistant_messages for item in projections)
    user_messages = sum(item.user_messages for item in projections)
    root_user_messages = next(
        item.user_messages for item in projections if item.source.thread_id == root_id
    )
    final_answers = sum(item.final_answers for item in projections)
    quality_tier = "gold"
    if assistant_messages == 0:
        quality_tier = "diagnostic"
    elif (
        root_user_messages == 0
        or missing_contracts
        or unresolved_calls
        or unmatched_calls
        or unmatched_outputs
        or final_answers == 0
    ):
        quality_tier = "silver"
    if structural_errors or invalid_lines:
        quality_tier = "quarantined"

    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "run": {
            "run_id": run_id,
            "issue": run.get("issue"),
            "state": run.get("state"),
            "pr_url": run.get("pr_url"),
            "pr_number": run.get("pr_number"),
            "branch": run.get("branch"),
            "created_at": run.get("created_at"),
            "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"),
            "outcome_reason": run.get("outcome_reason"),
            "retry_after_at": run.get("retry_after_at"),
            "attempt": run.get("attempt"),
            "error_present": bool(run.get("error")),
        },
        "root_thread_id": root_id,
        "thread_count": len(sessions),
        "subagent_count": len(sessions) - 1,
        "files": file_entries,
        "quality": {
            "tier": quality_tier,
            "structural_errors": structural_errors,
            "invalid_source_lines": invalid_lines,
            "recovered_source_records": recovered_records,
            "missing_task_contract_thread_ids": missing_contracts,
            "unresolved_encrypted_collaboration_calls": unresolved_calls,
            "unmatched_tool_call_ids": unmatched_calls,
            "unmatched_tool_output_ids": unmatched_outputs,
            "removed_reasoning_records": sum(
                item.dropped_reasoning_records for item in projections
            ),
            "removed_context_records": sum(item.dropped_context_records for item in projections),
            "removed_encrypted_fields": sum(item.removed_encrypted_fields for item in projections),
            "assistant_messages": assistant_messages,
            "user_messages": user_messages,
            "root_user_messages": root_user_messages,
            "final_answers": final_answers,
        },
        "codex_exec": trace_summary,
        "runner_stderr": stderr_summary,
        "trajectory_path": trajectory_path.name,
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    credential_findings = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file():
            continue
        for finding in detect_credentials(path.read_text(errors="replace")):
            credential_findings.append({"path": str(path.relative_to(output_dir)), **finding})
    manifest["quality"]["credential_findings"] = credential_findings
    if credential_findings:
        manifest["quality"]["tier"] = "quarantined"
    manifest["bundle_content_sha256"] = _json_sha256(
        {entry["path"]: entry["sha256"] for entry in file_entries}
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    return manifest


def _hf_token() -> str | None:
    token = os.environ.get("HF_TOKEN")
    if token:
        return token
    from huggingface_hub import get_token

    return get_token()


def _validate_downloaded_bundle_file(path: Path, relative_path: str) -> None:
    """Validate the downloaded representation, not only its checksum."""
    if relative_path.endswith(".jsonl"):
        for line_number, line in enumerate(path.read_text(errors="strict").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"remote JSONL is invalid at {relative_path}:{line_number}"
                ) from exc
            if not isinstance(value, dict):
                raise RuntimeError(
                    f"remote JSONL record is not an object at {relative_path}:{line_number}"
                )
    elif relative_path == "manifest.json" or relative_path.endswith("/manifest.json"):
        try:
            value = json.loads(path.read_text(errors="strict"))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"remote manifest is invalid: {relative_path}") from exc
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeError(f"remote manifest schema is invalid: {relative_path}")


def _batch_delete_patterns(upload_root: Path) -> list[str]:
    patterns: list[str] = []
    for bundle_dir in sorted(path for path in upload_root.glob("*/*") if path.is_dir()):
        relative = bundle_dir.relative_to(upload_root).as_posix()
        patterns.extend((f"{relative}/*", f"{relative}/**/*"))
    return patterns


def upload_and_verify(
    *,
    bundle_dir: Path,
    run_id: str,
    repo_id: str,
    prefix: str,
    quality_tier: str,
) -> tuple[str, dict[str, str]]:
    token = _hf_token()
    if not token:
        raise RuntimeError("Hugging Face token unavailable")
    from huggingface_hub import HfApi, hf_hub_download

    api = HfApi(token=token)
    remote_dir = f"{prefix.rstrip('/')}/{quality_tier}/{run_id}"
    commit = api.upload_folder(
        folder_path=bundle_dir,
        path_in_repo=remote_dir,
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=f"Backfill Codex training bundle {run_id}",
        delete_patterns=["*", "**/*"],
    )
    verified: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="trace-hf-verify-") as verify_temp:
        verify_dir = Path(verify_temp)
        for path in sorted(bundle_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(bundle_dir).as_posix()
            remote_path = f"{remote_dir}/{relative}"
            kwargs: dict[str, Any] = {
                "repo_id": repo_id,
                "repo_type": "dataset",
                "filename": remote_path,
                "token": token,
                "force_download": True,
                "local_dir": verify_dir,
            }
            revision = getattr(commit, "oid", None)
            if isinstance(revision, str) and revision:
                kwargs["revision"] = revision
            downloaded = Path(hf_hub_download(**kwargs))
            local_hash = _sha256(path)
            remote_hash = _sha256(downloaded)
            if local_hash != remote_hash:
                raise RuntimeError(f"remote checksum mismatch for {remote_path}")
            _validate_downloaded_bundle_file(downloaded, relative)
            verified[remote_path] = remote_hash
    return remote_dir, verified


def upload_batch_and_verify(
    *,
    upload_root: Path,
    repo_id: str,
    prefix: str,
) -> tuple[str, dict[str, str]]:
    """Upload a tier/run directory tree in one commit and verify every object."""
    token = _hf_token()
    if not token:
        raise RuntimeError("Hugging Face token unavailable")
    from huggingface_hub import HfApi, snapshot_download

    api = HfApi(token=token)
    remote_root = prefix.rstrip("/")
    commit = api.upload_folder(
        folder_path=upload_root,
        path_in_repo=remote_root,
        repo_id=repo_id,
        repo_type="dataset",
        commit_message=f"Backfill {len(list(upload_root.glob('*/*')))} Codex bundles",
        delete_patterns=_batch_delete_patterns(upload_root),
    )
    local_files = [path for path in sorted(upload_root.rglob("*")) if path.is_file()]
    remote_paths = [
        f"{remote_root}/{path.relative_to(upload_root).as_posix()}" for path in local_files
    ]
    with tempfile.TemporaryDirectory(prefix="trace-hf-batch-verify-") as verify_temp:
        kwargs: dict[str, Any] = {
            "repo_id": repo_id,
            "repo_type": "dataset",
            "token": token,
            "allow_patterns": remote_paths,
            "local_dir": Path(verify_temp),
            "max_workers": 8,
        }
        revision = getattr(commit, "oid", None)
        if isinstance(revision, str) and revision:
            kwargs["revision"] = revision
        snapshot_download(**kwargs)
        verified: dict[str, str] = {}
        for local_path, remote_path in zip(local_files, remote_paths, strict=True):
            downloaded = Path(verify_temp) / remote_path
            if not downloaded.is_file():
                raise RuntimeError(f"remote verification missing {remote_path}")
            local_hash = _sha256(local_path)
            remote_hash = _sha256(downloaded)
            if local_hash != remote_hash:
                raise RuntimeError(f"remote checksum mismatch for {remote_path}")
            _validate_downloaded_bundle_file(
                downloaded,
                local_path.relative_to(upload_root).as_posix(),
            )
            verified[remote_path] = remote_hash
    return remote_root, verified


def _record_trace_export_attempt(
    *,
    ledger_path: Path,
    run_id: str,
    status: str,
    quality_tier: str | None = None,
    remote_dir: str | None = None,
    error: str | None = None,
) -> None:
    now = int(datetime.now(UTC).timestamp())
    with sqlite3.connect(ledger_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_bundle_export_attempts (
                run_id TEXT PRIMARY KEY,
                attempts INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                quality_tier TEXT,
                remote_dir TEXT,
                error TEXT,
                last_attempt_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trace_bundle_export_attempts (
                run_id, attempts, status, quality_tier,
                remote_dir, error, last_attempt_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                attempts = trace_bundle_export_attempts.attempts + 1,
                status = excluded.status,
                quality_tier = excluded.quality_tier,
                remote_dir = excluded.remote_dir,
                error = excluded.error,
                last_attempt_at = excluded.last_attempt_at
            """,
            (run_id, status, quality_tier, remote_dir, error, now),
        )


def _backfill_run_ids(ledger_path: Path, *, limit: int | None) -> list[str]:
    with sqlite3.connect(ledger_path) as conn:
        conn.row_factory = sqlite3.Row
        export_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trace_bundle_exports'"
        ).fetchone()
        if export_table:
            query = """
                SELECT r.run_id
                FROM runs AS r
                LEFT JOIN trace_bundle_exports AS e ON e.run_id = r.run_id
                WHERE r.state IN (
                    'completed', 'failed', 'timeout',
                    'submitted', 'rejected', 'escalated',
                    'retryable', 'interrupted'
                )
                  AND r.run_id LIKE 'issue-%'
                  AND e.cleaned_at IS NULL
                ORDER BY r.created_at, r.run_id
            """
        else:
            query = """
                SELECT run_id FROM runs
                WHERE state IN (
                    'completed', 'failed', 'timeout',
                    'submitted', 'rejected', 'escalated',
                    'retryable', 'interrupted'
                )
                  AND run_id LIKE 'issue-%'
                ORDER BY created_at, run_id
            """
        rows = conn.execute(query).fetchall()
    run_ids = [str(row["run_id"]) for row in rows]
    return run_ids[:limit] if limit is not None else run_ids


def trace_export_report(*, runner_root: Path, codex_home: Path) -> dict[str, Any]:
    """Reconcile terminal runs, retained source bytes, and durable exports."""
    ledger_path = runner_root / "state" / "ledger.sqlite"
    sessions = index_company_resolver_sessions(codex_home)
    pending_ids = set(_backfill_run_ids(ledger_path, limit=None))
    session_paths = {
        source.path
        for run_id in pending_ids
        for source in sessions.get(run_id, [])
        if source.path.is_file()
    }
    local_session_bytes = sum(path.stat().st_size for path in session_paths)

    terminal_states = (
        "completed",
        "failed",
        "timeout",
        "submitted",
        "rejected",
        "escalated",
        "retryable",
        "interrupted",
    )
    placeholders = ",".join("?" for _ in terminal_states)
    with sqlite3.connect(ledger_path) as conn:
        conn.row_factory = sqlite3.Row
        terminal_rows = conn.execute(
            f"SELECT run_id, trace_path, stderr_path FROM runs "
            f"WHERE state IN ({placeholders}) AND run_id LIKE 'issue-%'",
            terminal_states,
        ).fetchall()
        local_runner_paths = {
            Path(value)
            for row in terminal_rows
            if str(row["run_id"]) in pending_ids
            for value in (row["trace_path"], row["stderr_path"])
            if isinstance(value, str) and Path(value).is_file()
        }
        export_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trace_bundle_exports'"
        ).fetchone()
        attempt_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'trace_bundle_export_attempts'"
        ).fetchone()
        exports = []
        if export_table:
            export_columns = {
                str(row["name"]) for row in conn.execute("PRAGMA table_info(trace_bundle_exports)")
            }
            aggregate = {
                "source_bytes": "SUM(source_bytes)" if "source_bytes" in export_columns else "0",
                "projected_bytes": (
                    "SUM(projected_bytes)" if "projected_bytes" in export_columns else "0"
                ),
                "threads": "SUM(thread_count)" if "thread_count" in export_columns else "0",
                "subagents": ("SUM(subagent_count)" if "subagent_count" in export_columns else "0"),
            }
            exports = [
                dict(row)
                for row in conn.execute(
                    "SELECT schema_version, quality_tier, COUNT(*) AS runs, "
                    f"{aggregate['source_bytes']} AS source_bytes, "
                    f"{aggregate['projected_bytes']} AS projected_bytes, "
                    f"{aggregate['threads']} AS threads, "
                    f"{aggregate['subagents']} AS subagents, "
                    "SUM(cleaned_at IS NOT NULL) AS cleaned "
                    "FROM trace_bundle_exports "
                    "WHERE run_id LIKE 'issue-%' "
                    "GROUP BY schema_version, quality_tier"
                )
            ]
        attempts = (
            {
                str(row["status"]): int(row["runs"])
                for row in conn.execute(
                    "SELECT a.status, COUNT(*) AS runs "
                    "FROM trace_bundle_export_attempts AS a "
                    "JOIN runs AS r ON r.run_id = a.run_id "
                    "WHERE r.run_id LIKE 'issue-%' GROUP BY a.status"
                )
            }
            if attempt_table
            else {}
        )
        exported_ids = (
            {
                str(row["run_id"])
                for row in conn.execute(
                    "SELECT run_id FROM trace_bundle_exports WHERE run_id LIKE 'issue-%'"
                )
            }
            if export_table
            else set()
        )
        attempted_ids = (
            {
                str(row["run_id"])
                for row in conn.execute(
                    "SELECT run_id FROM trace_bundle_export_attempts WHERE run_id LIKE 'issue-%'"
                )
            }
            if attempt_table
            else set()
        )

    terminal_ids = {str(row["run_id"]) for row in terminal_rows}
    disk = shutil.disk_usage(runner_root)
    return {
        "terminal_runs": len(terminal_ids),
        "pending_runs": len(pending_ids),
        "unaccounted_runs": len(terminal_ids - exported_ids - attempted_ids),
        "retained_session_files": len(session_paths),
        "retained_session_bytes": local_session_bytes,
        "retained_runner_files": len(local_runner_paths),
        "retained_runner_bytes": sum(path.stat().st_size for path in local_runner_paths),
        "exports_by_tier": exports,
        "attempts_by_status": attempts,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
    }


def backfill_all(
    *,
    runner_root: Path,
    codex_home: Path,
    repo_id: str,
    prefix: str,
    batch_size: int,
    cleanup: bool,
    allow_silver: bool,
    allow_diagnostic: bool,
    limit: int | None,
) -> dict[str, Any]:
    """Export retained resolver runs in bounded, independently verified batches."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    ledger_path = runner_root / "state" / "ledger.sqlite"
    session_index = index_company_resolver_sessions(codex_home)
    candidates = _backfill_run_ids(ledger_path, limit=limit)
    eligible = [run_id for run_id in candidates if run_id in session_index]
    summary: dict[str, Any] = {
        "candidates": len(candidates),
        "eligible": len(eligible),
        "uploaded": 0,
        "cleaned": 0,
        "quarantined": 0,
        "unavailable": len(candidates) - len(eligible),
        "failed": 0,
        "reclaimed_bytes": 0,
        "tiers": {"gold": 0, "silver": 0, "diagnostic": 0},
    }
    for run_id in candidates:
        if run_id not in session_index:
            _record_trace_export_attempt(
                ledger_path=ledger_path,
                run_id=run_id,
                status="unavailable",
                error="no retained Codex session tree",
            )

    for offset in range(0, len(eligible), batch_size):
        batch_ids = eligible[offset : offset + batch_size]
        with tempfile.TemporaryDirectory(
            prefix="trace-backfill-batch-", dir=runner_root / "state"
        ) as batch_temp:
            batch_root = Path(batch_temp)
            upload_root = batch_root / "upload"
            upload_root.mkdir()
            manifests: dict[str, dict[str, Any]] = {}
            for run_id in batch_ids:
                building = batch_root / "building" / run_id
                try:
                    manifest = build_bundle(
                        run_id=run_id,
                        runner_root=runner_root,
                        codex_home=codex_home,
                        output_dir=building,
                        sessions=session_index[run_id],
                    )
                    tier = str(manifest["quality"]["tier"])
                    if tier == "quarantined":
                        summary["quarantined"] += 1
                        _record_trace_export_attempt(
                            ledger_path=ledger_path,
                            run_id=run_id,
                            status="quarantined",
                            quality_tier=tier,
                            error=quality_gate_reason(manifest),
                        )
                        continue
                    if tier == "silver" and not allow_silver:
                        raise RuntimeError("silver bundle not allowed")
                    if tier == "diagnostic" and not allow_diagnostic:
                        raise RuntimeError("diagnostic bundle not allowed")
                    destination = upload_root / tier / run_id
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(building), destination)
                    manifests[run_id] = manifest
                except Exception as exc:  # noqa: BLE001 - isolate malformed runs
                    summary["failed"] += 1
                    _record_trace_export_attempt(
                        ledger_path=ledger_path,
                        run_id=run_id,
                        status="failed",
                        error=_safe_error(exc),
                    )
            if not manifests:
                continue
            try:
                _, verified = upload_batch_and_verify(
                    upload_root=upload_root,
                    repo_id=repo_id,
                    prefix=prefix,
                )
            except Exception as exc:  # noqa: BLE001 - retain all batch sources
                error = _safe_error(exc)
                for run_id, manifest in manifests.items():
                    summary["failed"] += 1
                    _record_trace_export_attempt(
                        ledger_path=ledger_path,
                        run_id=run_id,
                        status="failed",
                        quality_tier=str(manifest["quality"]["tier"]),
                        error=error,
                    )
                print(
                    json.dumps(
                        {
                            "phase": "batch_failed",
                            "offset": offset,
                            "runs": len(manifests),
                            "error": error,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                continue

            for run_id, manifest in manifests.items():
                tier = str(manifest["quality"]["tier"])
                remote_dir = f"{prefix.rstrip('/')}/{tier}/{run_id}"
                run_verified = {
                    path: digest
                    for path, digest in verified.items()
                    if path.startswith(f"{remote_dir}/")
                }
                try:
                    expected_verified = {
                        f"{remote_dir}/{path.relative_to(upload_root / tier / run_id).as_posix()}"
                        for path in (upload_root / tier / run_id).rglob("*")
                        if path.is_file()
                    }
                    if set(run_verified) != expected_verified:
                        raise RuntimeError(
                            f"verified object set mismatch for {run_id}: "
                            f"expected {len(expected_verified)}, got {len(run_verified)}"
                        )
                    record_verified_export(
                        ledger_path=ledger_path,
                        run_id=run_id,
                        remote_dir=remote_dir,
                        manifest=manifest,
                        verified=run_verified,
                    )
                    reclaimed = 0
                    status = "verified"
                    if cleanup:
                        cleanup_result = cleanup_verified_sources(
                            ledger_path=ledger_path,
                            run_id=run_id,
                            manifest=manifest,
                        )
                        reclaimed = int(cleanup_result["reclaimed_bytes"])
                        summary["cleaned"] += 1
                        summary["reclaimed_bytes"] += reclaimed
                        status = "cleaned"
                    _record_trace_export_attempt(
                        ledger_path=ledger_path,
                        run_id=run_id,
                        status=status,
                        quality_tier=tier,
                        remote_dir=remote_dir,
                    )
                    summary["uploaded"] += 1
                    summary["tiers"][tier] += 1
                    print(
                        json.dumps(
                            {
                                "phase": status,
                                "run_id": run_id,
                                "quality_tier": tier,
                                "remote_dir": remote_dir,
                                "reclaimed_bytes": reclaimed,
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                except Exception as exc:  # noqa: BLE001 - continue verified batch
                    summary["failed"] += 1
                    _record_trace_export_attempt(
                        ledger_path=ledger_path,
                        run_id=run_id,
                        status="failed",
                        quality_tier=tier,
                        remote_dir=remote_dir,
                        error=_safe_error(exc),
                    )
    return summary


def record_verified_export(
    *,
    ledger_path: Path,
    run_id: str,
    remote_dir: str,
    manifest: dict[str, Any],
    verified: dict[str, str],
) -> None:
    now = int(datetime.now(UTC).timestamp())
    with sqlite3.connect(ledger_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trace_bundle_exports (
                run_id TEXT PRIMARY KEY,
                schema_version TEXT NOT NULL,
                quality_tier TEXT NOT NULL,
                remote_dir TEXT NOT NULL,
                bundle_content_sha256 TEXT NOT NULL,
                verified_files_json TEXT NOT NULL,
                source_bytes INTEGER NOT NULL DEFAULT 0,
                projected_bytes INTEGER NOT NULL DEFAULT 0,
                thread_count INTEGER NOT NULL DEFAULT 0,
                subagent_count INTEGER NOT NULL DEFAULT 0,
                verified_at INTEGER NOT NULL,
                cleaned_at INTEGER
            )
            """
        )
        existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(trace_bundle_exports)")}
        for name, ddl in {
            "source_bytes": (
                "ALTER TABLE trace_bundle_exports "
                "ADD COLUMN source_bytes INTEGER NOT NULL DEFAULT 0"
            ),
            "projected_bytes": (
                "ALTER TABLE trace_bundle_exports "
                "ADD COLUMN projected_bytes INTEGER NOT NULL DEFAULT 0"
            ),
            "thread_count": (
                "ALTER TABLE trace_bundle_exports "
                "ADD COLUMN thread_count INTEGER NOT NULL DEFAULT 0"
            ),
            "subagent_count": (
                "ALTER TABLE trace_bundle_exports "
                "ADD COLUMN subagent_count INTEGER NOT NULL DEFAULT 0"
            ),
        }.items():
            if name not in existing:
                conn.execute(ddl)
        source_bytes = sum(int(entry.get("source_bytes", 0)) for entry in manifest["files"])
        projected_bytes = sum(int(entry["bytes"]) for entry in manifest["files"])
        conn.execute(
            """
            INSERT INTO trace_bundle_exports (
                run_id, schema_version, quality_tier, remote_dir,
                bundle_content_sha256, verified_files_json,
                source_bytes, projected_bytes, thread_count, subagent_count,
                verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                schema_version = excluded.schema_version,
                quality_tier = excluded.quality_tier,
                remote_dir = excluded.remote_dir,
                bundle_content_sha256 = excluded.bundle_content_sha256,
                verified_files_json = excluded.verified_files_json,
                source_bytes = excluded.source_bytes,
                projected_bytes = excluded.projected_bytes,
                thread_count = excluded.thread_count,
                subagent_count = excluded.subagent_count,
                verified_at = excluded.verified_at
            """,
            (
                run_id,
                manifest["schema_version"],
                manifest["quality"]["tier"],
                remote_dir,
                manifest["bundle_content_sha256"],
                json.dumps(verified, sort_keys=True),
                source_bytes,
                projected_bytes,
                manifest["thread_count"],
                manifest["subagent_count"],
                now,
            ),
        )


def cleanup_verified_sources(
    *, ledger_path: Path, run_id: str, manifest: dict[str, Any]
) -> dict[str, Any]:
    conn = sqlite3.connect(ledger_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM trace_bundle_exports WHERE run_id = ?", (run_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise RuntimeError(f"no verified export ledger row for {run_id}")
    if row["bundle_content_sha256"] != manifest["bundle_content_sha256"]:
        raise RuntimeError("verified export does not match current bundle manifest")
    if manifest["quality"]["tier"] == "quarantined":
        raise RuntimeError("refusing cleanup for quarantined bundle")
    verified_files = json.loads(row["verified_files_json"])
    if not isinstance(verified_files, dict):
        raise RuntimeError("verified export object inventory is invalid")
    for entry in manifest["files"]:
        remote_path = f"{row['remote_dir'].rstrip('/')}/{entry['path']}"
        if verified_files.get(remote_path) != entry["sha256"]:
            raise RuntimeError(f"projected object was not checksum-verified: {entry['path']}")

    verified_sources: list[tuple[Path, int]] = []
    for entry in manifest["files"]:
        raw = entry.get("source_path")
        source_hash = entry.get("source_sha256")
        if not raw or not source_hash:
            continue
        path = Path(raw)
        if not path.is_file():
            continue
        if _sha256(path) != source_hash:
            raise RuntimeError(f"source changed after export; refusing cleanup: {path}")
        verified_sources.append((path, path.stat().st_size))

    removed = []
    reclaimed = 0
    for path, size in verified_sources:
        path.unlink()
        removed.append(str(path))
        reclaimed += size

    cleaned_at = int(datetime.now(UTC).timestamp())
    with sqlite3.connect(ledger_path) as update_conn:
        update_conn.execute(
            "UPDATE trace_bundle_exports SET cleaned_at = ? WHERE run_id = ?",
            (cleaned_at, run_id),
        )
    return {"removed_files": removed, "reclaimed_bytes": reclaimed, "cleaned_at": cleaned_at}


def _result_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": manifest["run"]["run_id"],
        "state": manifest["run"]["state"],
        "quality": manifest["quality"],
        "thread_count": manifest["thread_count"],
        "subagent_count": manifest["subagent_count"],
        "bundle_content_sha256": manifest["bundle_content_sha256"],
        "projected_bytes": sum(entry["bytes"] for entry in manifest["files"]),
    }


def quality_gate_reason(manifest: dict[str, Any]) -> str:
    """Return a non-secret quarantine summary suitable for the export ledger."""
    quality = manifest.get("quality")
    if not isinstance(quality, dict):
        return "quality gate rejected bundle: manifest quality summary missing"
    findings = quality.get("credential_findings")
    credential_findings = findings if isinstance(findings, list) else []
    patterns = sorted(
        {
            str(finding.get("pattern"))
            for finding in credential_findings
            if isinstance(finding, dict) and finding.get("pattern")
        }
    )
    structural = quality.get("structural_errors")
    structural_errors = structural if isinstance(structural, list) else []
    return (
        "quality gate rejected bundle: "
        f"credential_findings={len(credential_findings)}"
        f"({','.join(patterns) or 'none'}); "
        f"structural_errors={len(structural_errors)}; "
        f"invalid_source_lines={int(quality.get('invalid_source_lines') or 0)}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--all", action="store_true", help="backfill every retained run")
    parser.add_argument("--report", action="store_true", help="print local/export reconciliation")
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--runner-root", type=Path, default=Path("/srv/jobseek-codex"))
    parser.add_argument("--codex-home", type=Path, default=Path.home() / ".codex")
    parser.add_argument("--repo-id", default=DEFAULT_HF_REPO)
    parser.add_argument("--prefix", default=DEFAULT_HF_PREFIX)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--allow-silver", action="store_true")
    parser.add_argument("--allow-diagnostic", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.report:
        if args.all or args.run_id:
            raise SystemExit("--report does not accept a run_id or --all")
        report = trace_export_report(
            runner_root=args.runner_root,
            codex_home=args.codex_home,
        )
        print(json.dumps({"phase": "report", **report}, sort_keys=True))
        return 0
    if args.all and args.run_id:
        raise SystemExit("provide a run_id or --all, not both")
    if not args.all and not args.run_id:
        raise SystemExit("provide a run_id or --all")
    if args.cleanup and not args.upload:
        raise SystemExit("--cleanup requires --upload")
    if args.all:
        if not args.upload:
            raise SystemExit("--all requires --upload")
        summary = backfill_all(
            runner_root=args.runner_root,
            codex_home=args.codex_home,
            repo_id=args.repo_id,
            prefix=args.prefix,
            batch_size=args.batch_size,
            cleanup=args.cleanup,
            allow_silver=args.allow_silver,
            allow_diagnostic=args.allow_diagnostic,
            limit=args.limit,
        )
        print(json.dumps({"phase": "complete", **summary}, sort_keys=True))
        return 1 if summary["failed"] else 0

    assert args.run_id is not None
    owned_temp = args.output_dir is None
    temp_root = (
        Path(tempfile.mkdtemp(prefix=f"trace-backfill-{args.run_id}-"))
        if owned_temp
        else args.output_dir
    )
    assert temp_root is not None
    bundle_dir = temp_root / args.run_id if owned_temp else temp_root
    ledger_path = args.runner_root / "state" / "ledger.sqlite"
    try:
        manifest = build_bundle(
            run_id=args.run_id,
            runner_root=args.runner_root,
            codex_home=args.codex_home,
            output_dir=bundle_dir,
        )
        print(json.dumps({"phase": "built", **_result_summary(manifest)}, sort_keys=True))
        tier = manifest["quality"]["tier"]
        if tier == "quarantined":
            _record_trace_export_attempt(
                ledger_path=ledger_path,
                run_id=args.run_id,
                status="quarantined",
                quality_tier=tier,
                error=quality_gate_reason(manifest),
            )
            raise SystemExit("bundle quarantined; refusing upload")
        if tier == "silver" and not args.allow_silver:
            raise SystemExit("bundle is silver; pass --allow-silver after review")
        if tier == "diagnostic" and not args.allow_diagnostic:
            raise SystemExit("bundle is diagnostic-only; pass --allow-diagnostic after review")
        if not args.upload:
            return 0
        try:
            remote_dir, verified = upload_and_verify(
                bundle_dir=bundle_dir,
                run_id=args.run_id,
                repo_id=args.repo_id,
                prefix=args.prefix,
                quality_tier=tier,
            )
            record_verified_export(
                ledger_path=ledger_path,
                run_id=args.run_id,
                remote_dir=remote_dir,
                manifest=manifest,
                verified=verified,
            )
        except Exception as exc:
            _record_trace_export_attempt(
                ledger_path=ledger_path,
                run_id=args.run_id,
                status="failed",
                quality_tier=tier,
                error=_safe_error(exc),
            )
            raise
        print(
            json.dumps(
                {"phase": "verified", "remote_dir": remote_dir, "files": len(verified)},
                sort_keys=True,
            )
        )
        _record_trace_export_attempt(
            ledger_path=ledger_path,
            run_id=args.run_id,
            status="verified",
            quality_tier=tier,
            remote_dir=remote_dir,
        )
        if args.cleanup:
            cleanup = cleanup_verified_sources(
                ledger_path=ledger_path, run_id=args.run_id, manifest=manifest
            )
            print(json.dumps({"phase": "cleaned", **cleanup}, sort_keys=True))
            _record_trace_export_attempt(
                ledger_path=ledger_path,
                run_id=args.run_id,
                status="cleaned",
                quality_tier=tier,
                remote_dir=remote_dir,
            )
        return 0
    finally:
        if owned_temp:
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
