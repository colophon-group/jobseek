"""Freeze actual original B0 sentinel decisions and a synthetic task envelope."""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

CRAWLER = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(CRAWLER))
sys.path.insert(0, str(CRAWLER / "tests"))

from test_lightpanda_queue import task  # noqa: E402

from src.lightpanda import activation as original  # noqa: E402
from src.lightpanda_queue import RouteIdentity  # noqa: E402


def generate() -> bytes:
    identity = {"namespace": "cold-fixture", "shard": "lightpanda-b0", "cohort": "c1", "epoch": 7}
    original.settings.lightpanda_b0_queue_namespace = identity["namespace"]
    original.settings.lightpanda_b0_shard_id = identity["shard"]
    original.settings.lightpanda_b0_routing_epoch = str(identity["epoch"])
    original._PRODUCER_AUTHORITY_UID = os.getuid()
    document = {
        "board_slugs": ["browser-use-careers"],
        "cohort": "c1",
        "engine_owner": "go",
        "namespace": identity["namespace"],
        "routing_epoch": 7,
        "schema": "jobseek.lightpanda.producer-activation/v1",
        "shard_id": identity["shard"],
    }
    rows = []
    with tempfile.TemporaryDirectory(prefix="b0-original-marker-") as directory:
        original._PRODUCER_AUTHORITY_DIRECTORY = Path(directory)

        def one(name: str, wire: bytes) -> None:
            marker = Path(directory) / ".activation-v1"
            marker.write_bytes(wire)
            marker.chmod(0o600)
            rows.append(
                {
                    "name": name,
                    "payload_hex": wire.hex(),
                    "phase": original._producer_marker_phase("c1"),
                }
            )

        for name, wire in [
            ("empty", b""),
            ("partial-prefix", b"P"),
            ("partial-object", b"P\n{"),
            ("active-truncated", b"A\n{"),
            ("invalid-prefix", b"X\n{}\n"),
            ("invalid-json", b"A\n{\n"),
            ("trailing-json", b"A\n{}{}\n"),
        ]:
            one(name, wire)
        for phase in (b"P", b"A"):
            one(
                phase.decode() + "-valid",
                phase + b"\n" + original._canonical(document)[0].encode() + b"\n",
            )
            one(
                phase.decode() + "-noncanonical",
                phase + b"\n" + json.dumps(document).encode() + b"\n",
            )
            for key, value in [
                ("cohort", "c2"),
                ("engine_owner", "python"),
                ("namespace", "other"),
                ("routing_epoch", 8),
                ("shard_id", "other"),
                ("schema", "unknown"),
                ("board_slugs", []),
                ("board_slugs", ["z", "a"]),
                ("board_slugs", ["a", "a"]),
                ("board_slugs", ["bad slug"]),
                ("board_slugs", ["é"]),
            ]:
                altered = copy.deepcopy(document)
                altered[key] = value
                one(
                    phase.decode() + "-" + key + "-" + str(len(rows)),
                    phase + b"\n" + original._canonical(altered)[0].encode() + b"\n",
                )
            altered = copy.deepcopy(document)
            altered["extra"] = True
            one(
                phase.decode() + "-extra",
                phase + b"\n" + original._canonical(altered)[0].encode() + b"\n",
            )
    fixture = {
        "schema": "jobseek.b0-cold-rollback-original/v1",
        "identity": identity,
        "marker_cases": rows,
        "task_payload": task(RouteIdentity("lightpanda-b0", 7, "go")).payload,
    }
    return (json.dumps(fixture, indent=2, ensure_ascii=True) + "\n").encode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    destination = Path(__file__).with_name("python_b0_cold_rollback.json")
    payload = generate()
    if args.check:
        if destination.read_bytes() != payload:
            raise SystemExit("original B0 cold rollback corpus differs")
    else:
        destination.write_bytes(payload)


if __name__ == "__main__":
    main()
