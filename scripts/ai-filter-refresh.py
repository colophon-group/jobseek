#!/usr/bin/env python3
"""Shared host driver for bounded, credential-file-authenticated worker slices."""
from __future__ import annotations

import json
import os
import argparse
import re
import signal
import threading
from pathlib import Path
import urllib.error
import urllib.request

ENDPOINT = "https://jseek.co/api/internal/ai-filter-refresh"
MAX_RESPONSE_BYTES = 16_384


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def run(opener=None, credential_directory: str | None = None, inspect: str | None = None) -> dict:
    directory = credential_directory or os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not directory:
        raise ValueError("credential_unavailable")
    bearer = (Path(directory) / "refresh-bearer").read_text().strip()
    if not 1 <= len(bearer) <= 1024 or any(ord(c) < 33 or ord(c) > 126 for c in bearer):
        raise ValueError("credential_invalid")
    client = opener or urllib.request.build_opener(NoRedirect())
    if inspect is not None and not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", inspect):
        raise ValueError("invalid_scope")
    endpoint = ENDPOINT if inspect is None else ENDPOINT + "?inspect=" + inspect
    request = urllib.request.Request(endpoint, headers={
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
    if inspect is not None:
        recovery = result.get("recovery", {})
        if result.get("contractVersion") != "narrowed-worker-v2" or recovery.get("legacyWorkersFenced") is not True:
            raise ValueError("recovery_contract_unavailable")
        return {"status": "inspected", "recovery": {key: recovery.get(key) for key in (
            "status", "queryRevision", "queryVersionId", "filterFingerprint", "candidateLanguages", "entitled", "reviewed", "accepted", "remaining", "coverage", "legacyWorkersFenced")}}
    if result.get("contractVersion") != "narrowed-worker-v2" or result.get("status") not in ("off", "completed"):
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
        parser = argparse.ArgumentParser()
        parser.add_argument("--inspect")
        parser.add_argument("--worker", action="store_true")
        arguments = parser.parse_args()
        if arguments.worker:
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            serve(stop=stop)
            return 0
        result = run(inspect=arguments.inspect)
    except Exception:
        # Never emit request headers, credential bytes, or an upstream body.
        print(json.dumps({"event": "ai_filter_refresh_trigger", "status": "failed"}))
        return 1
    print(json.dumps({"event": "ai_filter_refresh_trigger", **result}))
    return 0


def serve(run_once=None, stop=None) -> None:
    """One project driver. Active demand continues; idle work backs off cheaply.

    Each HTTPS request and response retains run()'s bounds. There is no per-scope
    loop, database credential or Docker access here. Owner wakes use the same
    server-side singleton; the host recovers missed wakes and crashed requests.
    """
    execute = run_once or run
    stopped = stop or threading.Event()
    failures = 0
    previous = None
    while not stopped.is_set():
        try:
            result = execute()
            failures = 0
            busy = result["started"] > 0 or result["claimed"] > 0
            delay = 2 if busy else 30
            status = "active" if busy else "idle"
            if busy or previous != status:
                print(json.dumps({"event": "ai_filter_refresh_trigger", **result}), flush=True)
            previous = status
        except Exception:
            failures += 1
            delay = min(900, 30 * 2 ** min(failures, 5))
            print(json.dumps({"event": "ai_filter_refresh_trigger", "status": "failed"}), flush=True)
            previous = "failed"
        stopped.wait(delay)


if __name__ == "__main__":
    raise SystemExit(main())
