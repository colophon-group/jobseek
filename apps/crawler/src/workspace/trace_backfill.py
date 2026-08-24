"""Quality-gated export of Jobseek Codex automation sessions."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
import uuid
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.workspace.safe_cleanup import (
    claim_child_at,
    directory_open_flags,
    open_absolute_directory_no_follow,
    restore_claimed_child_at,
    safe_rmtree_child,
    unlink_claimed_child_at,
    validate_child_name,
)
from src.workspace.trace import detect_credentials, redact_credentials

SCHEMA_VERSION = "jobseek-codex-training-bundle/v2"
DEFAULT_HF_REPO = "viktoroo/jobseek-agent-traces"
DEFAULT_HF_PREFIX = "training-bundles/v2"
_WORKTREE_RE = re.compile(r"/srv/jobseek-codex/worktrees/company-request-[^/\s\"']+")
_DOCUMENTATION_URL_CREDENTIAL_RE = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*)://user:pass@", re.I)
_FERNET_RE = re.compile(r"^gAAAAA[A-Za-z0-9_-]{40,}={0,2}$")
_TRACK_RE = re.compile(r"<track-([abc])>\s*(.*?)\s*</track-\1>", re.I | re.S)
_RUN_ID_FROM_CWD_RE = re.compile(
    r"(?P<run_id>issue-\d+-\d+-[A-Za-z0-9]+|"
    r"daily-(?:error-review|annotations)-\d{4}-\d{2}-\d{2}-\d+-[A-Za-z0-9]+)"
    r"(?:/|$)"
)
_AUTOMATION_RUN_SQL = (
    "(run_id LIKE 'issue-%' OR run_id LIKE 'daily-error-review-%' "
    "OR run_id LIKE 'daily-annotations-%')"
)
_DROP_TOP_LEVEL_TYPES = {"turn_context", "world_state"}
_DROP_PAYLOAD_TYPES = {"reasoning", "token_count"}
_DUPLICATE_EVENT_TYPES = {"agent_message", "user_message"}
_MAX_JSONL_RECOVERY_LINES = 200
_MAX_JSONL_RECOVERY_BYTES = 2 * 1024 * 1024
_MAX_SESSION_METADATA_BYTES = 256 * 1024
_MAX_SESSION_SOURCE_BYTES = 64 * 1024 * 1024
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


@dataclass(frozen=True)
class SessionInventory:
    by_run: dict[str, list[SessionSource]]
    all_files: tuple[Path, ...]
    entries: tuple[_RetainedSessionEntry, ...]
    unlinked: tuple[Path, ...]
    unparseable: tuple[Path, ...]
    oversize: tuple[_RetainedSessionEntry, ...]
    unsafe: tuple[_RetainedSessionEntry, ...]


@dataclass(frozen=True)
class SessionRetentionStatus:
    files: int
    bytes: int
    unlinked_files: int
    unlinked_bytes: int
    unparseable_files: int
    oversize_files: int
    oversize_bytes: int
    unsafe_files: int
    unsafe_bytes: int
    oldest_unlinked_age_s: int
    active_files: int
    over_limit: bool
    reason: str | None


@dataclass(frozen=True)
class _RetainedSessionEntry:
    path: Path
    bytes: int
    mtime: int


@dataclass
class _VerifiedSourceHandle:
    path: Path
    parent_fd: int
    file_fd: int
    name: str
    opened: os.stat_result
    size: int
    expected_sha256: str
    claimed_name: str | None = None
    already_claimed: bool = False


@dataclass(frozen=True)
class _SourceSnapshot:
    original_path: Path
    path: Path
    sha256: str
    bytes: int


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
    if path.lstat().st_size > _MAX_SESSION_SOURCE_BYTES:
        raise RuntimeError(f"session source exceeds {_MAX_SESSION_SOURCE_BYTES} bytes: {path}")
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


def discover_sessions(
    codex_home: Path,
    run_id: str,
    *,
    ledger_path: Path | None = None,
) -> list[SessionSource]:
    return list(
        inventory_automation_sessions(codex_home, ledger_path=ledger_path).by_run.get(run_id, [])
    )


def inventory_automation_sessions(
    codex_home: Path,
    *,
    ledger_path: Path | None = None,
) -> SessionInventory:
    """Classify every retained session without following a final-file symlink."""
    indexed: dict[str, list[SessionSource]] = {}
    unlinked: list[Path] = []
    unparseable: list[Path] = []
    entries, unsafe_entries = _walk_session_store_no_follow(codex_home / "sessions")
    all_files = [entry.path for entry in entries]
    entry_by_path = {entry.path: entry for entry in entries}
    oversize_entries: list[_RetainedSessionEntry] = []
    run_worktrees = _ledger_worktree_paths(ledger_path) if ledger_path is not None else {}
    known_run_ids = _ledger_run_ids(ledger_path) if ledger_path is not None else None
    for path in all_files:
        if entry_by_path[path].bytes > _MAX_SESSION_SOURCE_BYTES:
            oversize_entries.append(entry_by_path[path])
            continue
        try:
            metadata = _read_session_metadata_no_follow(
                path,
                root=codex_home / "sessions",
            )
        except json.JSONDecodeError:
            unparseable.append(path)
            continue
        except (OSError, RuntimeError):
            try:
                entry = path.lstat()
                unsafe_entries.append(
                    _RetainedSessionEntry(
                        path=path,
                        bytes=int(entry.st_size),
                        mtime=int(entry.st_mtime),
                    )
                )
            except OSError:
                unsafe_entries.append(_RetainedSessionEntry(path=path, bytes=0, mtime=0))
            continue
        if metadata is None:
            unparseable.append(path)
            continue
        cwd = str(metadata.get("cwd") or "")
        run_id = _run_id_for_cwd(cwd, run_worktrees)
        if run_id is None:
            match = _RUN_ID_FROM_CWD_RE.search(cwd)
            run_id = match.group("run_id") if match else None
            if run_id is not None and known_run_ids is not None and run_id not in known_run_ids:
                run_id = None
        if run_id is None:
            unlinked.append(path)
            continue
        indexed.setdefault(run_id, []).append(SessionSource(path=path, metadata=metadata))
    for sessions in indexed.values():
        sessions.sort(key=lambda item: (not item.is_root, item.role, item.thread_id))
    unsafe_by_path = {entry.path: entry for entry in unsafe_entries}
    entries = [entry for entry in entries if entry.path not in unsafe_by_path]
    all_files = [entry.path for entry in entries]
    return SessionInventory(
        by_run=indexed,
        all_files=tuple(all_files),
        entries=tuple(entries),
        unlinked=tuple(unlinked),
        unparseable=tuple(unparseable),
        oversize=tuple(oversize_entries),
        unsafe=tuple(sorted(unsafe_by_path.values(), key=lambda entry: str(entry.path))),
    )


def index_automation_sessions(
    codex_home: Path,
    *,
    ledger_path: Path | None = None,
) -> dict[str, list[SessionSource]]:
    """Index resolver and daily routine session trees for export/backfill."""
    return inventory_automation_sessions(codex_home, ledger_path=ledger_path).by_run


def index_company_resolver_sessions(codex_home: Path) -> dict[str, list[SessionSource]]:
    """Backward-compatible alias for callers migrating to the complete index."""
    return index_automation_sessions(codex_home)


def session_retention_status(
    *,
    runner_root: Path,
    codex_home: Path,
    max_files: int,
    max_bytes: int,
    max_unlinked_age_s: int,
    now: int | None = None,
) -> SessionRetentionStatus:
    """Return a fail-closed admission view of all retained Codex sessions."""
    ledger_path = runner_root / "state" / "ledger.sqlite"
    inventory = inventory_automation_sessions(codex_home, ledger_path=ledger_path)
    active_run_ids: set[str] = set()
    if ledger_path.is_file():
        with sqlite3.connect(ledger_path) as conn:
            try:
                active_run_ids = {
                    str(row[0])
                    for row in conn.execute(
                        "SELECT run_id FROM runs WHERE state IN ('claimed', 'running')"
                    )
                }
            except sqlite3.OperationalError:
                active_run_ids = set()
    entry_by_path = {entry.path: entry for entry in inventory.entries}
    total_bytes = sum(entry.bytes for entry in (*inventory.entries, *inventory.unsafe))
    unaccounted = (*inventory.unlinked, *inventory.unparseable)
    unlinked_bytes = sum(entry_by_path[path].bytes for path in unaccounted)
    unlinked_bytes += sum(entry.bytes for entry in inventory.oversize)
    unlinked_bytes += sum(entry.bytes for entry in inventory.unsafe)
    observed_at = int(time.time()) if now is None else now
    ages = [max(0, observed_at - entry_by_path[path].mtime) for path in unaccounted]
    ages.extend(max(0, observed_at - entry.mtime) for entry in inventory.oversize)
    ages.extend(max(0, observed_at - entry.mtime) for entry in inventory.unsafe)
    oldest_age = max(ages, default=0)
    active_files = sum(
        len(sessions) for run_id, sessions in inventory.by_run.items() if run_id in active_run_ids
    )
    reason = None
    total_files = len(inventory.entries) + len(inventory.unsafe)
    if inventory.unsafe:
        reason = f"unsafe Codex session entries retained: {len(inventory.unsafe)}"
    elif inventory.oversize:
        reason = f"oversize Codex session files retained: {len(inventory.oversize)}"
    elif max_files >= 0 and total_files >= max_files:
        reason = f"retained Codex session file limit reached: {total_files}"
    elif max_bytes >= 0 and total_bytes >= max_bytes:
        reason = f"retained Codex session byte limit reached: {total_bytes}"
    elif (
        (unaccounted or inventory.oversize or inventory.unsafe)
        and max_unlinked_age_s >= 0
        and oldest_age >= max_unlinked_age_s
    ):
        reason = f"unlinked or unparseable Codex session age limit reached: {oldest_age} seconds"
    return SessionRetentionStatus(
        files=total_files,
        bytes=total_bytes,
        unlinked_files=len(unaccounted) + len(inventory.oversize) + len(inventory.unsafe),
        unlinked_bytes=unlinked_bytes,
        unparseable_files=len(inventory.unparseable),
        oversize_files=len(inventory.oversize),
        oversize_bytes=sum(entry.bytes for entry in inventory.oversize),
        unsafe_files=len(inventory.unsafe),
        unsafe_bytes=sum(entry.bytes for entry in inventory.unsafe),
        oldest_unlinked_age_s=oldest_age,
        active_files=active_files,
        over_limit=reason is not None,
        reason=reason,
    )


def _read_session_metadata_no_follow(
    path: Path,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    descriptor = (
        _open_retained_file_no_follow(root=root, path=path)
        if root is not None
        else os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    )
    if descriptor is None:
        raise RuntimeError(f"session source disappeared: {path}")
    with os.fdopen(descriptor, errors="replace") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise RuntimeError(f"session source is not a regular file: {path}")
        first_line = handle.readline(_MAX_SESSION_METADATA_BYTES + 1)
        if len(first_line.encode(errors="replace")) > _MAX_SESSION_METADATA_BYTES:
            raise RuntimeError(f"session metadata line is too large: {path}")
        first = json.loads(first_line)
    metadata = first.get("payload") if isinstance(first, dict) else None
    return metadata if isinstance(metadata, dict) else None


def _walk_session_store_no_follow(
    root: Path,
) -> tuple[list[_RetainedSessionEntry], list[_RetainedSessionEntry]]:
    """Enumerate the session store through stable directory descriptors."""
    try:
        root_fd = open_absolute_directory_no_follow(root)
    except RuntimeError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return [], []
        return [], [_RetainedSessionEntry(path=root, bytes=0, mtime=0)]
    regular: list[_RetainedSessionEntry] = []
    unsafe: list[_RetainedSessionEntry] = []

    def walk(directory_fd: int, directory_path: Path) -> None:
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError:
            unsafe.append(_RetainedSessionEntry(path=directory_path, bytes=0, mtime=0))
            return
        for name in names:
            try:
                validate_child_name(name)
                entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except (OSError, RuntimeError):
                unsafe.append(_RetainedSessionEntry(path=directory_path / name, bytes=0, mtime=0))
                continue
            child_path = directory_path / name
            retained = _RetainedSessionEntry(
                path=child_path,
                bytes=int(entry.st_size),
                mtime=int(entry.st_mtime),
            )
            if stat.S_ISREG(entry.st_mode):
                regular.append(retained)
                continue
            if not stat.S_ISDIR(entry.st_mode):
                unsafe.append(retained)
                continue
            try:
                child_fd = os.open(name, directory_open_flags(), dir_fd=directory_fd)
                opened = os.fstat(child_fd)
                if not stat.S_ISDIR(opened.st_mode) or not _same_inode(entry, opened):
                    os.close(child_fd)
                    unsafe.append(retained)
                    continue
            except OSError:
                unsafe.append(retained)
                continue
            try:
                walk(child_fd, child_path)
            finally:
                os.close(child_fd)

    try:
        walk(root_fd, root)
    finally:
        os.close(root_fd)
    regular.sort(key=lambda entry: str(entry.path))
    unsafe.sort(key=lambda entry: str(entry.path))
    return regular, unsafe


def _lstat_regular_size(path: Path) -> int:
    entry = path.lstat()
    return entry.st_size if stat.S_ISREG(entry.st_mode) else 0


def _ledger_worktree_paths(ledger_path: Path) -> dict[str, Path]:
    if not ledger_path.is_file():
        return {}
    with sqlite3.connect(ledger_path) as conn:
        try:
            rows = conn.execute(
                "SELECT run_id, worktree_path FROM runs WHERE worktree_path IS NOT NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
    return {
        str(run_id): Path(str(worktree_path))
        for run_id, worktree_path in rows
        if run_id and worktree_path
    }


def _ledger_run_ids(ledger_path: Path) -> set[str]:
    if not ledger_path.is_file():
        return set()
    with sqlite3.connect(ledger_path) as conn:
        try:
            rows = conn.execute("SELECT run_id FROM runs").fetchall()
        except sqlite3.OperationalError:
            return set()
    return {str(row[0]) for row in rows if row[0]}


def _run_id_for_cwd(cwd: str, run_worktrees: dict[str, Path]) -> str | None:
    if not cwd:
        return None
    absolute_cwd = Path(os.path.abspath(cwd))
    matches = [
        run_id
        for run_id, worktree in run_worktrees.items()
        if absolute_cwd.is_relative_to(Path(os.path.abspath(worktree)))
    ]
    if len(matches) == 1:
        return matches[0]
    return None


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


def _redact_projected_files(
    output_dir: Path,
    file_entries: list[dict[str, Any]],
) -> list[dict[str, int | str]]:
    """Redact projected files and refresh their manifest checksums."""
    redactions: list[dict[str, int | str]] = []
    for entry in file_entries:
        relative_path = str(entry["path"])
        path = output_dir / relative_path
        original = path.read_text(errors="replace")
        redacted, findings = redact_credentials(original)
        if redacted != original:
            path.write_text(redacted)
        entry["sha256"] = _sha256(path)
        entry["bytes"] = path.stat().st_size
        redactions.extend({"path": relative_path, **finding} for finding in findings)
    return redactions


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


def _snapshot_source(
    *,
    root: Path,
    path: Path,
    destination: Path,
    max_bytes: int | None = None,
) -> _SourceSnapshot | None:
    """Copy one source from a no-follow descriptor and hash those exact bytes."""
    file_fd = _open_retained_file_no_follow(root=root, path=path)
    if file_fd is None:
        return None
    digest = hashlib.sha256()
    copied = 0
    try:
        opened = os.fstat(file_fd)
        if max_bytes is not None and opened.st_size > max_bytes:
            raise RuntimeError(f"retained source exceeds {max_bytes} bytes: {path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as target:
            while True:
                chunk = os.read(file_fd, 1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if max_bytes is not None and copied > max_bytes:
                    raise RuntimeError(f"retained source exceeds {max_bytes} bytes: {path}")
                target.write(chunk)
                digest.update(chunk)
        final_stat = os.fstat(file_fd)
        if not _same_inode(opened, final_stat):
            raise RuntimeError(f"retained source changed while snapshotting: {path}")
    finally:
        os.close(file_fd)
    return _SourceSnapshot(
        original_path=path,
        path=destination,
        sha256=digest.hexdigest(),
        bytes=copied,
    )


def _open_retained_file_no_follow(*, root: Path, path: Path) -> int | None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"retained source escapes its configured root: {path}") from exc
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"invalid retained source path: {path}")
    try:
        parent_fd = open_absolute_directory_no_follow(root)
    except RuntimeError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return None
        raise
    try:
        for part in parts[:-1]:
            validate_child_name(part)
            try:
                next_fd = os.open(part, directory_open_flags(), dir_fd=parent_fd)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise RuntimeError(f"retained source parent is unsafe: {path}: {exc}") from exc
            os.close(parent_fd)
            parent_fd = next_fd
        name = parts[-1]
        validate_child_name(name)
        try:
            expected = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            file_fd = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RuntimeError(f"retained source is unsafe: {path}: {exc}") from exc
        opened = os.fstat(file_fd)
        if (
            not stat.S_ISREG(expected.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or not _same_inode(expected, opened)
        ):
            os.close(file_fd)
            raise RuntimeError(f"retained source changed while opening: {path}")
        return file_fd
    finally:
        os.close(parent_fd)


def build_bundle(
    *,
    run_id: str,
    runner_root: Path,
    codex_home: Path,
    output_dir: Path,
    sessions: list[SessionSource] | None = None,
    traces_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> dict[str, Any]:
    """Build one bundle from immutable, descriptor-opened source snapshots."""
    created = Path(tempfile.mkdtemp(prefix=f"trace-source-snapshots-{run_id}-"))
    created_parent = created.parent.resolve(strict=True)
    snapshot_dir = created.resolve(strict=True)
    if snapshot_dir.parent != created_parent or not snapshot_dir.is_dir():
        raise RuntimeError("temporary source snapshot root escaped its canonical parent")
    try:
        return _build_bundle_from_snapshots(
            run_id=run_id,
            runner_root=runner_root,
            codex_home=codex_home,
            output_dir=output_dir,
            snapshot_dir=snapshot_dir,
            sessions=sessions,
            traces_dir=traces_dir,
            logs_dir=logs_dir,
        )
    finally:
        safe_rmtree_child(snapshot_dir.parent, snapshot_dir.name, missing_ok=True)


def _build_bundle_from_snapshots(
    *,
    run_id: str,
    runner_root: Path,
    codex_home: Path,
    output_dir: Path,
    snapshot_dir: Path,
    sessions: list[SessionSource] | None,
    traces_dir: Path | None,
    logs_dir: Path | None,
) -> dict[str, Any]:
    ledger_path = runner_root / "state" / "ledger.sqlite"
    run = _ledger_run(ledger_path, run_id)
    sessions = (
        sessions
        if sessions is not None
        else discover_sessions(codex_home, run_id, ledger_path=ledger_path)
    )
    run_worktrees = _ledger_worktree_paths(ledger_path)
    session_snapshots: list[tuple[SessionSource, SessionSource, _SourceSnapshot]] = []
    for index, source in enumerate(sessions):
        snapshot = _snapshot_source(
            root=codex_home / "sessions",
            path=source.path,
            destination=snapshot_dir / f"session-{index}.jsonl",
            max_bytes=_MAX_SESSION_SOURCE_BYTES,
        )
        if snapshot is None:
            raise RuntimeError(f"Codex session disappeared before snapshot: {source.path}")
        metadata = _read_session_metadata_no_follow(snapshot.path)
        if metadata is None:
            raise RuntimeError(f"Codex session metadata is invalid: {source.path}")
        cwd = str(metadata.get("cwd") or "")
        mapped_run_id = _run_id_for_cwd(cwd, run_worktrees)
        if mapped_run_id is None:
            match = _RUN_ID_FROM_CWD_RE.search(cwd)
            mapped_run_id = match.group("run_id") if match else None
        if mapped_run_id != run_id:
            raise RuntimeError(f"Codex session no longer belongs to {run_id}: {source.path}")
        session_snapshots.append(
            (
                source,
                SessionSource(path=snapshot.path, metadata=metadata),
                snapshot,
            )
        )
    snapped_sessions = [item[1] for item in session_snapshots]
    roots = [session for session in snapped_sessions if session.is_root]
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

    for original_source, source, snapshot in session_snapshots:
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
                "source_path": str(original_source.path),
                "source_sha256": snapshot.sha256,
                "source_bytes": snapshot.bytes,
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
    trace_snapshot = (
        _snapshot_source(
            root=traces_dir or runner_root / "traces",
            path=trace_path,
            destination=snapshot_dir / "codex-exec.jsonl",
        )
        if trace_path
        else None
    )
    if trace_snapshot is not None:
        destination = output_dir / "codex-exec.jsonl"
        trace_summary = _project_codex_exec(trace_snapshot.path, destination)
        file_entries.append(
            {
                "path": destination.name,
                "role": "codex_exec",
                "source_path": str(trace_snapshot.original_path),
                "source_sha256": trace_snapshot.sha256,
                "source_bytes": trace_snapshot.bytes,
                "sha256": _sha256(destination),
                "bytes": destination.stat().st_size,
                "records": trace_summary["records"],
            }
        )

    stderr_path = Path(run["stderr_path"]) if run.get("stderr_path") else None
    stderr_summary = None
    stderr_snapshot = (
        _snapshot_source(
            root=logs_dir or runner_root / "logs",
            path=stderr_path,
            destination=snapshot_dir / "runner-stderr.log",
        )
        if stderr_path
        else None
    )
    if stderr_snapshot is not None:
        destination = output_dir / "runner-stderr.log"
        stderr_text = _normalize_string(stderr_snapshot.path.read_text(errors="replace"))
        destination.write_text(stderr_text)
        stderr_summary = {
            "bytes": destination.stat().st_size,
            "lines": len(stderr_text.splitlines()),
        }
        file_entries.append(
            {
                "path": destination.name,
                "role": "runner_stderr",
                "source_path": str(stderr_snapshot.original_path),
                "source_sha256": stderr_snapshot.sha256,
                "source_bytes": stderr_snapshot.bytes,
                "sha256": _sha256(destination),
                "bytes": destination.stat().st_size,
                "records": stderr_summary["lines"],
            }
        )

    credential_redactions = _redact_projected_files(output_dir, file_entries)
    if stderr_summary is not None:
        stderr_summary["bytes"] = (output_dir / "runner-stderr.log").stat().st_size

    root_id = root.thread_id
    structural_errors = _session_tree_errors(snapped_sessions, root_id)
    if run.get("state") not in _EXPORTABLE_STATES:
        structural_errors.append(f"run state {run.get('state')!r} is not terminal")

    missing_contracts = [
        projection.source.thread_id
        for projection in projections
        if run_id.startswith("issue-")
        and not projection.source.is_root
        and not projection.task_contract
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
            "credential_redactions": credential_redactions,
        },
        "codex_exec": trace_summary,
        "runner_stderr": stderr_summary,
        "trajectory_path": trajectory_path.name,
    }

    manifest_path = output_dir / "manifest.json"
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    sanitized_manifest_text, manifest_redactions = redact_credentials(manifest_text)
    if manifest_redactions:
        manifest = json.loads(sanitized_manifest_text)
        credential_redactions.extend(
            {"path": "manifest.json", **finding} for finding in manifest_redactions
        )
        manifest["quality"]["credential_redactions"] = credential_redactions
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


def prune_hf_dataset_cache(
    *,
    repo_id: str,
    cache_dir: Path | None = None,
    execute: bool = True,
) -> dict[str, Any]:
    """Remove only cached revisions for the already-durable trace dataset."""
    from huggingface_hub import scan_cache_dir

    cache = scan_cache_dir(cache_dir=cache_dir)
    revisions = sorted(
        revision.commit_hash
        for repo in cache.repos
        if repo.repo_type == "dataset" and repo.repo_id == repo_id
        for revision in repo.revisions
    )
    if not revisions:
        return {"repo_id": repo_id, "revisions": 0, "reclaimed_bytes": 0}
    strategy = cache.delete_revisions(*revisions)
    reclaimed_bytes = int(strategy.expected_freed_size)
    if execute:
        strategy.execute()
    return {
        "repo_id": repo_id,
        "revisions": len(revisions),
        "reclaimed_bytes": reclaimed_bytes,
    }


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
    retained_bytes: int = 0,
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
                retained_bytes INTEGER NOT NULL DEFAULT 0,
                last_attempt_at INTEGER NOT NULL
            )
            """
        )
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(trace_bundle_export_attempts)")
        }
        if "retained_bytes" not in columns:
            conn.execute(
                "ALTER TABLE trace_bundle_export_attempts "
                "ADD COLUMN retained_bytes INTEGER NOT NULL DEFAULT 0"
            )
        conn.execute(
            """
            INSERT INTO trace_bundle_export_attempts (
                run_id, attempts, status, quality_tier,
                remote_dir, error, retained_bytes, last_attempt_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                attempts = trace_bundle_export_attempts.attempts + 1,
                status = excluded.status,
                quality_tier = excluded.quality_tier,
                remote_dir = excluded.remote_dir,
                error = excluded.error,
                retained_bytes = excluded.retained_bytes,
                last_attempt_at = excluded.last_attempt_at
            """,
            (run_id, status, quality_tier, remote_dir, error, retained_bytes, now),
        )


