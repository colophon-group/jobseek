from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.workspace.trace_backfill import (
    SessionSource,
    _backfill_run_ids,
    _batch_delete_patterns,
    _extract_contracts,
    _normalize_string,
    _read_jsonl,
    _safe_thread_filename,
    _session_tree_errors,
    _validate_downloaded_bundle_file,
    backfill_all,
    build_bundle,
    cleanup_verified_sources,
    project_thread,
    quality_gate_reason,
    record_verified_export,
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


def test_backfill_selects_only_company_resolver_runs(tmp_path: Path) -> None:
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

    assert _backfill_run_ids(ledger, limit=None) == ["issue-1-100-aaaa1111"]


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
        cleanup_verified_sources(ledger_path=ledger, run_id=run_id, manifest=manifest)
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
            "SELECT source_bytes FROM trace_bundle_exports WHERE run_id = ?", (run_id,)
        ).fetchone()
    assert source_row is not None
    source_bytes = source_row[0]
    assert source_bytes == sum(
        path.stat().st_size for path in (root_path, child_path, trace_path, stderr_path)
    )
    child_original = child_path.read_text()
    child_path.write_text(child_original + "tampered\n")
    with pytest.raises(RuntimeError, match="source changed after export"):
        cleanup_verified_sources(ledger_path=ledger, run_id=run_id, manifest=manifest)
    assert root_path.exists()
    assert child_path.exists()
    assert trace_path.exists()
    assert stderr_path.exists()
    child_path.write_text(child_original)
    result = cleanup_verified_sources(ledger_path=ledger, run_id=run_id, manifest=manifest)
    assert result["reclaimed_bytes"] > 0
    assert not root_path.exists()
    assert not child_path.exists()
    assert not trace_path.exists()
    assert not stderr_path.exists()


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
    assert summary["tiers"] == {"gold": 1, "silver": 0, "diagnostic": 1}
    assert not list((codex_home / "sessions").rglob("*.jsonl"))
    assert not list((runner_root / "traces").glob("*.jsonl"))
    report = trace_export_report(runner_root=runner_root, codex_home=codex_home)
    assert report["terminal_runs"] == 2
    assert report["pending_runs"] == 0
    assert report["unaccounted_runs"] == 0
    assert report["retained_session_bytes"] == 0


def test_credential_findings_quarantine_bundle(tmp_path: Path) -> None:
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
    assert manifest["quality"]["tier"] == "quarantined"
    assert manifest["quality"]["credential_findings"]
    remote_dir = f"training-bundles/v2/quarantined/{run_id}"
    record_verified_export(
        ledger_path=ledger,
        run_id=run_id,
        remote_dir=remote_dir,
        manifest=manifest,
        verified={f"{remote_dir}/{entry['path']}": entry["sha256"] for entry in manifest["files"]},
    )
    with pytest.raises(RuntimeError, match="refusing cleanup for quarantined"):
        cleanup_verified_sources(ledger_path=ledger, run_id=run_id, manifest=manifest)
    assert root_path.exists()
