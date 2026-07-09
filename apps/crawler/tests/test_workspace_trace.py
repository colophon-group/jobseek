from __future__ import annotations

import json
from pathlib import Path

from src.workspace import log as action_log
from src.workspace import trace
from src.workspace.state import Board, Workspace, save_board, save_workspace, ws_log_path


def _patch_workspace_dir(monkeypatch, tmp_path: Path) -> Path:
    ws_root = tmp_path / ".workspace"
    monkeypatch.setattr("src.shared.constants.get_workspace_dir", lambda: ws_root)
    monkeypatch.setattr("src.workspace.state.get_workspace_dir", lambda: ws_root)
    return ws_root


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def test_build_trace_prefers_codex_exec_jsonl(monkeypatch, tmp_path):
    _patch_workspace_dir(monkeypatch, tmp_path)
    save_workspace(Workspace(slug="acme", name="Acme", issue=42))

    action_log.append(ws_log_path("acme"), "new", True, "Fallback log should not be used")

    codex_events = tmp_path / "codex-events.jsonl"
    _write_jsonl(
        codex_events,
        [
            {"type": "thread.started", "thread_id": "thread_1"},
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "command_execution",
                    "command": "cd apps/crawler && uv run ws task --issue 42",
                },
            },
            {
                "type": "item.completed",
                "item": {
                    "id": "item_2",
                    "type": "command_execution",
                    "command": "uv run ws new acme --issue 42",
                },
            },
            {
                "type": "item.completed",
                "item": {"id": "item_3", "type": "agent_message", "text": "Configured Acme."},
            },
        ],
    )

    monkeypatch.setattr(trace, "discover_transcript", lambda _slug: None)
    monkeypatch.setattr(trace, "_find_codex_event_files", lambda: [codex_events])

    result = trace._build_trace("acme")

    assert result is not None
    header, records = result
    assert header["source"] == "codex_exec_jsonl"
    assert header["record_count"] == 3
    assert [record["_source"] for record in records] == ["codex_exec_jsonl"] * 3
    assert records[0]["item"]["command"].endswith("ws task --issue 42")


def test_codex_trace_does_not_match_hyphen_prefix_slug(monkeypatch, tmp_path):
    _patch_workspace_dir(monkeypatch, tmp_path)
    save_workspace(Workspace(slug="acme", name="Acme", issue=42))

    wrong_events = tmp_path / "wrong-codex-events.jsonl"
    _write_jsonl(
        wrong_events,
        [
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "command_execution",
                    "command": "uv run ws new acme-legacy --issue 99",
                },
            }
        ],
    )

    right_events = tmp_path / "right-codex-events.jsonl"
    _write_jsonl(
        right_events,
        [
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "command_execution",
                    "command": "uv run ws new acme --issue 42",
                },
            }
        ],
    )

    monkeypatch.setattr(trace, "_find_codex_event_files", lambda: [wrong_events, right_events])

    assert trace.discover_codex_events("acme") == right_events


def test_claude_transcript_does_not_match_hyphen_prefix_slug(monkeypatch, tmp_path):
    _patch_workspace_dir(monkeypatch, tmp_path)
    ws_log = ws_log_path("acme")
    action_log.append(ws_log, "new", True, "Created workspace")
    log_ts = action_log.read(ws_log)[0]["ts"]

    wrong_transcript = tmp_path / "wrong.jsonl"
    _write_jsonl(
        wrong_transcript,
        [
            {
                "type": "assistant",
                "timestamp": log_ts,
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "uv run ws new acme-legacy --issue 99"},
                        }
                    ]
                },
            }
        ],
    )

    right_transcript = tmp_path / "right.jsonl"
    _write_jsonl(
        right_transcript,
        [
            {
                "type": "assistant",
                "timestamp": log_ts,
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "uv run ws new acme --issue 42"},
                        }
                    ]
                },
            }
        ],
    )

    monkeypatch.setattr(
        trace,
        "_find_all_transcripts",
        lambda: [wrong_transcript, right_transcript],
    )

    assert trace.discover_transcript("acme") == right_transcript


