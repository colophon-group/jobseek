"""Capture the existing Python producer client's wire decisions without networking."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.lightpanda import producer_client as client


def response(**changes: object) -> dict[str, object]:
    value: dict[str, object] = {
        "version": client.PROTOCOL,
        "outcome": "legacy",
        "reason": "literal_legacy",
        "preparation_digest": "",
        "payload_sha256": "",
        "existing_state": "",
        "existing_payload_sha256": "",
        "activated": False,
        "cohort": "",
        "board_slugs": [],
        "lifetime_occupancy": 0,
        "lifetime_capacity": 2048,
        "lifetime_headroom": 2048,
    }
    value.update(changes)
    return value


def collect_responses() -> list[dict[str, object]]:
    prepared = response(
        outcome="prepared",
        reason="prepared",
        preparation_digest="a" * 64,
        payload_sha256="b" * 64,
    )
    values: list[tuple[str, bytes]] = []

    def add(name: str, value: dict[str, object]) -> None:
        values.append((name, client._canonical(value)))

    add("legacy", response())
    for cohort, slugs in {
        "c1": ["browser-use-careers"],
        "c2": ["browser-use-careers", "kandou-ai-careers"],
        "cdom": ["algorized-careers", "browser-use-careers", "bunq-careers"],
    }.items():
        add(
            cohort,
            response(outcome="manifest", reason="manifest", cohort=cohort, board_slugs=slugs),
        )
    for state in ["", "ready", "inflight", "dead", "terminal"]:
        add(
            "prepared-" + (state or "new"),
            dict(prepared, existing_state=state, existing_payload_sha256="c" * 64 if state else ""),
        )
    for reason in ["activated", "reactivated", "already_activated"]:
        add(
            reason,
            dict(
                prepared,
                outcome="activated",
                reason=reason,
                activated=reason != "already_activated",
            ),
        )
    for reason in ["request_invalid", "authority_lost", "digest_mismatch"]:
        add(reason, response(outcome="error", reason=reason))
    for reason, occupancy in [("namespace_full", 2048), ("pilot_occupancy_limit", 1600)]:
        add(
            reason,
            response(
                outcome="capacity",
                reason=reason,
                lifetime_occupancy=occupancy,
                lifetime_headroom=2048 - occupancy,
            ),
        )
    for name, changed in {
        "unknown-version": {"version": "secret"},
        "unknown-field": {"secret": "input"},
        "activated-legacy": {"activated": True},
        "wrong-reason": {"reason": "authority_lost"},
        "boolean-count": {"lifetime_occupancy": True},
        "negative-count": {"lifetime_occupancy": -1, "lifetime_headroom": 2049},
        "over-capacity": {"lifetime_occupancy": 2049, "lifetime_headroom": -1},
        "bad-headroom": {"lifetime_headroom": 0},
        "bad-capacity": {"lifetime_capacity": 2049},
        "unknown-outcome": {"outcome": "secret"},
        "legacy-with-digest": {"preparation_digest": "a" * 64},
    }.items():
        add(name, response(**changed))
    for name, changed in {
        "unknown-existing": {"existing_state": "secret", "existing_payload_sha256": "c" * 64},
        "missing-existing-hash": {"existing_state": "ready"},
        "orphan-existing-hash": {"existing_payload_sha256": "c" * 64},
        "prepared-activated": {"activated": True},
        "wrong-digest": {"preparation_digest": "secret"},
        "prepared-wrong-reason": {"reason": "activated"},
    }.items():
        add(name, dict(prepared, **changed))
    for name, slugs in [("unsorted", ["z", "a"]), ("duplicate", ["a", "a"]), ("empty", [])]:
        add(name, response(outcome="manifest", reason="manifest", cohort="c1", board_slugs=slugs))
    missing = response()
    del missing["reason"]
    add("missing-field", missing)
    body = client._canonical(response())
    values.extend(
        [
            ("duplicate-field", body[:-1] + b',"reason":"literal_legacy"}'),
            ("trailing-json", body + b"{}"),
            ("noncanonical", body + b" "),
            ("wrong-boolean", body.replace(b'"activated":false', b'"activated":"false"')),
        ]
    )
    cases: list[dict[str, object]] = []
    for name, body in values:
        wanted: dict[str, object] = {"name": name, "body": body.decode("ascii")}
        try:
            result = client._decode(body)
            wanted.update(outcome=result.outcome, reason="", accepted=True)
        except client.ProducerCapacityError as exc:
            wanted.update(accepted=False, capacity=True, reason=exc.reason)
        except client.ProducerClientError:
            wanted.update(accepted=False, capacity=False, reason="")
        cases.append(wanted)
    return cases


async def collect_requests() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []
    original_enabled, original_exchange = client._enabled, client._exchange

    async def capture(request: dict[str, object]) -> client.ProducerResult:
        cases.append({"request": request, "body": client._canonical(request).decode("ascii")})
        return client.ProducerResult("legacy")

    client._enabled, client._exchange = lambda: True, capture
    try:
        await client.request_manifest("cdom")
        config = {
            "board_id": "11111111-1111-4111-8111-111111111111",
            "source_url": "https://jobs.example.test/é/<posting>",
            "scrape_step": "0",
            "description_r2_hash": "-9223372036854775808",
            "scrape_interval_hours": "24",
        }
        for operation, first_time, score, due in [
            ("prepare", True, "350.0001", 350.0001),
            ("prepare", False, "1925089445.100001", 1925089445.100001),
            ("activate", False, "1925089445.100001", 1925089445.100001),
            ("enqueue", False, "", 0),
        ]:
            await client.request_task(
                operation=operation,
                domain="jobs.example.test",
                posting_id="00000000-0000-4000-8000-000000000001",
                next_scrape_at=due,
                config=config,
                browser=True,
                first_time=first_time,
                operator_transfer=operation != "enqueue",
                expected_digest="a" * 64 if operation == "activate" else "",
                legacy_schedule_score=score,
            )
    finally:
        client._enabled, client._exchange = original_enabled, original_exchange
    return cases


if __name__ == "__main__":
    document = {"responses": collect_responses(), "requests": asyncio.run(collect_requests())}
    Path(__file__).with_name("python_protocol.json").write_text(
        json.dumps(document, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
