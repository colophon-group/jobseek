from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.workspace.codex_runner import RunnerLedger
from src.workspace.trace_backfill import (
    SessionSource,
    _backfill_run_ids,
    _batch_delete_patterns,
    _extract_contracts,
    _normalize_string,
    _read_jsonl,
    _safe_thread_filename,
    _session_tree_errors,
    _snapshot_source,
    _validate_downloaded_bundle_file,
    backfill_all,
    build_bundle,
    cleanup_verified_sources,
    inventory_automation_sessions,
    project_thread,
    prune_hf_dataset_cache,
    quality_gate_reason,
    record_verified_export,
    session_retention_status,
    trace_export_report,
    upload_and_verify,
)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def _session_meta(
    *,
    thread_id: str,
    cwd: str,
    source: str | dict,
    parent: str | None = None,
    role: str | None = None,
) -> dict:
    return {
        "timestamp": "2026-07-20T00:00:00Z",
        "type": "session_meta",
        "payload": {
            "id": thread_id,
            "session_id": thread_id,
            "cwd": cwd,
            "source": source,
            "parent_thread_id": parent,
            "agent_path": f"/root/{role}" if role else None,
            "git": {"commit_hash": "abc123"},
            "base_instructions": {"text": "drop me"},
        },
    }


def _verified_sources_fixture(
    tmp_path: Path,
    *,
    source_count: int = 1,
) -> tuple[Path, str, Path, dict, list[Path]]:
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "codex-home"
    ledger = RunnerLedger(runner_root / "state" / "ledger.sqlite")
    run_id = "issue-99-100-race1234"
    assert ledger.acquire(run_id=run_id, issue=99, active_slot="test-race")
    sources = [runner_root / "traces" / f"{run_id}.jsonl"]
    if source_count > 1:
        sources.append(runner_root / "logs" / f"{run_id}.stderr.log")
    for index, source in enumerate(sources):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"source-{index}\n")
    ledger.update(
        run_id,
        trace_path=str(sources[0]),
        stderr_path=str(sources[1]) if source_count > 1 else None,
    )
    ledger.finish(run_id, "failed")
    remote_dir = f"training-bundles/v2/gold/{run_id}"
    files = []
    verified = {}
    for index, source in enumerate(sources):
        role = "codex_exec" if index == 0 else "runner_stderr"
        bundle_path = "codex-exec.jsonl" if index == 0 else "runner-stderr.log"
        projected_hash = hashlib.sha256(f"projected-{index}".encode()).hexdigest()
        files.append(
            {
                "path": bundle_path,
                "role": role,
                "sha256": projected_hash,
                "bytes": 10,
                "source_path": str(source),
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_bytes": source.stat().st_size,
            }
        )
        verified[f"{remote_dir}/{bundle_path}"] = projected_hash
    manifest = {
        "schema_version": "jobseek-codex-training-bundle/v2",
        "quality": {"tier": "gold"},
        "bundle_content_sha256": "bundle-race",
        "thread_count": 1,
        "subagent_count": 0,
        "files": files,
    }
    record_verified_export(
        ledger_path=ledger.path,
        run_id=run_id,
        remote_dir=remote_dir,
        manifest=manifest,
        verified=verified,
    )
    return runner_root, run_id, codex_home, manifest, sources


def _message(role: str, text: str, *, phase: str | None = None) -> dict:
    payload = {
        "type": "message",
        "role": role,
        "content": [{"type": "output_text", "text": text}],
    }
    if phase:
        payload["phase"] = phase
    return {"timestamp": "2026-07-20T00:00:01Z", "type": "response_item", "payload": payload}


def test_extract_contracts_from_rendered_ws_output() -> None:
    records = [
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "output": [
                    {
                        "type": "input_text",
                        "text": (
                            "<track-a>Enrich this company</track-a>\n"
                            "<track-b>Select logos</track-b>"
                        ),
                    }
                ],
            },
        }
    ]
    assert _extract_contracts(records) == {
        "a": "Enrich this company",
        "b": "Select logos",
    }


def test_normalize_redacts_only_documented_placeholder_url_credentials() -> None:
    assert (
        _normalize_string("proxy=http://user:pass@example.test")
        == "proxy=http://[REDACTED_URL_CREDENTIAL]@example.test"
    )


def test_read_jsonl_recovers_raw_newlines_but_rejects_corruption(tmp_path: Path) -> None:
    path = tmp_path / "legacy.jsonl"
    path.write_text(
        '{"type":"response_item","payload":{"text":"first line\n\nsecond line"}}\n'
        "not-json\n"
        '{"type":"event_msg"}\n'
    )

    records, invalid, recovered = _read_jsonl(path)

    assert records == [
        {"type": "response_item", "payload": {"text": "first line\n\nsecond line"}},
        {"type": "event_msg"},
    ]
    assert invalid == 1
    assert recovered == 1


