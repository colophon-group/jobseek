#!/usr/bin/env python3
"""Probe ChatGPT Codex usage windows from local Codex OAuth auth.

This is a deliberately narrow experiment around the same unofficial endpoint
used by pi-chatgpt-limit. It prints only normalized, non-secret fields.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_ENDPOINT = "https://chatgpt.com/backend-api/wham/usage"
OPENAI_AUTH_CLAIM = "https://api.openai.com/auth"
FIVE_HOURS = 5 * 60 * 60
ONE_WEEK = 7 * 24 * 60 * 60


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise SystemExit(f"auth file not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"auth file is not valid JSON: {path}: {exc}") from None
    if not isinstance(data, dict):
        raise SystemExit(f"auth file must contain a JSON object: {path}")
    return data


def _jwt_payload(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(decoded)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _token_from_auth_file(path: Path) -> tuple[str, str | None]:
    data = _read_json(path)
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        raise SystemExit(f"auth file has no tokens object: {path}")
    token = tokens.get("access_token")
    if not isinstance(token, str) or not token:
        raise SystemExit(f"auth file has no access_token: {path}")
    account_id = tokens.get("account_id")
    return token, account_id if isinstance(account_id, str) else None


def _metadata_from_token(token: str) -> dict[str, Any]:
    payload = _jwt_payload(token)
    auth = payload.get(OPENAI_AUTH_CLAIM)
    return auth if isinstance(auth, dict) else {}


def _classify_window(window_seconds: int | None) -> str:
    if window_seconds is None:
        return "unknown"
    if abs(window_seconds - FIVE_HOURS) <= 120:
        return "five_hour"
    if abs(window_seconds - ONE_WEEK) <= 120:
        return "weekly"
    return f"{window_seconds}s"


def _normalize_window(raw: Any, now: int) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    used = raw.get("used_percent")
    window_seconds = raw.get("limit_window_seconds")
    reset_at = raw.get("reset_at")
    if not isinstance(used, (int, float)):
        return None
    if not isinstance(window_seconds, int):
        window_seconds = None
    if not isinstance(reset_at, int):
        reset_at = None
    remaining = max(0.0, min(100.0, 100.0 - float(used)))
    return {
        "name": _classify_window(window_seconds),
        "used_percent": round(float(used), 3),
        "remaining_percent": round(remaining, 3),
        "window_seconds": window_seconds,
        "reset_at": reset_at,
        "reset_in_seconds": max(0, reset_at - now) if reset_at else None,
    }


def _redact_email(value: Any) -> str | None:
    if not isinstance(value, str) or "@" not in value:
        return None
    _, domain = value.rsplit("@", 1)
    return f"<redacted>@{domain}"


def _normalize_response(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    now = int(time.time())
    rate_limit = data.get("rate_limit")
    if not isinstance(rate_limit, dict):
        rate_limit = {}
    windows = [
        _normalize_window(rate_limit.get("primary_window"), now),
        _normalize_window(rate_limit.get("secondary_window"), now),
    ]
    return {
        "ok": True,
        "source": "chatgpt_wham_usage",
        "fetched_at": now,
        "plan_type": data.get("plan_type") if isinstance(data.get("plan_type"), str) else None,
        "email": _redact_email(data.get("email")),
        "windows": [window for window in windows if window is not None],
    }


def _ssl_context(ca_file: str | None) -> ssl.SSLContext | None:
    if not ca_file:
        return None
    return ssl.create_default_context(cafile=ca_file)


def _request(
    endpoint: str,
    token: str,
    account_id: str | None,
    timeout: float,
    ca_file: str | None,
) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "jobseek-codex-usage-probe",
    }
    if account_id:
        headers["chatgpt-account-id"] = account_id
    req = urllib.request.Request(endpoint, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context(ca_file)) as resp:
            body = resp.read()
            return {"status": resp.status, "headers": dict(resp.headers), "body": body}
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return {"status": exc.code, "headers": dict(exc.headers), "body": body}
    except urllib.error.URLError as exc:
        return {"status": None, "headers": {}, "body": b"", "transport_error": str(exc.reason)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Codex subscription usage windows.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--auth-file", default=str(Path.home() / ".codex" / "auth.json"))
    parser.add_argument("--ca-file", default=os.environ.get("SSL_CERT_FILE"))
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--bearer-env",
        default="CODEX_USAGE_BEARER_TOKEN",
        help="optional env var containing an explicit bearer token",
    )
    args = parser.parse_args()

    token = os.environ.get(args.bearer_env)
    account_id: str | None = None
    if token:
        metadata = _metadata_from_token(token)
        account_id = metadata.get("chatgpt_account_id")
        if not isinstance(account_id, str):
            account_id = None
    else:
        token, account_id = _token_from_auth_file(Path(args.auth_file).expanduser())

    if not account_id:
        metadata = _metadata_from_token(token)
        account_id = metadata.get("chatgpt_account_id")
        if not isinstance(account_id, str):
            account_id = None

    result = _request(args.endpoint, token, account_id, args.timeout, args.ca_file)
    status = result["status"]
    body = result["body"]
    transport_error = result.get("transport_error")
    if transport_error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": status,
                    "transport_error": transport_error,
                },
                sort_keys=True,
            )
        )
        return 1

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = None

    if status != 200:
        error_type = None
        reset_in = None
        if isinstance(parsed, dict):
            error = parsed.get("error")
            if isinstance(error, dict):
                error_type = error.get("type")
                reset_in = error.get("resets_in_seconds")
        print(
            json.dumps(
                {
                    "ok": False,
                    "status": status,
                    "error_type": error_type,
                    "resets_in_seconds": reset_in if isinstance(reset_in, int) else None,
                },
                sort_keys=True,
            )
        )
        return 1

    try:
        normalized = _normalize_response(parsed)
    except ValueError as exc:
        print(json.dumps({"ok": False, "status": status, "error": str(exc)}, sort_keys=True))
        return 1

    print(json.dumps(normalized, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
