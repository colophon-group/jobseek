"""JSON-in / JSON-out entry point for jobseek's HTTP shim routes.

Spawned by `apps/web/app/api/murmur/_lib/invoke-lib.ts` (pattern (a) per
J5's IPC decision). Reads a single JSON object on stdin::

    {
      "subcommand": "probe_monitor" | "run_monitor" | ...,
      "body":       { ... },              # request body validated by TS
      "claim_token": "<opaque token>",
      "db_dsn":     "postgresql://..."    # for select/feedback/run/probe
    }

and writes a single JSON envelope on stdout::

    { "ok": true,  "data": { ... } }
    { "ok": false, "errors": [ "<token>", ... ] }

Exit code is always 0 on a clean envelope (success OR known typed
failure). Non-zero exit codes plus uncaught exceptions are reserved for
catastrophic shim bugs — the TS side maps those to
``{"ok": false, "errors": ["internal_error"]}``.

Error mapping (typed exception → envelope token)::

    WsBoardNotFound          -> "board_not_found"
    WsConfigMissing          -> "config_missing"
    WsConfigInvalid          -> "config_invalid"
    WsProbeFailed            -> "probe_failed"
    WsMonitorRunFailed       -> "monitor_run_failed"
    WsScraperRunFailed       -> "scraper_run_failed"
    WsFeedbackIncomplete     -> "feedback_incomplete"
    Exception (other)        -> "internal_error" (full trace -> stderr)

@see colophon-group/jobseek#2759
"""

from __future__ import annotations

import asyncio
import json
import sys
import traceback
from typing import Any, Awaitable, Callable

from src.workspace.lib import (
    BoardConfigState,
    WsBoardNotFound,
    WsConfigInvalid,
    WsConfigMissing,
    WsFeedbackIncomplete,
    WsLibError,
    WsMonitorRunFailed,
    WsProbeFailed,
    WsScraperRunFailed,
    feedback,
    probe_monitor,
    probe_scraper,
    run_monitor,
    run_scraper,
    select_monitor,
    select_scraper,
)
from src.workspace.lib.postgres_claim_kv import PostgresClaimKV


# ── Error mapping ─────────────────────────────────────────────────────


_ERROR_TOKENS: dict[type[BaseException], str] = {
    WsBoardNotFound: "board_not_found",
    WsConfigMissing: "config_missing",
    WsConfigInvalid: "config_invalid",
    WsProbeFailed: "probe_failed",
    WsMonitorRunFailed: "monitor_run_failed",
    WsScraperRunFailed: "scraper_run_failed",
    WsFeedbackIncomplete: "feedback_incomplete",
}


def _map_exception(exc: BaseException) -> str:
    for cls, token in _ERROR_TOKENS.items():
        if isinstance(exc, cls):
            return token
    if isinstance(exc, WsLibError):
        # An unmapped lib exception subclass — treat as internal_error
        # but log so we notice and add a token next time.
        return "internal_error"
    return "internal_error"


# ── Subcommand dispatchers ────────────────────────────────────────────


async def _do_probe_monitor(
    body: dict[str, Any], _kv: PostgresClaimKV | None
) -> dict[str, Any]:
    state = BoardConfigState(board_url=body["board_url"])
    expected = int(body.get("expected_count", 0) or 0)
    result = await probe_monitor(state, expected)
    return result.to_dict()


async def _do_probe_scraper(
    body: dict[str, Any], _kv: PostgresClaimKV | None
) -> dict[str, Any]:
    state = BoardConfigState(board_url=body["board_url"])
    sample_url = body.get("sample_job_url")
    result = await probe_scraper(state, sample_url=sample_url)
    return result.to_dict()


async def _do_run_monitor(
    body: dict[str, Any], kv: PostgresClaimKV | None
) -> dict[str, Any]:
    if kv is None:
        raise WsConfigInvalid("run_monitor: claim_kv unavailable")
    active = await kv.get_active()
    slot: dict[str, Any] = {}
    if active:
        s = await kv.get(active)
        if isinstance(s, dict):
            slot = s
    state = BoardConfigState(
        board_url=body["board_url"],
        monitor_type=slot.get("monitor_type"),
        monitor_config=dict(slot.get("monitor_config") or {}),
    )
    result = await run_monitor(state)
    return result.to_dict()