def test_backfill_selects_all_terminal_automation_runs(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.sqlite"
    with sqlite3.connect(ledger) as conn:
        conn.execute("CREATE TABLE runs (run_id TEXT PRIMARY KEY, state TEXT, created_at INTEGER)")
        conn.executemany(
            "INSERT INTO runs VALUES (?, ?, ?)",
            [
                ("issue-1-100-aaaa1111", "completed", 1),
                ("daily-annotations-2026-07-20-100-bbbb2222", "completed", 2),
                ("issue-2-100-cccc3333", "running", 3),
            ],
        )

    assert _backfill_run_ids(ledger, limit=None) == [
        "issue-1-100-aaaa1111",
        "daily-annotations-2026-07-20-100-bbbb2222",
    ]


def test_quality_gate_reason_reports_only_safe_aggregates() -> None:
    manifest = {
        "quality": {
            "credential_findings": [
                {"pattern": "aws_access_key", "line": 5, "path": "threads/main.jsonl"},
                {"pattern": "aws_access_key", "line": 8, "path": "trajectory.jsonl"},
            ],
            "structural_errors": ["missing parent secret-thread-id"],
            "invalid_source_lines": 3,
        }
    }

    reason = quality_gate_reason(manifest)

    assert "credential_findings=2(aws_access_key)" in reason
    assert "structural_errors=1" in reason
    assert "invalid_source_lines=3" in reason
    assert "secret-thread-id" not in reason


def test_normalize_string_preserves_non_secret_proxy_url() -> None:
    assert (
        _normalize_string("proxy=http://real-user:real-password@example.test")
        == "proxy=http://real-user:real-password@example.test"
    )


def test_session_tree_accepts_nested_subagents(tmp_path: Path) -> None:
    root = SessionSource(
        path=tmp_path / "root.jsonl",
        metadata=_session_meta(thread_id="root", cwd="/tmp", source="exec")["payload"],
    )
    child = SessionSource(
        path=tmp_path / "child.jsonl",
        metadata=_session_meta(
            thread_id="child",
            cwd="/tmp",
            source={"subagent": {}},
            parent="root",
            role="child",
        )["payload"],
    )
    grandchild = SessionSource(
        path=tmp_path / "grandchild.jsonl",
        metadata=_session_meta(
            thread_id="grandchild",
            cwd="/tmp",
            source={"subagent": {}},
            parent="child",
            role="grandchild",
        )["payload"],
    )
    assert _session_tree_errors([root, child, grandchild], "root") == []


def test_thread_filename_cannot_escape_bundle_directory(tmp_path: Path) -> None:
    source = SessionSource(
        path=tmp_path / "source.jsonl",
        metadata={"id": "../../outside", "source": {"subagent": {}}, "agent_path": "/root/a/b"},
    )

    filename = _safe_thread_filename(source)

    assert filename == "b-outside.jsonl"
    assert "/" not in filename


def test_downloaded_bundle_validation_is_strict(tmp_path: Path) -> None:
    invalid_jsonl = tmp_path / "trajectory.jsonl"
    invalid_jsonl.write_text('{"valid": true}\nnot-json\n')
    with pytest.raises(RuntimeError, match="remote JSONL is invalid"):
        _validate_downloaded_bundle_file(invalid_jsonl, "trajectory.jsonl")

    invalid_manifest = tmp_path / "manifest.json"
    invalid_manifest.write_text(json.dumps({"schema_version": "old"}))
    with pytest.raises(RuntimeError, match="remote manifest schema is invalid"):
        _validate_downloaded_bundle_file(invalid_manifest, "manifest.json")


def test_batch_delete_patterns_are_scoped_to_current_runs(tmp_path: Path) -> None:
    upload_root = tmp_path / "upload"
    (upload_root / "gold" / "run-1").mkdir(parents=True)
    (upload_root / "silver" / "run-2").mkdir(parents=True)

    assert _batch_delete_patterns(upload_root) == [
        "gold/run-1/*",
        "gold/run-1/**/*",
        "silver/run-2/*",
        "silver/run-2/**/*",
    ]


def test_prune_hf_dataset_cache_is_repo_scoped(monkeypatch, tmp_path: Path) -> None:
    executed: list[bool] = []
    selected: list[tuple[str, ...]] = []

    class Strategy:
        expected_freed_size = 321

        def execute(self) -> None:
            executed.append(True)

    class Cache:
        repos = [
            SimpleNamespace(
                repo_type="dataset",
                repo_id="example/traces",
                revisions=[SimpleNamespace(commit_hash="a"), SimpleNamespace(commit_hash="b")],
            ),
            SimpleNamespace(
                repo_type="dataset",
                repo_id="example/other",
                revisions=[SimpleNamespace(commit_hash="c")],
            ),
        ]

        def delete_revisions(self, *revisions: str):
            selected.append(revisions)
            return Strategy()

    monkeypatch.setattr("huggingface_hub.scan_cache_dir", lambda cache_dir=None: Cache())

    result = prune_hf_dataset_cache(repo_id="example/traces", cache_dir=tmp_path / "cache")

    assert selected == [("a", "b")]
    assert executed == [True]
    assert result == {"repo_id": "example/traces", "revisions": 2, "reclaimed_bytes": 321}


def test_single_upload_replaces_run_directory_and_validates_commit(
    monkeypatch, tmp_path: Path
) -> None:
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / "manifest.json").write_text(
        json.dumps({"schema_version": "jobseek-codex-training-bundle/v2"})
    )
    (bundle_dir / "trajectory.jsonl").write_text('{"type":"trajectory_header"}\n')
    uploads: list[dict] = []
    downloads: list[dict] = []

    def fake_upload_folder(self, **kwargs):
        uploads.append(kwargs)
        return SimpleNamespace(oid="commit-123")

    def fake_download(**kwargs):
        remote_dir = "training-bundles/v2/gold/run-1/"
        relative = str(kwargs["filename"]).removeprefix(remote_dir)
        destination = tmp_path / "download" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((bundle_dir / relative).read_bytes())
        downloads.append(kwargs)
        return str(destination)

    monkeypatch.setattr("src.workspace.trace_backfill._hf_token", lambda: "token")
    monkeypatch.setattr("huggingface_hub.HfApi.upload_folder", fake_upload_folder)
    monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_download)

    remote_dir, verified = upload_and_verify(
        bundle_dir=bundle_dir,
        run_id="run-1",
        repo_id="example/dataset",
        prefix="training-bundles/v2",
        quality_tier="gold",
    )

    assert remote_dir == "training-bundles/v2/gold/run-1"
    assert len(verified) == 2
    assert uploads[0]["delete_patterns"] == ["*", "**/*"]
    assert all(item["revision"] == "commit-123" for item in downloads)


