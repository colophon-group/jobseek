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

Chromium is launched normally by Playwright with a cryptographically selected
debugging-port candidate bound only to `127.0.0.1`. No socket is opened and
released before launch. A separate raw WebSocket controller discovers the
public browser endpoint at that exact loopback origin. It does not use
Playwright private connections, browser-level `CDPSession`,
`connect_over_cdp`, host publishing, credentials, query tokens, or relaxed
remote-origin flags.

Endpoint syntax and loopback location are not treated as ownership. Once the
raw controller's root auto-attach acknowledgement is established, Playwright's
native connection creates a high-entropy `about:blank` proof page without a
network request. The raw endpoint must expose exactly one matching proof target
ID, attach exactly one session to that ID, and record that session's ordered
child auto-attach, Network enable, and debugger-resume acknowledgements before
ownership is verified. The verified page/context is then transferred for
application use. A port collision, competing controller, proof mismatch,
missing attachment or acknowledgement, bind failure, or proof setup failure
closes the proof context and admits no application task or traffic; the secret
proof value is never a metric label or log field.

The controller sends `Target.setAutoAttach` with `flatten=true`,
`waitForDebuggerOnStart=true`, and an explicit filter for page, OOPIF/iframe,
dedicated worker, shared worker, and service-worker targets. Every accepted
target is armed recursively in this order: child `Target.setAutoAttach`
acknowledgement, `Network.enable` acknowledgement, then
`Runtime.runIfWaitingForDebugger` acknowledgement. A session is not a complete
byte source until that sequence succeeds; events queued before the
`Network.enable` acknowledgement remain outside the accounting boundary.
The tracker begins with admission closed. Ready and task admission are opened
only after endpoint ownership, every required child acknowledgement, and
incoming setup envelopes are stable. Reader loss, malformed relevant protocol,
or a required child setup failure enters one fatal transition: readiness is
revoked, future admission is permanently frozen, live generation records are
terminalized as transport failures (or partial responses when bytes exist),
paused targets are released or closed, and the raw transport is closed.
An explicit child setup error enters that fatal transition before any cleanup
await; only raw transport closure is deferred while bounded release runs.
Duplicate-session resume and detach are part of required initialization:
failure of either acknowledgement enters the same fatal transition before any
further application admission, while cleanup continues only long enough to
prove the paused target was resumed, detached, or closed.
Every target initialization has one absolute 5.0-second acknowledgement
deadline covering command send and response, duplicate cleanup, and all
release/detach/close fallbacks. Cleanup never receives a fresh timeout. Missing
acknowledgements exhaust that shared budget, enter the fatal transition, and
close the raw transport as the final paused-target release path.

Request identity is `(browser_generation, session_id, request_id,
redirect_hop)`. Attribution and request class freeze at
`Network.requestWillBeSent`; sparse follow-ups never rerun the classifier.
Exact request-event replay across a reattached session aliases the existing
attempt. Chromium bootstrap tails reparented from a page to its newly attached
iframe/worker session may alias the one unambiguous current request with the
same request id. A different declaration with a reused id is a new attempt,
while ambiguity or malformed relevant evidence fails closed. Valid unrelated
`Network.*` notifications are ignored. Cache, prefetch, and service-worker
wrappers proven to perform no transport are suppressed so their underlying
network attempt is counted once.

Teardown freezes new admission and uses one absolute 5.0-second deadline across
both target barriers, recursively spawned setup tasks, incoming envelopes, and
live-record terminalization. Timeout records the bounded `drain_timeout`, then
the raw WebSocket and reader are closed. Cleanup is cancellation-safe and the
caller's cancellation is re-raised after cleanup.

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
