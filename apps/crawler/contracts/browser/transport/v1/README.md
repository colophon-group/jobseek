# Chromium browser transport registry v1

This directory is the authority for the bounded Chromium transport observer
contract frozen by issue #8402. It is a standalone pre-integration surface: no
production browser hook, runtime-cost Prometheus adapter, model, schema,
release, or deployment change is included here.

`registry.json` is canonical compact JSON. `registry.sha256` covers its exact
bytes. Regenerate or verify both from `apps/crawler/`:

```bash
uv run python contracts/browser/transport/v1/tools/generate_registry.py
uv run python contracts/browser/transport/v1/tools/generate_registry.py --check
uv run python contracts/browser/transport/v1/tools/generate_fixture.py
uv run python contracts/browser/transport/v1/tools/generate_fixture.py --check
```

The generated registry closes every label value. Request labels contain only
stage, the fixed `browser` execution class, the fixed `chromium` backend,
request class, one of the two valid route/provider pairs, and capability.
URLs, origins, hosts, IPs, company/board/posting identities, proxy endpoints,
exceptions, and target/session/request identifiers are internal-only and are
not metric labels. Capture metadata freezes the registry version and digest.

## Lifecycle and byte boundary

One admitted request hop becomes one terminal outcome. Redirect predecessors
have highest request-class precedence, then explicit warmup top-level requests,
then initial navigation/retry attempts, then subresources. Only nonnegative
`Network.dataReceived.encodedDataLength` values contribute transferred bytes.
Positive observed bytes make a failed, cancelled, or closed lifecycle one
`partial_response`; they never create a second causal outcome. Zero-byte target
death is `target_closed`, ordinary teardown is `cancelled`, and the remaining
bounded terminal classes are frozen in the registry.

The public-CDP observer must attach recursively to page, OOPIF/iframe,
dedicated/shared worker, and service-worker targets and enable `Network` before
accepting complete byte evidence. Missing coverage is
`byte_lifecycle_missing`, never an inferred healthy zero. Teardown freezes new
admission, terminalizes live records, drains for exactly 5.0 seconds, records a
bounded `drain_timeout` if needed, then detaches public listeners/sessions.

## Exact cardinality ledger

The base request dimension has
`2 stages × 4 request classes × 2 route/provider pairs × 3 capabilities = 48`
rows. Every zero child is exposed.

| Component | Rows | Series per row | Series |
|---|---:|---:|---:|
| Attempts (`_total`, `_created`) | 48 | 2 | 96 |
| Six outcomes (`_total`, `_created`) | 48 | 12 | 576 |
| Transferred bytes (`_total`, `_created`) | 48 | 2 | 96 |
| Histogram (10 buckets, sum, count, created) | 48 | 13 | 624 |
| Accepted tasks (`_total`, `_created`) | 12 | 2 | 24 |
| Pretransport (`_total`, `_created`) | 8 | 2 | 16 |
| Instrumentation failures (monotonic support series) | 16 | 1 | 16 |
| Registry info | 1 | 1 | 1 |
| **Exact total** |  |  | **1,449** |

Instrumentation is a monotonic support family deliberately exposed without a
`_created` child; the registry makes this explicit. Tests enumerate all 1,449
series and reject a missing/extra series, label, value, route/provider pair, or
histogram bucket. The registry also freezes Prometheus's public textual bucket
label encoding (`0.0`, `256.0`, …, `+Inf`) separately from the integer byte
bounds used by the capture fixture.

Pretransport events are separate: they contribute no attempt, terminal,
histogram observation, or byte. In particular, optional `use_proxy=True` with
configured provider `none` is a real `(direct,direct)` attempt.