def test_projection_removes_context_reasoning_and_reconstructs_spawn(tmp_path: Path) -> None:
    session_path = tmp_path / "root.jsonl"
    encrypted = "gAAAAA" + "x" * 80
    records = [
        _session_meta(
            thread_id="root",
            cwd="/srv/jobseek-codex/worktrees/company-request-1-run",
            source="exec",
        ),
        _message("user", "# AGENTS.md instructions for /tmp/repo\nproxy http://user:pass@host"),
        _message("user", "<recommended_plugins>Static connector catalog</recommended_plugins>"),
        _message("user", "Resolve issue #1"),
        {
            "timestamp": "2026-07-20T00:00:02Z",
            "type": "response_item",
            "payload": {"type": "reasoning", "encrypted_content": encrypted},
        },
        {
            "timestamp": "2026-07-20T00:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "spawn_agent",
                "call_id": "call-1",
                "arguments": json.dumps(
                    {"task_name": "company_enricher", "fork_turns": "all", "message": encrypted}
                ),
            },
        },
    ]
    _write_jsonl(session_path, records)
    source = SessionSource(path=session_path, metadata=records[0]["payload"])
    projection = project_thread(
        source,
        contracts={"a": "Research and set company metadata"},
        task_contract=None,
    )

    payloads = [line["payload"] for line in projection.lines]
    assert all(payload.get("type") != "reasoning" for payload in payloads)
    assert all("AGENTS.md instructions" not in json.dumps(payload) for payload in payloads)
    assert all("recommended_plugins" not in json.dumps(payload) for payload in payloads)
    spawn = next(payload for payload in payloads if payload.get("name") == "spawn_agent")
    assert json.loads(spawn["arguments"])["message"] == "Research and set company metadata"
    assert encrypted not in json.dumps(payloads)
    assert projection.dropped_reasoning_records == 1
    assert projection.unresolved_encrypted_calls == 0


def test_projection_drops_static_developer_harness_messages(tmp_path: Path) -> None:
    session_path = tmp_path / "root.jsonl"
    records = [
        _session_meta(thread_id="root", cwd="/tmp/run", source="exec"),
        _message("developer", "Static runtime policy"),
        _message("assistant", "Useful trajectory", phase="final_answer"),
    ]
    _write_jsonl(session_path, records)
    source = SessionSource(path=session_path, metadata=records[0]["payload"])
    projection = project_thread(source, contracts={}, task_contract=None)
    serialized = json.dumps(projection.lines)
    assert "Static runtime policy" not in serialized
    assert "Useful trajectory" in serialized