def _backfill_run_ids(ledger_path: Path, *, limit: int | None) -> list[str]:
    with sqlite3.connect(ledger_path) as conn:
        conn.row_factory = sqlite3.Row
        export_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trace_bundle_exports'"
        ).fetchone()
        if export_table:
            query = f"""
                SELECT r.run_id
                FROM runs AS r
                LEFT JOIN trace_bundle_exports AS e ON e.run_id = r.run_id
                WHERE r.state IN (
                    'completed', 'failed', 'timeout',
                    'submitted', 'rejected', 'escalated',
                    'retryable', 'interrupted'
                )
                  AND {_AUTOMATION_RUN_SQL.replace("run_id", "r.run_id")}
                  AND e.cleaned_at IS NULL
                ORDER BY r.created_at, r.run_id
            """
        else:
            query = f"""
                SELECT run_id FROM runs
                WHERE state IN (
                    'completed', 'failed', 'timeout',
                    'submitted', 'rejected', 'escalated',
                    'retryable', 'interrupted'
                )
                  AND {_AUTOMATION_RUN_SQL}
                ORDER BY created_at, run_id
            """
        rows = conn.execute(query).fetchall()
    run_ids = [str(row["run_id"]) for row in rows]
    return run_ids[:limit] if limit is not None else run_ids


