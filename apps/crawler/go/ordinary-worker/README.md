# Native ordinary processing assembly

This package connects the standard Greenhouse token/skip inventory to the
existing native enrichment, owned rich batches and terminal board lifecycle.
It is an assembly library; protected executable startup, heartbeat/drain and
installed cutover proof are still required before production selection.

`RunGreenhouseClaim` now connects one installed opaque claim to the entire native
path. `VerifiedDirectHTTP` seals its transport/redirect/cookie configuration.
The runner consumes its own completed response, binds the initial token endpoint
to the claim and records the final resource URL for provider404 or publisher
headers. Publisher signals precede status/body parsing; redirect-only headers,
partial bodies, refused redirects and cancellations never grant final-resource
authority. Pre-existing reservations and shared-circuit deferrals perform no
fetch. Provider404 bypasses generic host outcomes; publisher outcomes retain
Python's successful host-reset behavior. A racing reservation after a committed
prefix preserves that prefix without another chunk, absence or failure budget.

A recovered receipt settles without network/CPU work or repeated circuit
accounting. A result is a settled completion only when `Settled` is true; errors
can retain an already committed receipt for durable recovery. Diagnostics are
bounded symbols, and arbitrary upstream/preparation/SQL messages do not enter
logged error text. Cancellation abandons uncommitted work to lease recovery.
The Redis reschedule script now preflights all queue/index types and numeric
inputs before effects, preserving the lease/snapshot on corrupt ready state.

`DiscoverGreenhouse` performs one logical GET through a caller-owned persistent
HTTP client. It checks only the final fully read response's resource headers,
before provider-404/status/body interpretation, and accepts all successful 2xx
statuses. Repeated headers, Python header decoding/whitespace and JSON byte
encoding detection match 52 captures from the actual Python monitor. Failed or
canceled reads and malformed inventories expose no partial jobs. Private final
resource observations preserve redirected publisher/provider evidence without
granting queue/write authority; the owned lifecycle's redirected-source adapter
remains to be proven. The inherited native 64 MiB response bound requires cohort
admission. Errors carry bounded symbols, never source bodies or diagnostics.

`NewDirectHTTP` now creates one persistent verified HTTP/1.1 client from an
explicit trusted CA bundle and frozen startup internal-host allowlist. It
preserves the effective 100-connection/20-keepalive/five-second pool, separate
30-second network/pool deadlines, 20 redirects and a process-owned cookie jar.
Every request to a public hostname, including redirects and reused connections,
revalidates all DNS answers; new public connections pin
only validated literals, with staggered address fallback. The compiled address
policy, including mapped IPv4 and private-range exceptions, is regenerated from
Python. Refused targets never enter origin/failure accounting. A private
non-rewindable empty GET body prevents net/http's hidden retry without changing
wire method/body/header semantics. Explicit request headers remain authoritative.

`ObserveHTTP` provides detached request/response/no-response and encoded-byte
snapshots for the future runtime circuit/metrics adapters. Compressed bytes are
counted before lazy gzip/zlib/raw-deflate decoding; 25 actual httpx cases cover
combined encodings, members/trailers and errors. Decoder completion never masks
an incomplete HTTP body. Encoded and decoded body bounds both remain 64 MiB and
require cohort admission. This is prepared transport, not installed startup,
publisher/provider write authority or global metric publication.

`NormalizeGreenhouseInventory` matches the default Python non-streaming rich
monitor's raw-URL dictionary, URL sanity/canonicalization and canonical alias
content rules. Raw duplicate URLs retain their first dictionary position and
last content. Canonical aliases then retain the last raw dictionary entry's
content. Jobs are sorted only after those decisions, before native chunks.
Truncation preserves every collected job and suppresses absence through the
terminal cycle. No provider identity or alternate/filter/proxy profile is
introduced.

`NativeRichPreparer` reuses `Processor.PrepareRichMonitor`, including its model,
immutable taxonomy/currency snapshot and serialized native location index.
`OpenOrdinaryLookupStore` gives the ordinary reader its own attribution, a
one-connection budget and a read-only PostgreSQL default. The separate owned
authority remains the writer. The fixture proves native lookups/locations work
through that reader and rejects a board update through it.
`PersistGreenhouseInventory` validates inventory accounting, prepares each whole
500-row chunk outside the PostgreSQL transaction, writes only prepared chunks
through the opaque owned cycle, and finalizes only the complete processing run.
Every failed/canceled preparation or persistence path invalidates later
success/absence authority; earlier committed chunks survive for failure or
lease recovery. Terminal deadlines still come from the database receipt.

The ordinary queue workflow requires PostgreSQL17 and private Redis on both
Linux architectures. It regenerates 40 URL, six inventory, 52 HTTP, 233 address,
nine DNS and 25 content-decoding cases from the
actual Python functions, rejecting stale captures, and exercises inventories
above the 50,000-job flag without slicing. Real native assembly proof loads
reference tables and the SQLite location index in an owned schema, processes
one verified native TLS HTTP 202 inventory of 1,001 postings across three batches,
checks request/encoded-byte and enrichment/description/R2/absence
effects and settles the exact canonical deadline. A separate preparation failure
after the first committed 500 rows proves that no partial second chunk or
success/absence receipt survives, while canonical failure scheduling remains
available.

Continue with protected exact-source startup for this persistent verified client
and typed response outcomes, including pinned CA asset/internal-host construction
and deployment/profile TLS/cookie admission. The connected runner proves
redirected provider/publisher lifecycle in owned TLS/database fixtures.
Python's declared 20/10 connection limits are not its effective
wrapped direct transport limits: the pinned httpx0.28.1/httpcore1.0.9 inner pool
has 100 connections, 20 keepalive connections and a five-second keepalive expiry.
It uses HTTP/1.1, 20 redirects and separate 30-second connect/read/write/pool
timeouts. Match or explicitly prove changes against that effective baseline.
The prepared ordinary queue circuit path now supplies shared host preflight,
canonical deferrals/backoff lower bounds and durable learned-host settlement.
The native TLS pipeline fixture recovers its observed API circuit; a failed
preparation fixture publishes its fallback failure host after settlement.
Integrate these prepared paths with exact-source startup,
heartbeat and bounded shutdown into the native executable. Prove the installed
process and supported all-writer cutover/cold reversal with B0's shared epoch
before selection. The [continuation plan](../../../../docs/27-go-lightpanda-continuation-plan.md)
retains all enabled profiles, remaining runtime consumers, whole-service cost,
the rollback window and production Python/Playwright/Chromium retirement.
