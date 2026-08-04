#!/usr/bin/env python3
"""Verify predictable Redis noeviction failure on an explicitly disposable DB."""

from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlparse

import redis

MAX_TEST_MAXMEMORY = 64 * 1024 * 1024
VALUE_BYTES = 64 * 1024


class SafetyError(RuntimeError):
    """The target does not satisfy the disposable-instance guardrails."""


def _validate_target(url: str, client: redis.Redis) -> dict[str, int | str]:
    parsed = urlparse(url)
    database = int((parsed.path or "/0").removeprefix("/") or "0")
    if parsed.scheme not in {"redis", "rediss"}:
        raise SafetyError("target must use redis:// or rediss://")
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise SafetyError("target must be loopback")
    if (parsed.port or 6379) == 6379:
        raise SafetyError("target must not use the production/default Redis port 6379")
    if database < 14:
        raise SafetyError("target must use disposable logical DB 14 or 15")
    if client.dbsize() != 0:
        raise SafetyError("target DB must be empty")

    maxmemory = int(client.config_get("maxmemory").get("maxmemory", 0))
    policy = str(client.config_get("maxmemory-policy").get("maxmemory-policy", ""))
    if not 0 < maxmemory <= MAX_TEST_MAXMEMORY:
        raise SafetyError("target maxmemory must be configured at <=64 MiB")
    if policy != "noeviction":
        raise SafetyError("target maxmemory-policy must be noeviction")
    return {"database": database, "maxmemory_bytes": maxmemory, "policy": policy}


def run(url: str) -> dict[str, int | str | bool]:
    client = redis.Redis.from_url(url, decode_responses=False, socket_timeout=5)
    target = _validate_target(url, client)
    value = b"x" * VALUE_BYTES
    written = 0
    rejected = False
    rejection = ""
    evicted_before = int(client.info("stats").get("evicted_keys", 0))

    try:
        client.set("jobseek-pressure:sentinel", "preserve-me")
        for index in range(10_000):
            try:
                client.set(f"jobseek-pressure:fill:{index}", value)
                written += 1
            except redis.exceptions.ResponseError as exc:
                rejection = str(exc)
                rejected = "memory" in rejection.lower() or "oom" in rejection.lower()
                break
        if not rejected:
            raise RuntimeError("writes were not rejected before the bounded fill limit")
        if client.get("jobseek-pressure:sentinel") != b"preserve-me":
            raise RuntimeError("noeviction failed to preserve the existing sentinel")
        evicted_after = int(client.info("stats").get("evicted_keys", 0))
        if evicted_after != evicted_before:
            raise RuntimeError("noeviction unexpectedly evicted keys")
    finally:
        keys = list(client.scan_iter(match="jobseek-pressure:*", count=1000))
        for start in range(0, len(keys), 500):
            client.unlink(*keys[start : start + 500])

    recovered = bool(client.set("jobseek-pressure:post-cleanup", "ok"))
    client.delete("jobseek-pressure:post-cleanup")
    if not recovered:
        raise RuntimeError("writes did not recover after bounded cleanup")

    return {
        **target,
        "value_bytes": VALUE_BYTES,
        "writes_before_rejection": written,
        "write_rejected": rejected,
        "existing_key_preserved": True,
        "evicted_keys_delta": 0,
        "write_recovered_after_cleanup": recovered,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("REDIS_PRESSURE_TEST_URL"))
    args = parser.parse_args()
    if not args.url:
        raise SystemExit("--url or REDIS_PRESSURE_TEST_URL is required")
    print(json.dumps(run(args.url), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