def test_build_trace_falls_back_to_ws_action_log(monkeypatch, tmp_path):
    _patch_workspace_dir(monkeypatch, tmp_path)
    save_workspace(Workspace(slug="acme", name="Acme", issue=42))
    save_board(
        "acme",
        Board(
            alias="careers",
            slug="acme-careers",
            url="https://example.com/jobs",
            log=[
                {
                    "ts": "2026-06-22T09:02:00Z",
                    "cmd": "run monitor",
                    "ok": True,
                    "msg": "12 jobs",
                }
            ],
        ),
    )
    action_log.append(ws_log_path("acme"), "new", True, "Created workspace")

    monkeypatch.setattr(trace, "discover_transcript", lambda _slug: None)
    monkeypatch.setattr(trace, "_find_codex_event_files", list)

    result = trace._build_trace("acme")

    assert result is not None
    header, records = result
    assert header["source"] == "ws_action_log"
    assert header["record_count"] == 2
    assert {record["_scope"] for record in records} == {"workspace", "board:careers"}
    assert [record["_source"] for record in records] == ["ws_action_log"] * 2


def test_export_trace_writes_action_log_fallback(monkeypatch, tmp_path):
    _patch_workspace_dir(monkeypatch, tmp_path)
    save_workspace(Workspace(slug="acme", name="Acme", issue=42))
    action_log.append(ws_log_path("acme"), "complete", True, "Workflow complete")

    monkeypatch.setattr(trace, "discover_transcript", lambda _slug: None)
    monkeypatch.setattr(trace, "_find_codex_event_files", list)

    out_path = trace.export_trace("acme", tmp_path / "traces")

    assert out_path == tmp_path / "traces" / "traces.jsonl"
    lines = [json.loads(line) for line in out_path.read_text().splitlines()]
    assert lines[0]["_trace_header"] is True
    assert lines[0]["source"] == "ws_action_log"
    assert lines[1]["command"] == "complete"


def test_trace_credential_detector_catches_token_shapes() -> None:
    findings = trace.detect_credentials(
        "\n".join(
            [
                "Authorization: bearer abcdefghijklmnopqrstuvwxyz0123456789",
                "GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyzABCDEFGH123456",
                "HF_TOKEN=hf_abcdefghijklmnopqrstuvwxyzABCDEFGH123456",
            ]
        )
    )

    assert {finding["pattern"] for finding in findings} >= {
        "bearer_token",
        "github_token",
        "huggingface_token",
        "sensitive_assignment",
    }


def test_trace_credential_detector_ignores_public_ats_tokens() -> None:
    payload = json.dumps(
        {
            "monitor_config": {"token": "smartrecruiters-company-slug"},
            "message": "HF_TOKEN environment variable not set — cannot upload trace",
            "status": "DATABASE_URL: configured",
        }
    )

    assert trace.detect_credentials(payload) == []


def test_trace_credential_detector_catches_json_escaped_assignments() -> None:
    payload = trace._trace_payload(
        {"_trace_header": True, "date": "2026-07-09", "record_count": 1},
        [
            {
                "type": "item.completed",
                "item": {
                    "aggregated_output": 'SECRET_KEY="supersecretvalue"',
                    "env": {"DATABASE_URL": "postgres://user:pass@example.com/db"},
                },
            }
        ],
    )

    patterns = {finding["pattern"] for finding in trace.detect_credentials(payload)}

    assert "sensitive_assignment" in patterns
    assert "url_password" in patterns


def test_upload_trace_refuses_payload_with_credentials(monkeypatch) -> None:
    header = {"_trace_header": True, "date": "2026-07-09", "record_count": 1}
    records = [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "aggregated_output": (
                    "debug accidentally printed ghp_abcdefghijklmnopqrstuvwxyzABCDEFGH123456"
                ),
            },
        }
    ]

    monkeypatch.setattr(trace, "_hf_token", lambda: "hf_safe_token_not_in_payload")
    monkeypatch.setattr(trace, "_build_trace", lambda _slug: (header, records))

    try:
        trace.upload_trace_to_hf("acme")
    except trace.TraceCredentialError as exc:
        message = str(exc)
    else:
        raise AssertionError("upload should have been blocked")

    assert "github_token@line" in message
    assert "ghp_" not in message
