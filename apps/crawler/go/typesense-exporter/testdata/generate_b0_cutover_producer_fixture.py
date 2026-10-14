"""Offline administrative replies from the retained Python producer decoder."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.lightpanda.producer_client import PROTOCOL, ProducerClientError, _canonical, _decode

BASE = {
    "activated": False,
    "board_slugs": [],
    "cohort": "",
    "existing_payload_sha256": "",
    "existing_state": "",
    "lifetime_capacity": 2048,
    "lifetime_headroom": 2048,
    "lifetime_occupancy": 0,
    "outcome": "legacy",
    "payload_sha256": "",
    "preparation_digest": "",
    "reason": "literal_legacy",
    "version": PROTOCOL,
}
cases = [("literal-legacy", BASE)]
for cohort in ("c1", "c2", "c3", "c4", "cdom"):
    cases.append(
        (
            f"manifest-{cohort}",
            {
                **BASE,
                "outcome": "manifest",
                "reason": "manifest",
                "cohort": cohort,
                "board_slugs": ["browser-use-careers", "kandou-ai-careers"],
            },
        )
    )
for outcome, reason in (
    ("prepared", "prepared"),
    ("activated", "activated"),
    ("activated", "reactivated"),
    ("activated", "already_activated"),
):
    for state in ("", "ready", "inflight", "dead", "terminal"):
        value = {
            **BASE,
            "outcome": outcome,
            "reason": reason,
            "preparation_digest": "a" * 64,
            "payload_sha256": "b" * 64,
            "existing_state": state,
            "existing_payload_sha256": "c" * 64 if state else "",
            "activated": reason in {"activated", "reactivated"},
        }
        cases.append((f"{outcome}-{reason}-{state or 'empty'}", value))
for index, (field, invalid) in enumerate(
    (
        ("outcome", "other"),
        ("reason", "other"),
        ("activated", 1),
        ("board_slugs", None),
        ("cohort", 1),
        ("existing_state", "foreign"),
        ("existing_payload_sha256", ""),
        ("preparation_digest", "A" * 64),
        ("payload_sha256", "f" * 63),
        ("lifetime_capacity", 2047),
        ("lifetime_occupancy", -1),
        ("lifetime_headroom", 2047),
        ("lifetime_occupancy", True),
        ("extra", True),
    )
):
    cases.append((f"invalid-{field}-{index}", {**value, field: invalid}))
manifest = {**BASE, "outcome": "manifest", "reason": "manifest", "cohort": "c1"}
for name, change in (
    ("empty", {}),
    ("duplicate", {"board_slugs": ["a", "a"]}),
    ("unsorted", {"board_slugs": ["b", "a"]}),
    ("foreign-cohort", {"board_slugs": ["a", "b"], "cohort": "unknown"}),
    ("activated", {"board_slugs": ["a", "b"], "activated": True}),
    ("digest", {"board_slugs": ["a", "b"], "payload_sha256": "b" * 64}),
):
    cases.append((f"invalid-manifest-{name}", {**manifest, **change}))
for reason in ("request_invalid", "authority_lost", "digest_mismatch", "other"):
    cases.append((f"error-{reason}", {**BASE, "outcome": "error", "reason": reason}))
for reason, occupancy in (
    ("namespace_full", 2048),
    ("pilot_occupancy_limit", 1600),
    ("unknown", 0),
):
    cases.append(
        (
            f"capacity-{reason}",
            {
                **BASE,
                "outcome": "capacity",
                "reason": reason,
                "lifetime_occupancy": occupancy,
                "lifetime_headroom": 2048 - occupancy,
            },
        )
    )
for length in (128, 129):
    cases.append(
        (
            f"manifest-unicode-length-{length}",
            {
                **manifest,
                "board_slugs": ["ü" * length],
            },
        )
    )
raw_cases = [
    ("noncanonical", json.dumps(BASE).encode()),
    ("duplicate-field", _canonical(BASE).replace(b"{", b'{"activated":false,', 1)),
]
vectors = []
for name, payload in [(name, _canonical(value)) for name, value in cases] + raw_cases:
    vector = {"name": name, "payload_hex": payload.hex()}
    try:
        result = _decode(payload)
    except ProducerClientError:
        vector["accepted"] = False
    else:
        vector["accepted"] = True
        vector["decision"] = dataclasses.asdict(result)
    vectors.append(vector)
Path(__file__).with_name("python_b0_cutover_producer.json").write_text(
    json.dumps(
        {"schema": "jobseek.b0-cutover-producer-original/v1", "vectors": vectors},
        ensure_ascii=True,
        indent=2,
    )
    + "\n"
)