def test_build_and_cleanup_verified_bundle(tmp_path: Path) -> None:
    run_id = "issue-1-100-abcdef12"
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "home" / ".codex"
    cwd = f"/srv/jobseek-codex/worktrees/company-request-1-{run_id}/apps/crawler"
    ledger = runner_root / "state" / "ledger.sqlite"
    ledger.parent.mkdir(parents=True)
    trace_path = runner_root / "traces" / f"{run_id}.jsonl"
    _write_jsonl(
        trace_path,
        [
            {"type": "thread.started"},
            {"type": "item.completed", "item": {"type": "reasoning", "text": "hidden"}},
            {"type": "turn.completed"},
        ],
    )
    stderr_path = runner_root / "logs" / f"{run_id}.stderr.log"
    stderr_path.parent.mkdir(parents=True)
    stderr_path.write_text("runner diagnostic\n")
    with sqlite3.connect(ledger) as conn:
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, issue INTEGER, state TEXT, pr_url TEXT,
                branch TEXT, created_at INTEGER, started_at INTEGER,
                completed_at INTEGER, error TEXT, trace_path TEXT, stderr_path TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO runs VALUES (?, 1, 'completed', NULL, NULL, 1, 2, 3, NULL, ?, ?)",
            (run_id, str(trace_path), str(stderr_path)),
        )

    root_path = codex_home / "sessions" / "2026" / "07" / "20" / "root.jsonl"
    child_path = root_path.with_name("child.jsonl")
    rendered = "<track-a>Research facts and set metadata</track-a>"
    root_records = [
        _session_meta(thread_id="root", cwd=cwd, source="exec"),
        _message("user", "Resolve the company request"),
        {
            "timestamp": "2026-07-20T00:00:00Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "call_id": "render",
                "name": "exec_command",
                "input": {"cmd": "uv run ws task next"},
            },
        },
        {
            "timestamp": "2026-07-20T00:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "render",
                "output": [{"type": "input_text", "text": rendered}],
            },
        },
        _message("assistant", "Done", phase="final_answer"),
    ]
    child_records = [
        _session_meta(
            thread_id="child",
            cwd=cwd,
            source={"subagent": {}},
            parent="root",
            role="company_enricher",
        ),
        _message("assistant", "Metadata complete", phase="final_answer"),
    ]
    _write_jsonl(root_path, root_records)
    _write_jsonl(child_path, child_records)

    bundle_dir = tmp_path / "bundle"
    manifest = build_bundle(
        run_id=run_id,
        runner_root=runner_root,
        codex_home=codex_home,
        output_dir=bundle_dir,
    )
    assert manifest["thread_count"] == 2
    assert manifest["subagent_count"] == 1
    assert manifest["quality"]["tier"] == "gold"
    assert manifest["quality"]["user_messages"] == 1
    assert manifest["quality"]["root_user_messages"] == 1
    assert manifest["quality"]["credential_findings"] == []
    assert sum(entry.get("source_bytes", 0) for entry in manifest["files"]) == sum(
        path.stat().st_size for path in (root_path, child_path, trace_path, stderr_path)
    )
    child_output = next(
        path
        for path in (bundle_dir / "threads").iterdir()
        if path.name.startswith("company_enricher")
    )
    assert "Research facts and set metadata" in child_output.read_text()
    trajectory = (bundle_dir / "trajectory.jsonl").read_text()
    assert "Research facts and set metadata" in trajectory
    assert '"parent_thread_id": "root"' in trajectory
    assert "hidden" not in (bundle_dir / "codex-exec.jsonl").read_text()
    assert manifest["codex_exec"]["dropped_reasoning_records"] == 1

    remote_dir = f"training-bundles/v2/{run_id}"
    first_entry = manifest["files"][0]
    record_verified_export(
        ledger_path=ledger,
        run_id=run_id,
        remote_dir=remote_dir,
        manifest=manifest,
        verified={f"{remote_dir}/{first_entry['path']}": first_entry["sha256"]},
    )
    with pytest.raises(RuntimeError, match="was not checksum-verified"):
        cleanup_verified_sources(
            ledger_path=ledger,
            run_id=run_id,
            manifest=manifest,
            runner_root=runner_root,
            codex_home=codex_home,
        )
    assert root_path.exists()
    assert child_path.exists()
    assert trace_path.exists()
    assert stderr_path.exists()

    record_verified_export(
        ledger_path=ledger,
        run_id=run_id,
        remote_dir=remote_dir,
        manifest=manifest,
        verified={f"{remote_dir}/{entry['path']}": entry["sha256"] for entry in manifest["files"]},
    )
    with sqlite3.connect(ledger) as conn:
        source_row = conn.execute(
            "SELECT source_bytes, source_files_json FROM trace_bundle_exports WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    assert source_row is not None
    source_bytes = source_row[0]
    recorded_sources = json.loads(source_row[1])
    assert source_bytes == sum(
        path.stat().st_size for path in (root_path, child_path, trace_path, stderr_path)
    )
    assert set(recorded_sources) == {
        str(root_path),
        str(child_path),
        str(trace_path),
        str(stderr_path),
    }
    assert all(value["sha256"] for value in recorded_sources.values())
    with sqlite3.connect(ledger) as conn:
        conn.execute(
            "UPDATE trace_bundle_exports SET source_files_json = '{}' WHERE run_id = ?",
            (run_id,),
        )
    with pytest.raises(RuntimeError, match="source inventory does not match"):
        cleanup_verified_sources(
            ledger_path=ledger,
            run_id=run_id,
            manifest=manifest,
            runner_root=runner_root,
            codex_home=codex_home,
        )
    assert root_path.exists()
    with sqlite3.connect(ledger) as conn:
        conn.execute(
            "UPDATE trace_bundle_exports SET source_files_json = ? WHERE run_id = ?",
            (json.dumps(recorded_sources, sort_keys=True), run_id),
        )
    child_original = child_path.read_text()
    child_path.write_text(child_original + "tampered\n")
    with pytest.raises(RuntimeError, match="source changed after export"):
        cleanup_verified_sources(
            ledger_path=ledger,
            run_id=run_id,
            manifest=manifest,
            runner_root=runner_root,
            codex_home=codex_home,
        )
    assert root_path.exists()
    assert child_path.exists()
    assert trace_path.exists()
    assert stderr_path.exists()
    child_path.write_text(child_original)
    result = cleanup_verified_sources(
        ledger_path=ledger,
        run_id=run_id,
        manifest=manifest,
        runner_root=runner_root,
        codex_home=codex_home,
    )
    assert result["reclaimed_bytes"] > 0
    assert not root_path.exists()
    assert not child_path.exists()
    assert not trace_path.exists()
    assert not stderr_path.exists()


def test_source_snapshot_drives_hash_and_bytes_after_path_replacement(
    monkeypatch, tmp_path: Path
) -> None:
    from src.workspace import trace_backfill

    root = tmp_path / "sessions"
    source = root / "root.jsonl"
    source.parent.mkdir(parents=True)
    original = b'{"type":"session_meta","payload":{"id":"original"}}\n'
    replacement = b'{"type":"session_meta","payload":{"id":"replacement"}}\n'
    source.write_bytes(original)
    real_open = trace_backfill._open_retained_file_no_follow

    def replace_after_open(**kwargs):
        descriptor = real_open(**kwargs)
        replacement_path = root / "replacement.jsonl"
        replacement_path.write_bytes(replacement)
        replacement_path.replace(source)
        return descriptor

    monkeypatch.setattr(trace_backfill, "_open_retained_file_no_follow", replace_after_open)
    snapshot = _snapshot_source(
        root=root,
        path=source,
        destination=tmp_path / "snapshot.jsonl",
    )

    assert snapshot is not None
    assert snapshot.path.read_bytes() == original
    assert snapshot.sha256 == hashlib.sha256(original).hexdigest()
    assert snapshot.bytes == len(original)
    assert source.read_bytes() == replacement


def test_cleanup_rejects_symlink_source_without_touching_target(tmp_path: Path) -> None:
    runner_root, run_id, codex_home, manifest, sources = _verified_sources_fixture(tmp_path)
    source = sources[0]
    external = tmp_path / "external-evidence.jsonl"
    source.replace(external)
    source.symlink_to(external)

    with pytest.raises(RuntimeError, match="verified source is unsafe"):
        cleanup_verified_sources(
            ledger_path=runner_root / "state" / "ledger.sqlite",
            run_id=run_id,
            manifest=manifest,
            runner_root=runner_root,
            codex_home=codex_home,
        )

    assert source.is_symlink()
    assert external.read_text() == "source-0\n"


def test_cleanup_preserves_replacement_at_atomic_claim_boundary(
    monkeypatch, tmp_path: Path
) -> None:
    from src.workspace import trace_backfill

    runner_root, run_id, codex_home, manifest, sources = _verified_sources_fixture(tmp_path)
    source = sources[0]
    original_claim = trace_backfill.claim_child_at
    saved_name = "saved-original.jsonl"

    def replace_before_claim(parent_fd, name, *, expected, claimed_name=None):
        os.rename(name, saved_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        replacement_fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_fd,
        )
        os.write(replacement_fd, b"replacement\n")
        os.close(replacement_fd)
        return original_claim(
            parent_fd,
            name,
            expected=expected,
            claimed_name=claimed_name,
        )

    monkeypatch.setattr(trace_backfill, "claim_child_at", replace_before_claim)
    with pytest.raises(RuntimeError, match="changed at mutation boundary"):
        cleanup_verified_sources(
            ledger_path=runner_root / "state" / "ledger.sqlite",
            run_id=run_id,
            manifest=manifest,
            runner_root=runner_root,
            codex_home=codex_home,
        )

    assert source.read_text() == "replacement\n"
    assert source.with_name(saved_name).read_text() == "source-0\n"


def test_partial_cleanup_failure_restores_remaining_claims_for_retry(
    monkeypatch, tmp_path: Path
) -> None:
    from src.workspace import trace_backfill

    runner_root, run_id, codex_home, manifest, sources = _verified_sources_fixture(
        tmp_path,
        source_count=2,
    )
    original_unlink = trace_backfill.unlink_claimed_child_at
    calls = 0

    def fail_second_unlink(parent_fd, claimed_name, *, expected):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated unlink failure")
        return original_unlink(parent_fd, claimed_name, expected=expected)

    monkeypatch.setattr(trace_backfill, "unlink_claimed_child_at", fail_second_unlink)
    with pytest.raises(RuntimeError, match="simulated unlink failure"):
        cleanup_verified_sources(
            ledger_path=runner_root / "state" / "ledger.sqlite",
            run_id=run_id,
            manifest=manifest,
            runner_root=runner_root,
            codex_home=codex_home,
        )

    assert not sources[0].exists()
    assert sources[1].exists()
    monkeypatch.setattr(trace_backfill, "unlink_claimed_child_at", original_unlink)
    result = cleanup_verified_sources(
        ledger_path=runner_root / "state" / "ledger.sqlite",
        run_id=run_id,
        manifest=manifest,
        runner_root=runner_root,
        codex_home=codex_home,
    )
    assert result["removed_files"] == [str(sources[1])]
    assert not sources[1].exists()


def test_process_death_after_claim_resumes_from_durable_claim_name(
    monkeypatch, tmp_path: Path
) -> None:
    from src.workspace import trace_backfill

    runner_root, run_id, codex_home, manifest, sources = _verified_sources_fixture(tmp_path)
    source = sources[0]
    original_unlink = trace_backfill.unlink_claimed_child_at
    monkeypatch.setattr(
        trace_backfill,
        "unlink_claimed_child_at",
        lambda *args, **kwargs: (_ for _ in ()).throw(SystemExit("process died")),
    )

    with pytest.raises(SystemExit, match="process died"):
        cleanup_verified_sources(
            ledger_path=runner_root / "state" / "ledger.sqlite",
            run_id=run_id,
            manifest=manifest,
            runner_root=runner_root,
            codex_home=codex_home,
        )

    assert not source.exists()
    with sqlite3.connect(runner_root / "state" / "ledger.sqlite") as conn:
        row = conn.execute(
            "SELECT cleanup_claims_json, cleaned_at FROM trace_bundle_exports WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    claims = json.loads(row[0])
    claimed_path = source.with_name(claims[str(source)])
    assert claimed_path.read_text() == "source-0\n"
    assert row[1] is None

    monkeypatch.setattr(trace_backfill, "unlink_claimed_child_at", original_unlink)
    result = cleanup_verified_sources(
        ledger_path=runner_root / "state" / "ledger.sqlite",
        run_id=run_id,
        manifest=manifest,
        runner_root=runner_root,
        codex_home=codex_home,
    )

    assert result["removed_files"] == [str(source)]
    assert not claimed_path.exists()


def test_session_inventory_accounts_active_orphan_unparseable_and_unsafe(
    tmp_path: Path,
) -> None:
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "codex-home"
    ledger = RunnerLedger(runner_root / "state" / "ledger.sqlite")
    run_id = "daily-error-review-2026-08-24-100-abcd1234"
    worktree = runner_root / "worktrees" / run_id
    assert ledger.acquire(run_id=run_id, issue=None, active_slot="daily-error-review")
    ledger.update(run_id, worktree_path=str(worktree))
    session_root = codex_home / "sessions" / "2026" / "08" / "24"
    active = session_root / "active.jsonl"
    _write_jsonl(active, [_session_meta(thread_id="active", cwd=str(worktree), source="exec")])
    orphan = session_root / "orphan.jsonl"
    _write_jsonl(orphan, [_session_meta(thread_id="orphan", cwd="/tmp/unowned", source="exec")])
    malformed = session_root / "malformed.jsonl"
    malformed.write_text("not-json\n")
    outside = tmp_path / "outside.jsonl"
    outside.write_text("outside\n")
    unsafe = session_root / "unsafe.jsonl"
    unsafe.symlink_to(outside)

    inventory = inventory_automation_sessions(codex_home, ledger_path=ledger.path)
    status = session_retention_status(
        runner_root=runner_root,
        codex_home=codex_home,
        max_files=100,
        max_bytes=1024 * 1024,
        max_unlinked_age_s=86400,
    )

    assert [source.path for source in inventory.by_run[run_id]] == [active]
    assert inventory.unlinked == (orphan,)
    assert inventory.unparseable == (malformed,)
    assert [entry.path for entry in inventory.unsafe] == [unsafe]
    assert status.files == 4
    assert status.active_files == 1
    assert status.unlinked_files == 3
    assert status.unparseable_files == 1
    assert status.unsafe_files == 1
    assert status.unsafe_bytes == len(str(outside))
    assert status.over_limit
    assert status.reason == "unsafe Codex session entries retained: 1"


def test_daily_subagents_do_not_require_resolver_track_contracts(tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "codex-home"
    ledger = RunnerLedger(runner_root / "state" / "ledger.sqlite")
    run_id = "daily-annotations-2026-08-24-100-abcd1234"
    worktree = runner_root / "worktrees" / run_id
    assert ledger.acquire(run_id=run_id, issue=None, active_slot="daily-annotations")
    ledger.update(run_id, worktree_path=str(worktree))
    ledger.finish(run_id, "completed")
    session_root = codex_home / "sessions" / "2026" / "08" / "24"
    _write_jsonl(
        session_root / "root.jsonl",
        [
            _session_meta(thread_id="root", cwd=str(worktree), source="exec"),
            _message("user", "Run the daily annotations routine"),
            _message("assistant", "Complete", phase="final_answer"),
        ],
    )
    _write_jsonl(
        session_root / "child.jsonl",
        [
            _session_meta(
                thread_id="child",
                cwd=str(worktree),
                source={"subagent": {}},
                parent="root",
                role="jobseek-labeller-extractor",
            ),
            _message("assistant", "Extracted", phase="final_answer"),
        ],
    )

    manifest = build_bundle(
        run_id=run_id,
        runner_root=runner_root,
        codex_home=codex_home,
        output_dir=tmp_path / "bundle",
    )

    assert manifest["quality"]["missing_task_contract_thread_ids"] == []
    assert manifest["quality"]["tier"] == "gold"


def test_oversize_session_is_accounted_and_blocks_admission(tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "codex-home"
    RunnerLedger(runner_root / "state" / "ledger.sqlite")
    oversize = codex_home / "sessions" / "2026" / "08" / "24" / "oversize.jsonl"
    oversize.parent.mkdir(parents=True)
    with oversize.open("wb") as handle:
        handle.truncate(64 * 1024 * 1024 + 1)

    inventory = inventory_automation_sessions(
        codex_home,
        ledger_path=runner_root / "state" / "ledger.sqlite",
    )
    status = session_retention_status(
        runner_root=runner_root,
        codex_home=codex_home,
        max_files=500,
        max_bytes=2 * 1024**3,
        max_unlinked_age_s=7 * 24 * 60 * 60,
    )

    assert [entry.path for entry in inventory.oversize] == [oversize]
    assert status.oversize_files == 1
    assert status.oversize_bytes == 64 * 1024 * 1024 + 1
    assert status.unlinked_files == 1
    assert status.over_limit
    assert status.reason == "oversize Codex session files retained: 1"


def test_stale_syntactic_run_id_without_ledger_row_is_an_orphan(tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "codex-home"
    ledger = RunnerLedger(runner_root / "state" / "ledger.sqlite")
    stale_run_id = "daily-annotations-2026-07-01-100-forged12"
    session = codex_home / "sessions" / "2026" / "07" / "01" / "stale.jsonl"
    _write_jsonl(
        session,
        [
            _session_meta(
                thread_id="stale",
                cwd=f"/tmp/{stale_run_id}",
                source="exec",
            )
        ],
    )
    now = 2_000_000_000
    old = now - 30 * 24 * 60 * 60
    os.utime(session, (old, old))

    inventory = inventory_automation_sessions(codex_home, ledger_path=ledger.path)
    status = session_retention_status(
        runner_root=runner_root,
        codex_home=codex_home,
        max_files=500,
        max_bytes=2 * 1024**3,
        max_unlinked_age_s=7 * 24 * 60 * 60,
        now=now,
    )

    assert stale_run_id not in inventory.by_run
    assert inventory.unlinked == (session,)
    assert status.oldest_unlinked_age_s == 30 * 24 * 60 * 60
    assert status.over_limit
    assert status.reason == (
        "unlinked or unparseable Codex session age limit reached: 2592000 seconds"
    )


def test_known_run_id_outside_authoritative_worktree_remains_unlinked(
    tmp_path: Path,
) -> None:
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "codex-home"
    ledger = RunnerLedger(runner_root / "state" / "ledger.sqlite")
    run_id = "daily-error-review-2026-08-24-100-abcd1234"
    worktree = runner_root / "worktrees" / run_id
    assert ledger.acquire(run_id=run_id, issue=None, active_slot="daily-error-review")
    ledger.update(run_id, worktree_path=str(worktree))
    ledger.finish(run_id, "failed")
    unrelated_cwd = tmp_path / "unrelated" / run_id / "work"
    session = codex_home / "sessions" / "2026" / "08" / "24" / "unrelated.jsonl"
    records = [
        _session_meta(thread_id="unrelated", cwd=str(unrelated_cwd), source="exec"),
        _message("user", "unrelated evidence"),
        _message("assistant", "done", phase="final_answer"),
    ]
    _write_jsonl(session, records)

    inventory = inventory_automation_sessions(codex_home, ledger_path=ledger.path)

    assert run_id not in inventory.by_run
    assert inventory.unlinked == (session,)
    with pytest.raises(RuntimeError, match="no longer belongs"):
        build_bundle(
            run_id=run_id,
            runner_root=runner_root,
            codex_home=codex_home,
            output_dir=tmp_path / "bundle",
            sessions=[SessionSource(path=session, metadata=records[0]["payload"])],
        )
    assert session.exists()
    assert "unrelated evidence" in session.read_text()


def test_backfill_all_batches_tiers_and_cleans(monkeypatch, tmp_path: Path) -> None:
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "home" / ".codex"
    ledger = runner_root / "state" / "ledger.sqlite"
    ledger.parent.mkdir(parents=True)
    run_ids = ["issue-10-100-aaaa1111", "issue-11-101-bbbb2222"]
    with sqlite3.connect(ledger) as conn:
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, issue INTEGER, state TEXT,
                created_at INTEGER, trace_path TEXT, stderr_path TEXT
            )
            """
        )
        for index, run_id in enumerate(run_ids):
            trace_path = runner_root / "traces" / f"{run_id}.jsonl"
            _write_jsonl(trace_path, [{"type": "turn.completed"}])
            conn.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, NULL)",
                (run_id, 10 + index, "failed", index, str(trace_path)),
            )
            session_path = codex_home / "sessions" / "2026" / "07" / "20" / f"{run_id}.jsonl"
            cwd = f"/srv/jobseek-codex/worktrees/company-request-{10 + index}-{run_id}"
            records = [_session_meta(thread_id=f"root-{index}", cwd=cwd, source="exec")]
            records.append(_message("user", "Resolve the company request"))
            if index == 0:
                records.append(_message("assistant", "Useful", phase="final_answer"))
            _write_jsonl(session_path, records)

    def fake_upload_batch(**kwargs):
        upload_root = kwargs["upload_root"]
        prefix = kwargs["prefix"]
        verified = {
            f"{prefix}/{path.relative_to(upload_root).as_posix()}": hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in upload_root.rglob("*")
            if path.is_file()
        }
        return prefix, verified

    monkeypatch.setattr("src.workspace.trace_backfill.upload_batch_and_verify", fake_upload_batch)
    summary = backfill_all(
        runner_root=runner_root,
        codex_home=codex_home,
        repo_id="example/dataset",
        prefix="training-bundles/v2",
        batch_size=2,
        cleanup=True,
        allow_silver=True,
        allow_diagnostic=True,
        limit=None,
    )

    assert summary["uploaded"] == 2
    assert summary["cleaned"] == 2
    assert summary["failed"] == 0
    assert summary["credential_redactions"] == 0
    assert summary["tiers"] == {"gold": 1, "silver": 0, "diagnostic": 1}
    assert not list((codex_home / "sessions").rglob("*.jsonl"))
    assert not list((runner_root / "traces").glob("*.jsonl"))
    report = trace_export_report(runner_root=runner_root, codex_home=codex_home)
    assert report["terminal_runs"] == 2
    assert report["pending_runs"] == 0
    assert report["unaccounted_runs"] == 0
    assert report["retained_session_bytes"] == 0


def test_retention_report_accounts_categories_reasons_and_cleanup_candidates(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "home" / ".codex"
    ledger = RunnerLedger(runner_root / "state" / "ledger.sqlite")
    quarantined_run = "issue-20-100-aaaa1111"
    verified_run = "issue-21-101-bbbb2222"

    quarantined_trace = runner_root / "traces" / f"{quarantined_run}.jsonl"
    verified_trace = runner_root / "traces" / f"{verified_run}.jsonl"
    stderr_path = runner_root / "logs" / f"{quarantined_run}.stderr.log"
    for path, content in (
        (quarantined_trace, '{"type":"turn.failed"}\n'),
        (verified_trace, '{"type":"turn.completed"}\n'),
        (stderr_path, "diagnostic\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    worktree = runner_root / "worktrees" / f"company-request-20-{quarantined_run}"
    (worktree / "apps" / "crawler").mkdir(parents=True)
    (worktree / "debug.txt").write_text("retained debug state")
    session_path = codex_home / "sessions" / "2026" / "07" / "20" / "root.jsonl"
    _write_jsonl(
        session_path,
        [
            _session_meta(
                thread_id="root",
                cwd=f"{worktree}/apps/crawler",
                source="exec",
            ),
            _message("user", "Resolve the company request"),
        ],
    )
    hf_cache_file = codex_home.parent / ".cache" / "huggingface" / "hub" / "blob"
    hf_cache_file.parent.mkdir(parents=True)
    hf_cache_file.write_bytes(b"cache")

    for run_id, trace in (
        (quarantined_run, quarantined_trace),
        (verified_run, verified_trace),
    ):
        assert ledger.acquire(run_id=run_id, issue=20, active_slot=run_id)
        ledger.update(
            run_id,
            trace_path=str(trace),
            stderr_path=str(stderr_path) if run_id == quarantined_run else None,
            worktree_path=str(worktree) if run_id == quarantined_run else None,
        )
        ledger.finish(run_id, "failed")
    ledger.record_trace_bundle_attempt(
        quarantined_run,
        status="quarantined",
        quality_tier="quarantined",
        retained_bytes=quarantined_trace.stat().st_size + session_path.stat().st_size,
    )
    record_verified_export(
        ledger_path=ledger.path,
        run_id=verified_run,
        remote_dir=f"training-bundles/v2/gold/{verified_run}",
        manifest={
            "schema_version": "jobseek-codex-training-bundle/v2",
            "quality": {"tier": "gold"},
            "bundle_content_sha256": "bundle",
            "thread_count": 1,
            "subagent_count": 0,
            "files": [
                {
                    "path": "codex-exec.jsonl",
                    "sha256": "remote",
                    "bytes": verified_trace.stat().st_size,
                    "source_path": str(verified_trace),
                    "source_sha256": hashlib.sha256(verified_trace.read_bytes()).hexdigest(),
                    "source_bytes": verified_trace.stat().st_size,
                }
            ],
        },
        verified={f"training-bundles/v2/gold/{verified_run}/codex-exec.jsonl": "remote"},
    )

    report = trace_export_report(
        runner_root=runner_root,
        codex_home=codex_home,
        include_files=True,
        max_quarantine_runs=1,
    )

    assert report["storage"]["codex_sessions"]["files"] == 1
    assert report["storage"]["canonical_traces"]["files"] == 2
    assert report["storage"]["stderr_logs"]["files"] == 1
    assert report["storage"]["huggingface_cache"]["bytes"] == 5
    assert report["storage"]["worktrees"]["directories"] == 1
    assert report["quarantine"]["runs"] == 1
    assert any(alert["kind"] == "quarantine_limit" for alert in report["alerts"])
    assert any(
        item["path"] == str(verified_trace)
        and item["reason"] == "verified_pending_checksum_gated_cleanup"
        for item in report["cleanup_candidates"]
    )
    assert any(
        item["path"] == str(quarantined_trace)
        and item["reason"] == "quarantined_retained"
        and not item["cleanup_candidate"]
        for item in report["files"]
    )


def test_credential_values_are_redacted_before_verified_export(tmp_path: Path) -> None:
    run_id = "issue-2-100-abcdef12"
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "home" / ".codex"
    cwd = f"/srv/jobseek-codex/worktrees/company-request-2-{run_id}/apps/crawler"
    ledger = runner_root / "state" / "ledger.sqlite"
    ledger.parent.mkdir(parents=True)
    with sqlite3.connect(ledger) as conn:
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, issue INTEGER, state TEXT, pr_url TEXT,
                branch TEXT, created_at INTEGER, started_at INTEGER,
                completed_at INTEGER, error TEXT, trace_path TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO runs VALUES (?, 2, 'failed', NULL, NULL, 1, 2, 3, 'x', NULL)",
            (run_id,),
        )
    root_path = codex_home / "sessions" / "2026" / "07" / "20" / "root.jsonl"
    _write_jsonl(
        root_path,
        [
            _session_meta(thread_id="root", cwd=cwd, source="exec"),
            _message("assistant", "Leaked token hf_" + "a" * 32, phase="final_answer"),
        ],
    )
    manifest = build_bundle(
        run_id=run_id,
        runner_root=runner_root,
        codex_home=codex_home,
        output_dir=tmp_path / "bundle",
    )
    assert manifest["quality"]["tier"] == "silver"
    assert manifest["quality"]["credential_findings"] == []
    assert manifest["quality"]["credential_redactions"]
    assert {finding["pattern"] for finding in manifest["quality"]["credential_redactions"]} == {
        "huggingface_token"
    }
    assert "hf_" + "a" * 32 in root_path.read_text()
    for path in (tmp_path / "bundle").rglob("*"):
        if path.is_file():
            assert "hf_" + "a" * 32 not in path.read_text()
    remote_dir = f"training-bundles/v2/silver/{run_id}"
    record_verified_export(
        ledger_path=ledger,
        run_id=run_id,
        remote_dir=remote_dir,
        manifest=manifest,
        verified={f"{remote_dir}/{entry['path']}": entry["sha256"] for entry in manifest["files"]},
    )
    cleanup_verified_sources(
        ledger_path=ledger,
        run_id=run_id,
        manifest=manifest,
        runner_root=runner_root,
        codex_home=codex_home,
    )
    assert not root_path.exists()


def test_residual_credential_finding_still_quarantines_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from src.workspace import trace_backfill

    run_id = "issue-3-100-abcdef12"
    runner_root = tmp_path / "runner"
    codex_home = tmp_path / "home" / ".codex"
    cwd = f"/srv/jobseek-codex/worktrees/company-request-3-{run_id}/apps/crawler"
    ledger = runner_root / "state" / "ledger.sqlite"
    ledger.parent.mkdir(parents=True)
    with sqlite3.connect(ledger) as conn:
        conn.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY, issue INTEGER, state TEXT, pr_url TEXT,
                branch TEXT, created_at INTEGER, started_at INTEGER,
                completed_at INTEGER, error TEXT, trace_path TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO runs VALUES (?, 3, 'failed', NULL, NULL, 1, 2, 3, 'x', NULL)",
            (run_id,),
        )
    root_path = codex_home / "sessions" / "2026" / "07" / "20" / "root.jsonl"
    _write_jsonl(
        root_path,
        [
            _session_meta(thread_id="root", cwd=cwd, source="exec"),
            _message("assistant", "Leaked token hf_" + "a" * 32, phase="final_answer"),
        ],
    )
    monkeypatch.setattr(
        trace_backfill,
        "redact_credentials",
        lambda text: (text, []),
    )

    manifest = build_bundle(
        run_id=run_id,
        runner_root=runner_root,
        codex_home=codex_home,
        output_dir=tmp_path / "bundle",
    )

    assert manifest["quality"]["tier"] == "quarantined"
    assert manifest["quality"]["credential_findings"]
    assert root_path.exists()


def test_owned_temp_root_is_canonicalized_before_safe_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from src.workspace import trace_backfill

    real_parent = tmp_path / "real-temp-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-temp-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    created_via_link = linked_parent / "owned-temp"
    captured_output: list[Path] = []

    def fake_mkdtemp(*, prefix: str) -> str:
        assert prefix.startswith("trace-backfill-issue-9-")
        created_via_link.mkdir()
        return str(created_via_link)

    def fake_build_bundle(**kwargs):
        output_dir = kwargs["output_dir"]
        captured_output.append(output_dir)
        output_dir.mkdir(parents=True)
        return {
            "run": {"run_id": "issue-9", "state": "failed"},
            "quality": {"tier": "gold"},
            "thread_count": 1,
            "subagent_count": 0,
            "bundle_content_sha256": "a" * 64,
            "files": [],
        }

    monkeypatch.setattr(trace_backfill.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(trace_backfill, "build_bundle", fake_build_bundle)

    result = trace_backfill.main(
        [
            "issue-9",
            "--runner-root",
            str(tmp_path / "runner"),
            "--codex-home",
            str(tmp_path / "codex-home"),
        ]
    )

    assert result == 0
    assert captured_output == [real_parent / "owned-temp" / "issue-9"]
    assert not (real_parent / "owned-temp").exists()