async def _do_run_scraper(
    body: dict[str, Any], kv: PostgresClaimKV | None
) -> dict[str, Any]:
    if kv is None:
        raise WsConfigInvalid("run_scraper: claim_kv unavailable")
    active = await kv.get_active()
    slot: dict[str, Any] = {}
    if active:
        s = await kv.get(active)
        if isinstance(s, dict):
            slot = s
    sample_urls: list[str] | None = None
    if "sample_job_url" in body and body["sample_job_url"]:
        sample_urls = [body["sample_job_url"]]
    state = BoardConfigState(
        board_url=body["board_url"],
        scraper_type=slot.get("scraper_type"),
        scraper_config=dict(slot.get("scraper_config") or {}),
    )
    result = await run_scraper(state, sample_urls=sample_urls)
    return result.to_dict()


async def _do_select_monitor(
    body: dict[str, Any], kv: PostgresClaimKV | None
) -> dict[str, Any]:
    if kv is None:
        raise WsConfigInvalid("select_monitor: claim_kv unavailable")
    # The agent supplies `candidate_id` rather than a (monitor_type,
    # monitor_config) pair; for the demo we store `candidate_id` as the
    # monitor type so the named slot survives the round trip. A
    # production implementation would resolve `candidate_id` against
    # the prior probe's candidate list.
    name = body["candidate_id"]
    result = await select_monitor(kv, monitor_type=name, name=name, config={})
    return result.to_dict()


async def _do_select_scraper(
    body: dict[str, Any], kv: PostgresClaimKV | None
) -> dict[str, Any]:
    if kv is None:
        raise WsConfigInvalid("select_scraper: claim_kv unavailable")
    name = body["candidate_id"]
    result = await select_scraper(kv, scraper_type=name, name=name, config={})
    return result.to_dict()


async def _do_feedback(
    body: dict[str, Any], kv: PostgresClaimKV | None
) -> dict[str, Any]:
    if kv is None:
        raise WsConfigInvalid("feedback: claim_kv unavailable")
    verdict = body["verdict"]
    # The YAML's verdict enum (`ok` / `needs-work` / `rejected`) differs
    # from the lib's tighter vocabulary (`good` / `acceptable` / `poor`
    # / `unusable`). Map demo-vocab → lib-vocab one way:
    verdict_map = {"ok": "good", "needs-work": "acceptable", "rejected": "poor"}
    lib_verdict = verdict_map.get(verdict, verdict)
    notes = body.get("notes", "")
    per_field = body.get("per_field") or None
    if isinstance(per_field, dict):
        # Coerce the demo's `{field: "clean"}` shorthand to the lib's
        # `{field: {"quality": "clean"}}` shape if needed.
        normalised: dict[str, dict[str, str]] = {}
        for k, v in per_field.items():
            if isinstance(v, dict):
                normalised[k] = {kk: str(vv) for kk, vv in v.items()}
            elif isinstance(v, str):
                normalised[k] = {"quality": v}
        per_field = normalised
    result = await feedback(
        kv, verdict=lib_verdict, per_field=per_field, verdict_notes=notes
    )
    return result.to_dict()


_DISPATCH: dict[
    str,
    Callable[[dict[str, Any], PostgresClaimKV | None], Awaitable[dict[str, Any]]],
] = {
    "probe_monitor": _do_probe_monitor,
    "probe_scraper": _do_probe_scraper,
    "run_monitor": _do_run_monitor,
    "run_scraper": _do_run_scraper,
    "select_monitor": _do_select_monitor,
    "select_scraper": _do_select_scraper,
    "feedback": _do_feedback,
}


# ── Top-level entry point ─────────────────────────────────────────────


async def run_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute the requested subcommand and return an M0 envelope dict."""
    subcommand = payload.get("subcommand")
    body = payload.get("body") or {}
    claim_token = payload.get("claim_token") or ""
    dsn = payload.get("db_dsn") or ""

    if not isinstance(subcommand, str) or subcommand not in _DISPATCH:
        return {"ok": False, "errors": ["unknown_subcommand"]}
    if not isinstance(body, dict):
        return {"ok": False, "errors": ["invalid_body"]}

    kv: PostgresClaimKV | None = None
    if claim_token and dsn:
        kv = PostgresClaimKV(claim_token=claim_token, dsn=dsn)

    handler = _DISPATCH[subcommand]
    try:
        data = await handler(body, kv)
    except WsLibError as exc:
        token = _map_exception(exc)
        # Log full trace to stderr; the TS side only ever sees the token.
        traceback.print_exc(file=sys.stderr)
        return {"ok": False, "errors": [token]}
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return {"ok": False, "errors": ["internal_error"]}
    return {"ok": True, "data": data}


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        sys.stdout.write(json.dumps({"ok": False, "errors": ["invalid_json"]}))
        return 0
    if not isinstance(payload, dict):
        sys.stdout.write(json.dumps({"ok": False, "errors": ["invalid_payload"]}))
        return 0
    result = asyncio.run(run_envelope(payload))
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
