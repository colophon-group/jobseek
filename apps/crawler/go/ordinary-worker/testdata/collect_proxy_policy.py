"""Freeze the actual Python Webshare policy, using synthetic endpoint identities."""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import random
from pathlib import Path

from src.shared.proxy import PoolProxyProvider, ProxyPoolExhaustedError


def health(value):
    return [
        value.failures,
        value.quarantined_until,
        value.probe_due,
        value.probe_in_flight,
        value.generation,
    ]


def freeze(name, size, forced, operations):
    clock = [0.0]
    provider = PoolProxyProvider(
        "webshare",
        tuple(f"http://synthetic-{slot}.invalid:8080" for slot in range(size)),
        clock=lambda: clock[0],
        forced_slot=forced,
    )
    leases = {}
    steps = []
    for operation in operations:
        action = operation["action"]
        result = None
        if action == "time":
            clock[0] = operation["at"]
        elif action == "select":
            try:
                lease = provider.select(origin=operation["origin"], transport="httpx")
                leases[operation["id"]] = lease
                result = [
                    lease.pool_slot,
                    lease.half_open,
                    lease._global_generation,
                    lease._origin_generation,
                    lease._global_probe,
                    lease._origin_probe,
                ]
            except ProxyPoolExhaustedError:
                result = "exhausted"
        elif operation["id"] in leases:
            lease = leases[operation["id"]]
            if action == "failure":
                provider.report_failure(
                    lease, origin=operation["origin"], reason=operation["reason"]
                )
            elif action == "success":
                provider.report_success(lease, origin=operation["origin"])
            elif action == "abandon":
                provider.abandon(lease, origin=operation["origin"])
            else:
                raise AssertionError(action)
        steps.append(
            {
                **operation,
                "result": result,
                "state": {
                    "cursor": provider._cursor,
                    "global": [health(h) for h in provider._global_health],
                    "origins": [
                        [slot, origin, health(h)]
                        for (slot, origin), h in provider._origin_health.items()
                    ],
                    "evidence": provider._transport_failure_origins,
                },
            }
        )
        # Evidence dictionaries mutate in place; preserve each observation now.
        steps[-1] = json.loads(json.dumps(steps[-1]))
    return {"name": name, "size": size, "forced": forced, "steps": steps}


def select(identifier, origin="https://a.example"):
    return {"action": "select", "id": identifier, "origin": origin}


def report(action, identifier, origin="https://a.example", reason=None):
    operation = {"action": action, "id": identifier, "origin": origin}
    if reason is not None:
        operation["reason"] = reason
    return operation


def main():
    cases = []
    with contextlib.redirect_stdout(io.StringIO()):
        for reason, cooldown in (
            ("proxy_auth", 3600),
            ("proxy_transport", 120),
            ("origin_block", 900),
            ("origin_transport", 120),
        ):
            for resolution in ("success", "abandon", "failure"):
                operations = [
                    select("old"),
                    select("first"),
                    report("failure", "first", reason=reason),
                    select("cooling"),
                    report("success", "old"),
                    {"action": "time", "at": cooldown},
                    select("probe"),
                    select("parallel"),
                    report(resolution, "probe", reason=reason),
                    select("after"),
                ]
                cases.append(freeze(f"{reason}-{resolution}", 1, None, operations))

        for size, forced in ((3, None), (3, 0), (3, 2)):
            cases.append(
                freeze(
                    f"round-robin-{size}-{forced}",
                    size,
                    forced,
                    [select(str(i), "https://a.example" if i % 2 else None) for i in range(12)],
                )
            )

        # Failures accepted under old redirect leases cannot mutate existing
        # final-origin state; owned selected-origin probes must still resolve.
        for final_reason in ("origin_block", "origin_transport", "proxy_auth"):
            for existing_final in (False, True):
                operations = [select("origin"), report("failure", "origin", reason="origin_block")]
                if existing_final:
                    operations += [
                        select("final", "https://b.example"),
                        report("failure", "final", "https://b.example", "origin_block"),
                    ]
                operations += [
                    {"action": "time", "at": 900},
                    select("probe"),
                    report("failure", "probe", "https://b.example", final_reason),
                    select("after"),
                ]
                cases.append(
                    freeze(f"redirect-{final_reason}-{existing_final}", 1, None, operations)
                )

        operations = [select("origin"), report("failure", "origin", reason="origin_block")]
        operations += [
            {"action": "time", "at": 900},
            select("origin-probe"),
            select("global", "https://b.example"),
            report("failure", "global", "https://b.example", "proxy_transport"),
            report("failure", "origin-probe", reason="origin_block"),
            {"action": "time", "at": 1020},
            select("both-probes"),
            report("failure", "both-probes", reason="origin_transport"),
        ]
        cases.append(freeze("stale-global-releases-origin-probe", 1, None, operations))

        for spacing in (0, 150, 151, 301):
            operations = []
            for i in range(4):
                origin = f"https://origin-{i}.example"
                operations += [
                    {"action": "time", "at": i * spacing},
                    select(str(i), origin),
                    report("failure", str(i), origin, "origin_transport"),
                ]
            cases.append(freeze(f"multi-origin-{spacing}", 1, None, operations))

        # Deterministic interleaving retains old leases across quarantine,
        # recovery, redirects and cancellation. It exercises concurrency policy
        # without depending on a particular Go implementation.
        rng = random.Random(956)
        for number in range(8):
            operations = []
            at = 0
            for i in range(160):
                origin = rng.choice(
                    [None, "https://a.example", "https://b.example", "https://c.example"]
                )
                if i % 5 == 0:
                    at += rng.choice([1, 120, 300, 900, 3600, 86400])
                    operations.append({"action": "time", "at": at})
                operations.append(select(str(i), origin))
                for _ in range(rng.randrange(3)):
                    operations.append(
                        report(
                            rng.choice(["success", "failure", "failure", "abandon"]),
                            str(rng.randrange(i + 1)),
                            origin,
                            rng.choice(
                                [
                                    "proxy_auth",
                                    "proxy_transport",
                                    "origin_block",
                                    "origin_transport",
                                ]
                            ),
                        )
                    )
            cases.append(freeze(f"interleaved-{number}", 3, None, operations))

    source = Path(__import__("src.shared.proxy", fromlist=["__file__"]).__file__)
    output = {
        "python_source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "cases": cases,
    }
    target = Path(__file__).with_name("python_proxy_policy.json.gz")
    target.write_bytes(gzip.compress(json.dumps(output, sort_keys=True).encode(), mtime=0))
    print(f"froze {len(cases)} cases / {sum(len(c['steps']) for c in cases)} operations")


if __name__ == "__main__":
    main()
