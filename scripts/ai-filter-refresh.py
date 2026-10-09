#!/usr/bin/env python3
"""One bounded, credential-file-authenticated refresh dispatch; no Docker access."""
from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.request

ENDPOINT = "https://jseek.co/api/internal/ai-filter-refresh"
MAX_RESPONSE_BYTES = 16_384


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run(opener=None, credential_directory: str | None = None) -> dict:
    directory = credential_directory or os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not directory:
        raise ValueError("credential_unavailable")
    bearer = (Path(directory) / "refresh-bearer").read_text().strip()
    if not 1 <= len(bearer) <= 1024 or any(ord(c) < 33 or ord(c) > 126 for c in bearer):
        raise ValueError("credential_invalid")
    client = opener or urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(ENDPOINT, headers={
        "Authorization": f"Bearer {bearer}", "Accept": "application/json",
        "User-Agent": "Jobseek-Narrowed-Refresh/1",
    })
    with client.open(request, timeout=55) as response:
        if response.status != 200:
            raise ValueError("refresh_http_failure")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("refresh_response_too_large")
    result = json.loads(payload)
    if result.get("contractVersion") != "narrowed-refresh-v1" or result.get("status") not in ("off", "completed"):
        raise ValueError("refresh_contract_unavailable")
    counters = {}
    for key in ("claimed", "started", "deferred", "failed"):
        value = result.get(key)
        if type(value) is not int or not 0 <= value <= 20:
            raise ValueError("refresh_response_invalid")
        counters[key] = value
    if counters["failed"]:
        raise ValueError("refresh_dispatch_failure")
    return {"status": result["status"], **counters}


def main() -> int:
    try:
        result = run()
    except Exception:
        # Never emit request headers, credential bytes, or an upstream body.
        print(json.dumps({"event": "ai_filter_refresh_trigger", "status": "failed"}))
        return 1
    print(json.dumps({"event": "ai_filter_refresh_trigger", **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