def _files_under(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [path for path in sorted(root.rglob("*")) if path.is_file() and not path.is_symlink()]


def _directory_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in _files_under(root))


def _storage_totals(paths: list[Path]) -> dict[str, int]:
    return {"files": len(paths), "bytes": sum(path.stat().st_size for path in paths)}


def _default_hf_cache_dir(codex_home: Path) -> Path:
    configured = os.environ.get("HF_HUB_CACHE")
    if configured:
        return Path(configured)
    hf_home = os.environ.get("HF_HOME")
    if hf_home:
        return Path(hf_home) / "hub"
    return codex_home.parent / ".cache" / "huggingface" / "hub"


def trace_export_report(
    *,
    runner_root: Path,
    codex_home: Path,
    include_files: bool = False,
    min_disk_free_gib: float = 5.0,
    disk_alert_margin_gib: float = 2.0,
    max_quarantine_runs: int = 50,
    max_quarantine_gib: float = 2.0,
    max_retained_session_files: int = 500,
    max_retained_session_gib: float = 2.0,
    max_unlinked_session_age_days: float = 7.0,
) -> dict[str, Any]:
    """Reconcile durable exports with every local retention category."""
    ledger_path = runner_root / "state" / "ledger.sqlite"
    inventory = inventory_automation_sessions(codex_home, ledger_path=ledger_path)
    sessions = inventory.by_run
    pending_ids = set(_backfill_run_ids(ledger_path, limit=None))

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
        run_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        optional_run_columns = ["trace_path", "stderr_path", "worktree_path"]
        run_select = ", ".join(
            name if name in run_columns else f"NULL AS {name}" for name in optional_run_columns
        )
        terminal_rows = conn.execute(
            f"SELECT run_id, state, {run_select} FROM runs "
            f"WHERE state IN ({placeholders}) AND {_AUTOMATION_RUN_SQL}",
            terminal_states,
        ).fetchall()
        automation_rows = conn.execute(
            f"SELECT run_id, state FROM runs WHERE {_AUTOMATION_RUN_SQL}"
        ).fetchall()
        all_worktree_rows = (
            conn.execute(
                "SELECT run_id, state, worktree_path FROM runs WHERE worktree_path IS NOT NULL"
            ).fetchall()
            if "worktree_path" in run_columns
            else []
        )
        export_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'trace_bundle_exports'"
        ).fetchone()
        attempt_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'trace_bundle_export_attempts'"
        ).fetchone()
        exports = []
        export_rows: list[sqlite3.Row] = []
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
                    f"WHERE {_AUTOMATION_RUN_SQL} "
                    "GROUP BY schema_version, quality_tier"
                )
            ]
            export_rows = conn.execute(
                "SELECT run_id, quality_tier, remote_dir, source_bytes, "
                "projected_bytes, cleaned_at, "
                + (
                    "source_files_json "
                    if "source_files_json" in export_columns
                    else "'{}' AS source_files_json "
                )
                + "FROM trace_bundle_exports "
                f"WHERE {_AUTOMATION_RUN_SQL}"
            ).fetchall()
        attempt_rows: list[sqlite3.Row] = []
        if attempt_table:
            attempt_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(trace_bundle_export_attempts)")
            }
            retained_select = (
                "retained_bytes" if "retained_bytes" in attempt_columns else "0 AS retained_bytes"
            )
            attempt_rows = conn.execute(
                "SELECT run_id, status, quality_tier, error, "
                f"{retained_select} FROM trace_bundle_export_attempts "
                f"WHERE {_AUTOMATION_RUN_SQL}"
            ).fetchall()
        attempts = (
            {
                str(row["status"]): int(row["runs"])
                for row in conn.execute(
                    "SELECT a.status, COUNT(*) AS runs "
                    "FROM trace_bundle_export_attempts AS a "
                    "JOIN runs AS r ON r.run_id = a.run_id "
                    "WHERE "
                    f"{_AUTOMATION_RUN_SQL.replace('run_id', 'r.run_id')} "
                    "GROUP BY a.status"
                )
            }
            if attempt_table
            else {}
        )
        exported_ids = (
            {
                str(row["run_id"])
                for row in conn.execute(
                    f"SELECT run_id FROM trace_bundle_exports WHERE {_AUTOMATION_RUN_SQL}"
                )
            }
            if export_table
            else set()
        )
        attempted_ids = (
            {
                str(row["run_id"])
                for row in conn.execute(
                    f"SELECT run_id FROM trace_bundle_export_attempts WHERE {_AUTOMATION_RUN_SQL}"
                )
            }
            if attempt_table
            else set()
        )

    terminal_ids = {str(row["run_id"]) for row in terminal_rows}
    run_states = {str(row["run_id"]): str(row["state"]) for row in automation_rows}
    exports_by_run = {str(row["run_id"]): dict(row) for row in export_rows}
    attempts_by_run = {str(row["run_id"]): dict(row) for row in attempt_rows}

    def retention_reason(run_id: str, path: Path) -> tuple[str, bool]:
        export = exports_by_run.get(run_id)
        if export and export.get("cleaned_at") is None:
            source_files = json.loads(str(export.get("source_files_json") or "{}"))
            if isinstance(source_files, dict) and str(path) in source_files:
                return "verified_pending_checksum_gated_cleanup", True
            return "verified_but_source_inventory_missing", False
        if export:
            return "verified_cleanup_residue", False
        attempt = attempts_by_run.get(run_id)
        if attempt:
            return f"{attempt['status']}_retained", False
        return "unaccounted_retained", False

    inventory_by_path: dict[Path, dict[str, Any]] = {}

    def add_source(path: Path, *, category: str, run_id: str) -> None:
        if not path.is_file() or path.is_symlink():
            return
        reason, cleanup_candidate = retention_reason(run_id, path)
        inventory_by_path[path] = {
            "path": str(path),
            "category": category,
            "run_id": run_id,
            "bytes": path.stat().st_size,
            "reason": reason,
            "cleanup_candidate": cleanup_candidate,
        }

    for run_id in terminal_ids:
        for source in sessions.get(run_id, []):
            add_source(source.path, category="codex_session", run_id=run_id)
    for row in terminal_rows:
        run_id = str(row["run_id"])
        for category, raw in (
            ("canonical_trace", row["trace_path"]),
            ("stderr_log", row["stderr_path"]),
        ):
            if isinstance(raw, str):
                add_source(Path(raw), category=category, run_id=run_id)

    all_session_paths = list(inventory.all_files)
    oversize_session_paths = {entry.path for entry in inventory.oversize}
    session_run_by_path = {
        source.path: run_id for run_id, sources in sessions.items() for source in sources
    }
    all_trace_paths = _files_under(runner_root / "traces")
    all_log_paths = _files_under(runner_root / "logs")
    for category, paths in (
        ("codex_session", all_session_paths),
        ("canonical_trace", all_trace_paths),
        ("stderr_log", all_log_paths),
    ):
        for path in paths:
            if path in inventory_by_path:
                continue
            linked_run_id = session_run_by_path.get(path) if category == "codex_session" else None
            inventory_by_path[path] = {
                "path": str(path),
                "category": category,
                "run_id": linked_run_id,
                "bytes": path.stat().st_size,
                "reason": (
                    "active_automation_session_retained"
                    if linked_run_id and run_states.get(linked_run_id) in {"claimed", "running"}
                    else "oversize_session_retained"
                    if path in oversize_session_paths
                    else "unparseable_session_retained"
                    if path in inventory.unparseable
                    else "unlinked_automation_session_retained"
                    if path in inventory.unlinked
                    else "unlinked_runner_artifact_retained"
                ),
                "cleanup_candidate": False,
            }
    for entry in inventory.unsafe:
        inventory_by_path[entry.path] = {
            "path": str(entry.path),
            "category": "codex_session",
            "run_id": None,
            "bytes": entry.bytes,
            "reason": "unsafe_session_entry_retained",
            "cleanup_candidate": False,
        }
    source_inventory = sorted(inventory_by_path.values(), key=lambda item: item["path"])
    reasons: dict[str, dict[str, int]] = {}
    for item in source_inventory:
        reason = str(item["reason"])
        summary = reasons.setdefault(reason, {"files": 0, "bytes": 0})
        summary["files"] += 1
        summary["bytes"] += int(item["bytes"])

    hf_cache_dir = _default_hf_cache_dir(codex_home)
    hf_cache_paths = _files_under(hf_cache_dir)
    worktree_root = runner_root / "worktrees"
    worktree_paths = (
        [path for path in sorted(worktree_root.iterdir()) if path.is_dir()]
        if worktree_root.is_dir()
        else []
    )
    run_by_worktree = {
        str(row["worktree_path"]): (str(row["run_id"]), str(row["state"]))
        for row in all_worktree_rows
        if isinstance(row["worktree_path"], str)
    }
    worktrees = []
    for path in worktree_paths:
        run = run_by_worktree.get(str(path))
        worktrees.append(
            {
                "path": str(path),
                "bytes": _directory_bytes(path),
                "run_id": run[0] if run else None,
                "reason": (
                    "active_worktree"
                    if run and run[1] in {"claimed", "running"}
                    else "terminal_debug_worktree"
                    if run
                    else "orphan_worktree"
                ),
                "cleanup_candidate": False,
            }
        )

    quarantine_run_ids = {
        run_id
        for run_id, attempt in attempts_by_run.items()
        if attempt.get("status") == "quarantined"
    }
    quarantine_local_bytes = sum(
        int(item["bytes"]) for item in source_inventory if item.get("run_id") in quarantine_run_ids
    )
    quarantine_recorded_bytes = sum(
        int(attempts_by_run[run_id].get("retained_bytes") or 0) for run_id in quarantine_run_ids
    )
    disk = shutil.disk_usage(runner_root)
    min_disk_free_bytes = int(min_disk_free_gib * 1024**3)
    alert_margin_bytes = int(disk_alert_margin_gib * 1024**3)
    max_quarantine_bytes = int(max_quarantine_gib * 1024**3)
    session_status = session_retention_status(
        runner_root=runner_root,
        codex_home=codex_home,
        max_files=max_retained_session_files,
        max_bytes=int(max_retained_session_gib * 1024**3),
        max_unlinked_age_s=int(max_unlinked_session_age_days * 24 * 60 * 60),
    )
    alerts: list[dict[str, Any]] = []
    if disk.free < min_disk_free_bytes + alert_margin_bytes:
        alerts.append(
            {
                "kind": "disk_headroom",
                "severity": "critical" if disk.free < min_disk_free_bytes else "warning",
                "disk_free_bytes": disk.free,
                "admission_floor_bytes": min_disk_free_bytes,
            }
        )
    if (
        len(quarantine_run_ids) >= max_quarantine_runs
        or max(quarantine_local_bytes, quarantine_recorded_bytes) >= max_quarantine_bytes
    ):
        alerts.append(
            {
                "kind": "quarantine_limit",
                "severity": "critical",
                "runs": len(quarantine_run_ids),
                "bytes": max(quarantine_local_bytes, quarantine_recorded_bytes),
            }
        )
    unaccounted_runs = len(terminal_ids - exported_ids - attempted_ids)
    if unaccounted_runs:
        alerts.append(
            {"kind": "unaccounted_runs", "severity": "critical", "runs": unaccounted_runs}
        )
    if session_status.over_limit:
        alerts.append(
            {
                "kind": "session_retention_limit",
                "severity": "critical",
                "files": session_status.files,
                "bytes": session_status.bytes,
                "unlinked_files": session_status.unlinked_files,
                "oversize_files": session_status.oversize_files,
                "oldest_unlinked_age_s": session_status.oldest_unlinked_age_s,
                "reason": session_status.reason,
            }
        )

    cleaned_source_bytes = sum(
        int(row["source_bytes"] or 0) for row in export_rows if row["cleaned_at"] is not None
    )
    deleted_files = []
    cleaned_exports_missing_source_inventory = 0
    for row in export_rows:
        if row["cleaned_at"] is None:
            continue
        source_files = json.loads(str(row["source_files_json"] or "{}"))
        if not isinstance(source_files, dict) or not source_files:
            cleaned_exports_missing_source_inventory += 1
            continue
        for path, details in source_files.items():
            if not isinstance(details, dict):
                continue
            deleted_files.append(
                {
                    "path": str(path),
                    "run_id": str(row["run_id"]),
                    "bytes": int(details.get("bytes") or 0),
                    "sha256": str(details.get("sha256") or ""),
                    "bundle_path": str(details.get("bundle_path") or ""),
                    "remote_dir": str(row["remote_dir"]),
                }
            )
    if cleaned_exports_missing_source_inventory:
        alerts.append(
            {
                "kind": "legacy_cleaned_inventory_missing",
                "severity": "warning",
                "runs": cleaned_exports_missing_source_inventory,
            }
        )
    report: dict[str, Any] = {
        "terminal_runs": len(terminal_ids),
        "pending_runs": len(pending_ids),
        "unaccounted_runs": unaccounted_runs,
        "retained_session_files": session_status.files,
        "retained_session_bytes": session_status.bytes,
        "retained_runner_files": len(all_trace_paths) + len(all_log_paths),
        "retained_runner_bytes": sum(
            path.stat().st_size for path in [*all_trace_paths, *all_log_paths]
        ),
        "storage": {
            "codex_sessions": {
                "files": session_status.files,
                "bytes": session_status.bytes,
            },
            "canonical_traces": _storage_totals(all_trace_paths),
            "stderr_logs": _storage_totals(all_log_paths),
            "huggingface_cache": {
                "path": str(hf_cache_dir),
                **_storage_totals(hf_cache_paths),
            },
            "worktrees": {
                "directories": len(worktrees),
                "bytes": sum(int(item["bytes"]) for item in worktrees),
            },
        },
        "retention_reasons": reasons,
        "quarantine": {
            "runs": len(quarantine_run_ids),
            "local_bytes": quarantine_local_bytes,
            "recorded_bytes": quarantine_recorded_bytes,
            "max_runs": max_quarantine_runs,
            "max_bytes": max_quarantine_bytes,
        },
        "session_retention": {
            **asdict(session_status),
            "max_files": max_retained_session_files,
            "max_bytes": int(max_retained_session_gib * 1024**3),
            "max_unlinked_age_s": int(max_unlinked_session_age_days * 24 * 60 * 60),
        },
        "cleaned_source_bytes": cleaned_source_bytes,
        "deleted_source_files": len(deleted_files),
        "cleaned_exports_missing_source_inventory": (cleaned_exports_missing_source_inventory),
        "exports_by_tier": exports,
        "attempts_by_status": attempts,
        "disk_free_bytes": disk.free,
        "disk_total_bytes": disk.total,
        "disk_admission_floor_bytes": min_disk_free_bytes,
        "disk_headroom_above_floor_bytes": disk.free - min_disk_free_bytes,
        "alerts": alerts,
    }
    if include_files:
        report["files"] = source_inventory
        report["worktrees"] = worktrees
        report["cleanup_candidates"] = [
            item for item in source_inventory if item["cleanup_candidate"]
        ]
        report["deleted_files"] = sorted(deleted_files, key=lambda item: item["path"])
    return report


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
    """Export retained Jobseek automation runs in verified bounded batches."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    ledger_path = runner_root / "state" / "ledger.sqlite"
    session_index = index_automation_sessions(codex_home, ledger_path=ledger_path)
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
        "credential_redactions": 0,
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
                manifest: dict[str, Any] | None = None
                try:
                    manifest = build_bundle(
                        run_id=run_id,
                        runner_root=runner_root,
                        codex_home=codex_home,
                        output_dir=building,
                        sessions=session_index[run_id],
                    )
                    summary["credential_redactions"] += len(
                        manifest["quality"].get("credential_redactions") or []
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
                            retained_bytes=_manifest_source_bytes(manifest),
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
                        retained_bytes=(
                            _manifest_source_bytes(manifest) if manifest is not None else 0
                        ),
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
                        retained_bytes=_manifest_source_bytes(manifest),
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
                            runner_root=runner_root,
                            codex_home=codex_home,
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
                        retained_bytes=(0 if cleanup else _manifest_source_bytes(manifest)),
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
                        retained_bytes=_manifest_source_bytes(manifest),
                    )
    if cleanup:
        try:
            summary["huggingface_cache_cleanup"] = prune_hf_dataset_cache(repo_id=repo_id)
        except Exception as exc:  # noqa: BLE001 - cache is non-canonical
            summary["huggingface_cache_cleanup"] = {"error": _safe_error(exc)}
    disk = shutil.disk_usage(runner_root)
    summary["disk_free_bytes"] = disk.free
    summary["disk_total_bytes"] = disk.total
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
                source_files_json TEXT NOT NULL DEFAULT '{}',
                cleanup_claims_json TEXT NOT NULL DEFAULT '{}',
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
            "source_files_json": (
                "ALTER TABLE trace_bundle_exports "
                "ADD COLUMN source_files_json TEXT NOT NULL DEFAULT '{}'"
            ),
            "cleanup_claims_json": (
                "ALTER TABLE trace_bundle_exports "
                "ADD COLUMN cleanup_claims_json TEXT NOT NULL DEFAULT '{}'"
            ),
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
        source_files = {
            str(entry["source_path"]): {
                "sha256": str(entry["source_sha256"]),
                "bytes": int(entry.get("source_bytes", 0)),
                "bundle_path": str(entry["path"]),
            }
            for entry in manifest["files"]
            if entry.get("source_path") and entry.get("source_sha256")
        }
        conn.execute(
            """
            INSERT INTO trace_bundle_exports (
                run_id, schema_version, quality_tier, remote_dir,
                bundle_content_sha256, verified_files_json, source_files_json,
                source_bytes, projected_bytes, thread_count, subagent_count,
                verified_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                schema_version = excluded.schema_version,
                quality_tier = excluded.quality_tier,
                remote_dir = excluded.remote_dir,
                bundle_content_sha256 = excluded.bundle_content_sha256,
                verified_files_json = excluded.verified_files_json,
                source_files_json = excluded.source_files_json,
                source_bytes = excluded.source_bytes,
                projected_bytes = excluded.projected_bytes,
                thread_count = excluded.thread_count,
                subagent_count = excluded.subagent_count,
                verified_at = excluded.verified_at,
                cleanup_claims_json = '{}',
                cleaned_at = NULL
            """,
            (
                run_id,
                manifest["schema_version"],
                manifest["quality"]["tier"],
                remote_dir,
                manifest["bundle_content_sha256"],
                json.dumps(verified, sort_keys=True),
                json.dumps(source_files, sort_keys=True),
                source_bytes,
                projected_bytes,
                manifest["thread_count"],
                manifest["subagent_count"],
                now,
            ),
        )


def pending_verified_cleanup(
    *,
    ledger_path: Path,
    run_id: str,
) -> tuple[dict[str, Any], str, str, int] | None:
    """Reconstruct a cleanup manifest without requiring deleted source sessions."""
    with sqlite3.connect(ledger_path) as conn:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM trace_bundle_exports WHERE run_id = ? AND cleaned_at IS NULL",
                (run_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    if row is None:
        return None
    verified_files = json.loads(str(row["verified_files_json"]))
    source_files = json.loads(str(row["source_files_json"]))
    if not isinstance(verified_files, dict) or not isinstance(source_files, dict):
        raise RuntimeError("verified cleanup inventory is invalid")
    if not source_files:
        raise RuntimeError("verified cleanup source inventory is empty")
    remote_dir = str(row["remote_dir"])
    files: list[dict[str, Any]] = []
    retained_bytes = 0
    for source_path, details in sorted(source_files.items()):
        if not isinstance(source_path, str) or not isinstance(details, dict):
            raise RuntimeError("verified cleanup source inventory is invalid")
        bundle_path = details.get("bundle_path")
        source_sha256 = details.get("sha256")
        source_bytes = details.get("bytes")
        if (
            not isinstance(bundle_path, str)
            or not isinstance(source_sha256, str)
            or not isinstance(source_bytes, int)
        ):
            raise RuntimeError("verified cleanup source entry is invalid")
        projected_sha256 = verified_files.get(f"{remote_dir.rstrip('/')}/{bundle_path}")
        if not isinstance(projected_sha256, str):
            raise RuntimeError("verified cleanup projected object is missing")
        files.append(
            {
                "path": bundle_path,
                "role": (
                    "codex_exec"
                    if bundle_path == "codex-exec.jsonl"
                    else "runner_stderr"
                    if bundle_path == "runner-stderr.log"
                    else "codex_session"
                ),
                "sha256": projected_sha256,
                "source_path": source_path,
                "source_sha256": source_sha256,
                "source_bytes": source_bytes,
            }
        )
        retained_bytes += source_bytes
    quality_tier = str(row["quality_tier"])
    manifest = {
        "bundle_content_sha256": str(row["bundle_content_sha256"]),
        "quality": {"tier": quality_tier},
        "files": files,
    }
    return manifest, quality_tier, remote_dir, retained_bytes


def cleanup_verified_sources(
    *,
    ledger_path: Path,
    run_id: str,
    manifest: dict[str, Any],
    runner_root: Path,
    codex_home: Path,
    traces_dir: Path | None = None,
    logs_dir: Path | None = None,
) -> dict[str, Any]:
    conn = sqlite3.connect(ledger_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            str(column[1]) for column in conn.execute("PRAGMA table_info(trace_bundle_exports)")
        }
        if "cleanup_claims_json" not in columns:
            conn.execute(
                "ALTER TABLE trace_bundle_exports "
                "ADD COLUMN cleanup_claims_json TEXT NOT NULL DEFAULT '{}'"
            )
            conn.commit()
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
    recorded_sources = json.loads(row["source_files_json"])
    if not isinstance(recorded_sources, dict):
        raise RuntimeError("verified source inventory is invalid")
    manifest_sources = {
        str(entry["source_path"]): {
            "sha256": str(entry["source_sha256"]),
            "bytes": int(entry.get("source_bytes", 0)),
            "bundle_path": str(entry["path"]),
        }
        for entry in manifest["files"]
        if entry.get("source_path") and entry.get("source_sha256")
    }
    if recorded_sources != manifest_sources:
        raise RuntimeError("verified source inventory does not match current manifest")
    for entry in manifest["files"]:
        remote_path = f"{row['remote_dir'].rstrip('/')}/{entry['path']}"
        if verified_files.get(remote_path) != entry["sha256"]:
            raise RuntimeError(f"projected object was not checksum-verified: {entry['path']}")

    cleanup_claims = json.loads(row["cleanup_claims_json"])
    if not isinstance(cleanup_claims, dict):
        raise RuntimeError("verified cleanup claim inventory is invalid")
    if any(path not in manifest_sources for path in cleanup_claims):
        raise RuntimeError("verified cleanup claim inventory contains an unknown source")
    for source_path in manifest_sources:
        claim_name = cleanup_claims.get(source_path)
        if claim_name is None:
            cleanup_claims[source_path] = f".jobseek-cleanup-{uuid.uuid4().hex}"
        elif not isinstance(claim_name, str):
            raise RuntimeError("verified cleanup claim name is invalid")
        validate_child_name(str(cleanup_claims[source_path]))
    with sqlite3.connect(ledger_path) as claim_conn:
        updated = claim_conn.execute(
            "UPDATE trace_bundle_exports SET cleanup_claims_json = ? "
            "WHERE run_id = ? AND bundle_content_sha256 = ? AND cleaned_at IS NULL",
            (
                json.dumps(cleanup_claims, sort_keys=True),
                run_id,
                manifest["bundle_content_sha256"],
            ),
        )
        if updated.rowcount != 1:
            raise RuntimeError("verified cleanup claim plan could not be persisted")

    source_roots = {
        "codex_session": codex_home / "sessions",
        "codex_exec": traces_dir or runner_root / "traces",
        "runner_stderr": logs_dir or runner_root / "logs",
    }
    verified_sources: list[_VerifiedSourceHandle] = []
    seen_source_paths: set[str] = set()
    try:
        for entry in manifest["files"]:
            raw = entry.get("source_path")
            source_hash = entry.get("source_sha256")
            if not raw or not source_hash:
                continue
            path = Path(raw)
            source_key = str(path)
            if source_key in seen_source_paths:
                raise RuntimeError(f"duplicate verified source path: {path}")
            seen_source_paths.add(source_key)
            source_kind = (
                "codex_session"
                if str(entry.get("path") or "").startswith("threads/")
                else str(entry.get("role") or "")
            )
            root = source_roots.get(source_kind)
            if root is None:
                raise RuntimeError(f"unrecognized verified source kind: {source_kind}")
            handle = _open_verified_source(
                root=root,
                path=path,
                expected_sha256=str(source_hash),
                expected_bytes=int(entry.get("source_bytes", 0)),
                claimed_name=str(cleanup_claims[source_key]),
            )
            if handle is None:
                continue
            verified_sources.append(handle)
    except Exception:
        for handle in verified_sources:
            os.close(handle.file_fd)
            os.close(handle.parent_fd)
        raise

    claimed: list[_VerifiedSourceHandle] = []
    claimed_this_attempt: list[_VerifiedSourceHandle] = []
    try:
        try:
            for handle in verified_sources:
                assert handle.claimed_name is not None
                if not handle.already_claimed:
                    handle.claimed_name = claim_child_at(
                        handle.parent_fd,
                        handle.name,
                        expected=handle.opened,
                        claimed_name=handle.claimed_name,
                    )
                    handle.already_claimed = True
                    claimed_this_attempt.append(handle)
                claimed.append(handle)
                if _sha256_fd(handle.file_fd) != handle.expected_sha256:
                    raise RuntimeError(
                        f"source changed after export; refusing cleanup: {handle.path}"
                    )
                final_stat = os.fstat(handle.file_fd)
                if not _same_inode(final_stat, handle.opened) or final_stat.st_size != handle.size:
                    raise RuntimeError(
                        f"source changed after export; refusing cleanup: {handle.path}"
                    )
        except Exception:
            for handle in reversed(claimed_this_attempt):
                assert handle.claimed_name is not None
                with suppress(RuntimeError):
                    restore_claimed_child_at(
                        handle.parent_fd,
                        handle.name,
                        handle.claimed_name,
                        expected=handle.opened,
                    )
            raise

        removed = []
        removed_handles: set[str] = set()
        reclaimed = 0
        try:
            for handle in verified_sources:
                assert handle.claimed_name is not None
                unlink_claimed_child_at(
                    handle.parent_fd,
                    handle.claimed_name,
                    expected=handle.opened,
                )
                removed_handles.add(str(handle.path))
                removed.append(str(handle.path))
                reclaimed += handle.size
        except Exception:
            for handle in reversed(claimed):
                if str(handle.path) in removed_handles:
                    continue
                assert handle.claimed_name is not None
                with suppress(RuntimeError):
                    restore_claimed_child_at(
                        handle.parent_fd,
                        handle.name,
                        handle.claimed_name,
                        expected=handle.opened,
                    )
            raise
    finally:
        for handle in verified_sources:
            os.close(handle.file_fd)
            os.close(handle.parent_fd)

    cleaned_at = int(datetime.now(UTC).timestamp())
    with sqlite3.connect(ledger_path) as update_conn:
        update_conn.execute(
            "UPDATE trace_bundle_exports SET cleaned_at = ? WHERE run_id = ?",
            (cleaned_at, run_id),
        )
    return {"removed_files": removed, "reclaimed_bytes": reclaimed, "cleaned_at": cleaned_at}


def _open_verified_source(
    *,
    root: Path,
    path: Path,
    expected_sha256: str,
    expected_bytes: int,
    claimed_name: str,
) -> _VerifiedSourceHandle | None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"verified source escapes its retention root: {path}") from exc
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"invalid verified source path: {path}")
    try:
        parent_fd = open_absolute_directory_no_follow(root)
    except RuntimeError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return None
        raise
    try:
        for part in parts[:-1]:
            validate_child_name(part)
            try:
                next_fd = os.open(part, directory_open_flags(), dir_fd=parent_fd)
            except FileNotFoundError:
                os.close(parent_fd)
                return None
            except OSError as exc:
                raise RuntimeError(f"verified source parent is unsafe: {path}: {exc}") from exc
            os.close(parent_fd)
            parent_fd = next_fd
        name = parts[-1]
        validate_child_name(name)
        validate_child_name(claimed_name)
        file_fd: int | None = None
        expected: os.stat_result | None = None
        already_claimed = False
        for candidate, is_claim in ((claimed_name, True), (name, False)):
            try:
                candidate_stat = os.stat(
                    candidate,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                candidate_fd = os.open(
                    candidate,
                    os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RuntimeError(f"verified source is unsafe: {path}: {exc}") from exc
            file_fd = candidate_fd
            expected = candidate_stat
            already_claimed = is_claim
            break
        if file_fd is None or expected is None:
            os.close(parent_fd)
            return None
        opened = os.fstat(file_fd)
        if (
            not stat.S_ISREG(expected.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or not _same_inode(expected, opened)
        ):
            os.close(file_fd)
            raise RuntimeError(f"verified source changed while opening: {path}")
        digest = _sha256_fd(file_fd)
        final_stat = os.fstat(file_fd)
        if (
            digest != expected_sha256
            or final_stat.st_size != opened.st_size
            or final_stat.st_mtime_ns != opened.st_mtime_ns
            or opened.st_size != expected_bytes
        ):
            os.close(file_fd)
            raise RuntimeError(f"source changed after export; refusing cleanup: {path}")
        return _VerifiedSourceHandle(
            path=path,
            parent_fd=parent_fd,
            file_fd=file_fd,
            name=name,
            opened=opened,
            size=opened.st_size,
            expected_sha256=expected_sha256,
            claimed_name=claimed_name,
            already_claimed=already_claimed,
        )
    except Exception:
        os.close(parent_fd)
        raise


def _sha256_fd(file_fd: int) -> str:
    os.lseek(file_fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(file_fd, 1024 * 1024)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


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


def _manifest_source_bytes(manifest: dict[str, Any]) -> int:
    return sum(
        int(entry.get("source_bytes", 0))
        for entry in manifest.get("files", [])
        if isinstance(entry, dict)
    )


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
    parser.add_argument(
        "--dry-run-cleanup",
        action="store_true",
        help="list exact retained sources, cleanup candidates, worktrees, and reasons",
    )
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
    parser.add_argument(
        "--min-disk-free-gib",
        type=float,
        default=float(os.environ.get("JOBSEEK_CODEX_MIN_DISK_FREE_GIB", "5")),
    )
    parser.add_argument(
        "--disk-alert-margin-gib",
        type=float,
        default=float(os.environ.get("JOBSEEK_CODEX_DISK_ALERT_MARGIN_GIB", "2")),
    )
    parser.add_argument(
        "--max-quarantine-runs",
        type=int,
        default=int(os.environ.get("JOBSEEK_CODEX_MAX_QUARANTINE_RUNS", "50")),
    )
    parser.add_argument(
        "--max-quarantine-gib",
        type=float,
        default=float(os.environ.get("JOBSEEK_CODEX_MAX_QUARANTINE_GIB", "2")),
    )
    parser.add_argument(
        "--max-retained-session-files",
        type=int,
        default=int(os.environ.get("JOBSEEK_CODEX_MAX_RETAINED_SESSION_FILES", "500")),
    )
    parser.add_argument(
        "--max-retained-session-gib",
        type=float,
        default=float(os.environ.get("JOBSEEK_CODEX_MAX_RETAINED_SESSION_GIB", "2")),
    )
    parser.add_argument(
        "--max-unlinked-session-age-days",
        type=float,
        default=float(os.environ.get("JOBSEEK_CODEX_MAX_UNLINKED_SESSION_AGE_DAYS", "7")),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.report or args.dry_run_cleanup:
        if args.all or args.run_id:
            raise SystemExit("report/dry-run cleanup does not accept a run_id or --all")
        report = trace_export_report(
            runner_root=args.runner_root,
            codex_home=args.codex_home,
            include_files=args.dry_run_cleanup,
            min_disk_free_gib=args.min_disk_free_gib,
            disk_alert_margin_gib=args.disk_alert_margin_gib,
            max_quarantine_runs=args.max_quarantine_runs,
            max_quarantine_gib=args.max_quarantine_gib,
            max_retained_session_files=args.max_retained_session_files,
            max_retained_session_gib=args.max_retained_session_gib,
            max_unlinked_session_age_days=args.max_unlinked_session_age_days,
        )
        phase = "cleanup_plan" if args.dry_run_cleanup else "report"
        print(json.dumps({"phase": phase, **report}, sort_keys=True))
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
    if owned_temp:
        created_temp_root = Path(tempfile.mkdtemp(prefix=f"trace-backfill-{args.run_id}-"))
        created_parent = created_temp_root.parent.resolve(strict=True)
        temp_root = created_temp_root.resolve(strict=True)
        if temp_root.parent != created_parent or not temp_root.is_dir():
            raise RuntimeError("temporary trace root escaped its canonical parent")
    else:
        temp_root = args.output_dir
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
                retained_bytes=_manifest_source_bytes(manifest),
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
                retained_bytes=_manifest_source_bytes(manifest),
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
            retained_bytes=_manifest_source_bytes(manifest),
        )
        if args.cleanup:
            cleanup = cleanup_verified_sources(
                ledger_path=ledger_path,
                run_id=args.run_id,
                manifest=manifest,
                runner_root=args.runner_root,
                codex_home=args.codex_home,
            )
            print(json.dumps({"phase": "cleaned", **cleanup}, sort_keys=True))
            _record_trace_export_attempt(
                ledger_path=ledger_path,
                run_id=args.run_id,
                status="cleaned",
                quality_tier=tier,
                remote_dir=remote_dir,
                retained_bytes=0,
            )
        return 0
    finally:
        if owned_temp:
            with suppress(RuntimeError):
                safe_rmtree_child(temp_root.parent, temp_root.name, missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
