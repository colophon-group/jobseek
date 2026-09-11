#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

from src.lightpanda.client import LightpandaB0Client, LightpandaServiceConfig
from src.lightpanda.routing import resolve_render_assignment
from src.lightpanda_queue import LightpandaB0Task, RouteIdentity

CREDS = Path("/run/credentials/lightpanda-b0")
TARGET = "https://example.com/"
CREDENTIAL_FILES = (
    "ca.pem",
    "ca.sha256",
    "client-key.pem",
    "client.pem",
    "server-leaf.sha256",
    "server-spki.sha256",
)


def pin(name: str) -> str:
    value = (CREDS / name).read_text(encoding="ascii").strip()
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError("credential pin is invalid")
    return value


def credential_digest(root: Path = CREDS) -> str:
    result = hashlib.sha256()
    for name in CREDENTIAL_FILES:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("credential bundle is invalid")
        result.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return result.hexdigest()


async def probe() -> dict[str, object]:
    assignment = resolve_render_assignment(
        "json-ld",
        {
            "browser_backend": "lightpanda",
            "render": True,
            "routing_revision": "host-acceptance-v1",
            "timeout": 15_000,
            "wait": "load",
            "wait_fallback": None,
        },
    )
    if assignment is None:
        raise RuntimeError("render assignment was not resolved")
    task = LightpandaB0Task.create(
        task_id="host-acceptance-public",
        board_id="host-acceptance",
        source_url=TARGET,
        policy_key="lightpanda-b0-v1",
        domain="example.com",
        route=RouteIdentity(shard_id="lightpanda-b0", routing_epoch=1),
        config_revision=1,
        initial_ready_at_ms=1,
        assignment=assignment,
    )
    client = LightpandaB0Client(
        LightpandaServiceConfig(
            host="10.0.0.5",
            ca_certificate=CREDS / "ca.pem",
            client_certificate=CREDS / "client.pem",
            client_private_key=CREDS / "client-key.pem",
            ca_sha256=pin("ca.sha256"),
            server_leaf_sha256=pin("server-leaf.sha256"),
            server_spki_sha256=pin("server-spki.sha256"),
        )
    )
    result = cast(Any, await client.execute(task))
    success = result.success
    if (
        result.WhichOneof("outcome") != "success"
        or success.status != 200
        or success.final_url != TARGET
        or not success.HasField("html")
        or not success.html.complete
        or success.html.total_size_bytes <= 0
    ):
        raise RuntimeError("public render did not succeed")
    return {
        "status": "accepted",
        "target": TARGET,
        "http_status": 200,
        "credential_bundle_sha256": credential_digest(),
    }


def main() -> int:
    try:
        print(json.dumps(asyncio.run(probe()), sort_keys=True, separators=(",", ":")))
        return 0
    except (OSError, RuntimeError, TimeoutError, ValueError):
        print("Lightpanda mTLS public-render probe failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
