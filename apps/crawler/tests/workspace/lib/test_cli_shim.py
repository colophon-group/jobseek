"""Tests for the JSON-in / JSON-out HTTP shim entry point.

The shim is the boundary the TS routes call into via subprocess. These
tests stub the real lib functions so we can verify the shim's
responsibilities in isolation:

  - dispatch on `subcommand`
  - typed-exception → envelope-token mapping
  - generic exception → ``internal_error`` (no traceback in envelope)
  - unknown subcommand → ``unknown_subcommand`` envelope
  - body forwarded to the lib unchanged
  - PostgresClaimKV is not constructed when claim_token is missing
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from src.workspace.lib import (
    WsBoardNotFound,
    WsConfigInvalid,
    WsConfigMissing,
    WsFeedbackIncomplete,
    WsLibError,
    WsMonitorRunFailed,
    WsProbeFailed,
    WsScraperRunFailed,
    cli_shim,
)

# ── Dispatch map / unknown-subcommand handling ───────────────────────


@pytest.mark.asyncio
async def test_unknown_subcommand_returns_envelope() -> None:
    out = await cli_shim.run_envelope({"subcommand": "no-such-thing", "body": {}})
    assert out == {"ok": False, "errors": ["unknown_subcommand"]}


@pytest.mark.asyncio
async def test_invalid_body_type_returns_envelope() -> None:
    out = await cli_shim.run_envelope({"subcommand": "probe_monitor", "body": "not-a-dict"})
    assert out == {"ok": False, "errors": ["invalid_body"]}


# ── Typed-exception mapping (one per error class) ────────────────────


@pytest.mark.parametrize(
    ("exc", "token"),
    [
        (WsBoardNotFound("nope"), "board_not_found"),
        (WsConfigMissing("missing"), "config_missing"),
        (WsConfigInvalid("invalid"), "config_invalid"),
        (WsProbeFailed("pf"), "probe_failed"),
        (WsMonitorRunFailed("mrf"), "monitor_run_failed"),
        (WsScraperRunFailed("srf"), "scraper_run_failed"),
        (WsFeedbackIncomplete("fb"), "feedback_incomplete"),
    ],
)
@pytest.mark.asyncio
async def test_typed_exception_mapping(exc: WsLibError, token: str) -> None:
    async def stub(_state: Any, _expected: Any) -> Any:
        raise exc

    with patch.object(cli_shim, "probe_monitor", stub):
        out = await cli_shim.run_envelope(
            {
                "subcommand": "probe_monitor",
                "body": {"board_url": "https://job-boards.greenhouse.io/x"},
            }
        )
    assert out == {"ok": False, "errors": [token]}


@pytest.mark.asyncio
async def test_generic_exception_maps_to_internal_error() -> None:
    async def stub(_state: Any, _expected: Any) -> Any:
        raise RuntimeError("boom secret token=xxxx")

    with patch.object(cli_shim, "probe_monitor", stub):
        out = await cli_shim.run_envelope(
            {
                "subcommand": "probe_monitor",
                "body": {"board_url": "https://job-boards.greenhouse.io/x"},
            }
        )
    assert out == {"ok": False, "errors": ["internal_error"]}
    # The envelope must not carry the underlying message.
    assert "boom" not in str(out)


# ── Happy path: probe / select dispatch via stub ─────────────────────


@pytest.mark.asyncio
async def test_probe_monitor_happy_path_stub() -> None:
    captured: dict[str, Any] = {}

    class _StubResult:
        def to_dict(self) -> dict[str, Any]:
            return {"sentinel": "probe-monitor"}

    async def stub(state: Any, expected: int) -> Any:
        captured["board_url"] = state.board_url
        captured["expected"] = expected
        return _StubResult()

    with patch.object(cli_shim, "probe_monitor", stub):
        out = await cli_shim.run_envelope(
            {
                "subcommand": "probe_monitor",
                "body": {
                    "board_url": "https://job-boards.greenhouse.io/x",
                    "expected_count": 42,
                },
            }
        )
    assert out == {"ok": True, "data": {"sentinel": "probe-monitor"}}
    assert captured == {
        "board_url": "https://job-boards.greenhouse.io/x",
        "expected": 42,
    }


@pytest.mark.asyncio
async def test_select_monitor_routes_through_kv_when_provided() -> None:
    received: dict[str, Any] = {}

    class _StubKV:
        async def set(self, name: str, value: Any) -> None:
            received[("set", name)] = value

        async def set_active(self, name: str) -> None:
            received["active"] = name

        async def get(self, name: str) -> Any:
            return None

        async def get_active(self) -> str | None:
            return None

    class _StubResult:
        def to_dict(self) -> dict[str, Any]:
            return {"name": "cfg-1", "kind": "monitor"}

    async def stub_select_monitor(kv: Any, monitor_type: str, name: str, config: Any) -> Any:
        received["type"] = monitor_type
        received["name"] = name
        return _StubResult()

    with (
        patch.object(cli_shim, "select_monitor", stub_select_monitor),
        patch.object(cli_shim, "PostgresClaimKV", lambda *a, **kw: _StubKV()),
    ):
        out = await cli_shim.run_envelope(
            {
                "subcommand": "select_monitor",
                "body": {
                    "candidate_id": "cfg-1",
                    "board_url": "https://job-boards.greenhouse.io/x",
                },
                "claim_token": "claim-abc",
                "db_dsn": "postgresql://stub",
            }
        )
    assert out["ok"] is True
    assert received["name"] == "cfg-1"


@pytest.mark.asyncio
async def test_select_monitor_without_kv_returns_config_invalid() -> None:
    out = await cli_shim.run_envelope(
        {
            "subcommand": "select_monitor",
            "body": {
                "candidate_id": "cfg-1",
                "board_url": "https://job-boards.greenhouse.io/x",
            },
            # No claim_token / db_dsn — KV unavailable.
        }
    )
    assert out == {"ok": False, "errors": ["config_invalid"]}


# ── Feedback verdict mapping ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_feedback_maps_demo_verdict_to_lib_vocab() -> None:
    received: dict[str, Any] = {}

    class _StubKV:
        async def set(self, *a: Any, **kw: Any) -> None: ...
        async def set_active(self, *a: Any, **kw: Any) -> None: ...
        async def get(self, *a: Any, **kw: Any) -> Any:
            return None

        async def get_active(self, *a: Any, **kw: Any) -> Any:
            return None

    class _StubResult:
        def to_dict(self) -> dict[str, Any]:
            return {"verdict": "good"}

    async def stub_feedback(kv: Any, verdict: str, **kw: Any) -> Any:
        received["verdict"] = verdict
        return _StubResult()

    with (
        patch.object(cli_shim, "feedback", stub_feedback),
        patch.object(cli_shim, "PostgresClaimKV", lambda *a, **kw: _StubKV()),
    ):
        for demo, lib in (
            ("ok", "good"),
            ("needs-work", "acceptable"),
            ("rejected", "poor"),
        ):
            await cli_shim.run_envelope(
                {
                    "subcommand": "feedback",
                    "body": {"verdict": demo},
                    "claim_token": "claim-abc",
                    "db_dsn": "postgresql://stub",
                }
            )
            assert received["verdict"] == lib
