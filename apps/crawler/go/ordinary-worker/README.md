# Native ordinary processing assembly

This package connects the standard Greenhouse token/skip inventory to the
existing native enrichment, owned rich batches and terminal board lifecycle.
The native executable now connects this assembly to protected startup, bounded
claims, heartbeat, deadlines, metrics and drain. Installed image/fault admission
and supported all-writer cutover proof remain required before production selection.

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
granting queue/write authority; the connected owned runner proves its
redirected-source lifecycle adapters. The inherited native 64 MiB response bound requires cohort
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
snapshots for runtime circuit/metrics adapters. Compressed bytes are
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

`cmd/live` builds `go-ordinary-worker`. The binary's `--health` probes the installed process without another DB pool,
requires matching source/plan/epoch and live claim-loop progress, and follows no
redirects. Its `--identity` prints only
its immutable source revision, CA SHA256 and profile. Startup compares its own
clean Go VCS metadata or protected linker revision with
`ORDINARY_OWNERSHIP_SOURCE_REVISION`, the exact plan SHA256, installed projection
SHA1 and canonical routing epoch. It cannot activate a plan, rebuild a projection
or adopt the allocator. Docker release, CI and B0 builders inject the checked-out
revision with `CRAWLER_SOURCE_REVISION`; a missing or malformed build identity
fails the image build. Both crawler images carry the binary, but no service is
selected or started by this change.

Runtime requires `ORDINARY_GO_WORKER_MODE=enabled`, all four
`ORDINARY_OWNERSHIP_*` fields, `LOCAL_DATABASE_URL` and `REDIS_URL`. It loads native
models from protected `ORDINARY_GO_DATA_DIRECTORY` (default `/app/data`), one
ordinary authority connection and one read-only lookup connection. The CA asset
is the exact certifi2026.2.25 snapshot from `uv.lock`, hash
`fc9165a12403263e7ebfbdad7be7a3eac0fa5d325d3c70465f28d3690072ca28`;
its bundled MPL notice is in `trust/LICENSE`. Runtime invokes no Python and
substitutes no system CA. Internal-host exemptions derive once from protected
service/proxy endpoints and `INTERNAL_HOSTS_ALLOW`, never queue/board evidence.

Active monitor claims are bounded by the smaller discovery/monitor concurrency
(default five; a zero monitor setting retains bounded discovery concurrency).
There is no claimed-work prefetch beyond these slots. Defaults preserve the
600-second lease, 120-second renewal, two-second queue fallback delay and ten
domain probes. Every heartbeat revalidates the exact installed owner. A local
settlement guard prevents an acknowledged terminal lease from racing its own
renewal; PostgreSQL/token barriers remain the cross-process authority.

`ORDINARY_GO_TASK_TIMEOUT_SECONDS` defaults to600 and requires cohort duration
admission. The process stops claims on signal, keeps task contexts/heartbeats live
for `SHUTDOWN_GRACE_SECONDS` (default30), then cancels unfinished work without
inventing failure/success or removing its recoverable lease. Individual tasks and
process shutdown have bounded cancellation grace (default5); uncooperative work
forces process exit and skips blocking resource cleanup. A600-second default
claim-loop watchdog also makes a responsive metrics server unhealthy during a
claim outage. The listener defaults to `127.0.0.1:9104`, configurable through
protected `ORDINARY_GO_METRICS_ADDRESS`.

The runtime exports bounded legacy task/status/duration, heartbeat/drain and
monitor posting metrics, native extraction duration/output and conserved
origin/response/transport-error/encoded-byte counters. Extraction timing excludes
posting persistence; recovered receipts and no-fetch deferrals emit no extraction
execution. Partial processing results retain only confirmed committed batch
counts, never whole-inventory success authority. Errors and public metrics expose
no credentials, upstream URLs, hosts or arbitrary exception strings.

Real fixtures build and run the native command against migrated private
PostgreSQL/Redis, actual reference/model/location assets and the exact fixture
plan. They prove no-fetch reservation settlement, health/metrics, signal drain
and refusal of a wrong installed projection. Real TLS/queue token loss cancels a
blocked fetch without canonical failure or fabricated terminal receipt. Race
fixtures cover bounded claims, live renewal during drain, task deadlines,
acknowledgement/renewal serialization, watchdogs and uncooperative cancellation.
The real SIGKILL/guarded-reaper/restart fixture also preserves committed receipts,
canonical board/posting rows, future deadlines, failure budget and unrelated host
outcomes without HTTP or extraction replay. Checkpoint `16de7543b` passed this
fixture using the actual image-extracted AMD64 binary and read-only model assets
on Linux against private PostgreSQL/Redis (run 36860358274). That is installed
component evidence, not full production-container/public-network or cold cutover
authority. Fresh AMD64/ARM64 installed-image admission is now prepared.

## Native ownership preparation

The same source-bound executable provides `--stage-ownership` and
`--inspect-ownership`. They use distinct protected `ORDINARY_GO_WORKER_MODE`
values `stage-ownership` and `inspect-ownership`, the compiled matching
`ORDINARY_OWNERSHIP_SOURCE_REVISION`, an explicit current
`ORDINARY_OWNERSHIP_ROUTING_EPOCH`, and protected database/Redis URLs. They never
allocate/adopt an epoch, claim work, activate ownership or publish its projection.

Staging requires `ORDINARY_GO_COHORT_FILE`: an absolute regular non-symlink file,
not writable by group/others, containing a bounded JSON array of distinct
canonical board UUIDs. `ORDINARY_OWNERSHIP_PLAN_SHA256` and projection SHA1 must
be absent. The tool stages fresh canonical eligible configurations and performs
another exact readback. Inspection requires only the expected plan SHA256;
cohort file and projection SHA1 must be absent. It rejects active/retired plans,
configuration/eligibility drift, wrong source/digest and stale epochs. Both modes
emit bounded document identities, not board configuration or credentials.

Activation remains part of the coordinated all-writer ordinary/B0 cold protocol.
The B0-only allocator now takes the ordinary lease barrier before its epoch
barrier and declines an active ordinary plan before burning another epoch. It
retains compatibility when the ordinary schema is absent and may allocate after
the old ordinary plan is retired. Installed process fixtures cover staging,
idempotent readback, drift/disabled-board rejection, epoch/source binding and
unchanged canonical/queue/owner state; production selection remains disabled.

Continue with installed image/process fault admission, supported all-writer
startup/projection/cutover and B0's shared epoch cold reversal before selection.
The [continuation plan](../../../../docs/27-go-lightpanda-continuation-plan.md)
retains every enabled effective profile, remaining runtime consumers, whole-service
output/freshness/queue/cost, the rollback window and production Python/Playwright/
Chromium retirement. Python's effective direct transport remains100/20/five-second,
HTTP1.1/20redirects/separate30-second operations; admit deployment/profile TLS,
cookie, body-size and task-duration compatibility against that baseline.

## Protected cold coordinator primitives

The compiled-source-bound executable now accepts seven distinct one-shot commands:

| Argument / matching `ORDINARY_GO_WORKER_MODE` | Operation |
| --- | --- |
| `--cold-b0-target` / `cold-b0-target` | Capture exact fresh PG/Redis fixed B0 board configurations. |
| `--cold-begin` / `cold-begin` | Retain exact canonical intent before any sequence allocation. |
| `--cold-reserve` / `cold-reserve` | Allocate or inspect the exact intent's fresh reservation. |
| `--cold-inspect` / `cold-inspect` | Read retained phase/reservation by exact intent, granting no authority. |
| `--cold-prepare` / `cold-prepare` | Retain pending Redis witness before committed publishing phase. |
| `--cold-publish` / `cold-publish` | Audit actual B0 queues and publish/persist/read back joint routing. |
| `--cold-activate` / `cold-activate` | Atomically install the exact ordinary DB owner and active journal. |

All require protected database/Redis URLs, compiled matching
`ORDINARY_OWNERSHIP_SOURCE_REVISION` and canonical positive
`ORDINARY_COLD_ROUTING_EPOCH`. Worker ownership epoch/plan/projection and cohort
file fields must be absent. Capture uses the current caller-attested epoch;
begin/reserve/inspect bind the intent's previous epoch, including pending burned-epoch
recovery. Prepare/publish/activate require the exact reserved epoch and
`ORDINARY_COLD_PLAN_SHA256`; neither a high-water nor a latest-plan selector is
accepted. PostgreSQL pool capacity is one; statements are bounded to ten seconds,
each critical transaction to fifteen and the one-shot operation to thirty.

Capture requires `ORDINARY_COLD_B0_NAMESPACE`, `ORDINARY_COLD_B0_SHARD_ID`,
`ORDINARY_COLD_B0_COHORT` and `ORDINARY_COLD_B0_LUA_FILE`. Its bounded JSON output
contains a target SHA256 plus canonical `target` object with board IDs/slugs and
configuration hashes. The host must durably save those exact object bytes; no
credentials or raw configuration are returned. Capture creates no ownership.

Other operations require `ORDINARY_COLD_INTENT_FILE` and
`ORDINARY_COLD_INTENT_SHA256`. Except reservation/inspection, they also require
`ORDINARY_COLD_B0_TARGET_FILE`, `ORDINARY_COLD_B0_TARGET_SHA256` and
`ORDINARY_COLD_B0_LUA_FILE`. Reservation and inspection reject those unused fields. Files must
be absolute regular non-symlink files, not group/other writable; intent/target/Lua
limits are 4 KiB/16 KiB/128 KiB. Canonical JSON rejects duplicate/unknown fields,
trailing data and reformatted bytes even with a matching hash. The actual B0 Lua
must match its reviewed SHA256. Input hashes and source must bind the exact intent;
the immutable journal binds the reserved epoch/plan before publication effects.

Inspection returns `retained_phase` and any exact recorded reservation, including
after publication interruption or witness loss. It does not attest live allocator,
configuration, Redis routing or permission to start an owner.

Outputs identify the completed primitive and exact documents, not host readiness
or permission to start services. Errors are constant and omit inputs/credentials.
A real executable fixture kills publication with SIGKILL after MSET, acknowledged
SAVE and readback, before PostgreSQL commit. Its retained publishing intent and
staged plan recover through exact publication/activation retries without changing
canonical rows/deadlines/receipts or other Redis keys. Missing witnesses remain
contained. The installed-image workflow runs this fixture on both architectures.

These commands do not stop services, deploy releases, transfer full PG-derived
B0 tasks, verify host-cold/release/rollback evidence or implement reversal. The
supported ADR006 wrapper must own those operations under its mutation lock before
these primitives can select production authority. Ordinary production remains
Python until that complete protocol, readiness and full reversal are proven.

## Native joint runtime admission

Migration 0040 retains the exact canonical B0 configuration target before
publishing phase commits. Its content/hash are immutable; the schema binds each
reserved ordinary plan to at most one journal. Publishing/published/active phases
require the retained target, and downgrade refuses any target or journal history.
No witness is recreated automatically after a publishing phase has committed.

For an active joint plan the native worker requires
`ORDINARY_GO_B0_AUDIT_LUA_FILE`: an absolute regular non-symlink file, not writable
by group/others and at most 128 KiB. Its bytes must match the reviewed actual B0
Lua SHA256. Missing/untrusted Lua rejects startup before claiming. Foundation
fixtures without any unfinished joint transition retain their standalone binding;
they are not production selection proof.

Startup and each native claim/write/heartbeat/settlement freshly bind the exact
active journal to the approved ordinary plan/source/epoch and retained B0 target.
The target's PG eligibility/configuration and Redis stable fields are re-attested.
One read-only EVAL invokes the unmodified source-pinned B0 conservation audit and
checks exact fixed selectors plus permanent ordinary projection/joint witness/
B0 route/producer owner. Conserved live inflight/dead/terminal B0 states are valid;
runtime admission does not impose the cutover's zero-inflight/dead requirement.
Lost/expired/changed evidence declines authority and never adopts a later owner.
Final Redis checks also guard the operation's held claim/receipt. Supported
ownership/configuration mutators must honor the existing lease/epoch barriers.

Private tests reject 15 journal/target/config/route/selector/record/type/TTL faults
before pop without changing state. A real B0 inflight claim remains compatible
with ordinary execution, while witness loss fences heartbeat, canonical callback
and settlement of a real completed receipt with a future due. The actual worker
process becomes ready under joint authority, then exits without popping future
work or changing canonical state after witness loss. Installed-image tests run
this behavior on both architectures before admission of the later source.

Legacy ordinary startup and every claim now use the same retained journal/target,
source-pinned actual B0 audit, fixed selectors and permanent shared witnesses.
The installed `ORDINARY_GO_B0_AUDIT_LUA_FILE` requirement also applies to a
journalled legacy owner. Unselected legacy claims hold the existing DB barriers
and refuse unfinished joint intent. This does not add legacy write/settlement
fences; all legacy inflight work must finish while the supported host drains and
stops every writer before a transition.

Metadata configuration hashes compare exact numeric values across PostgreSQL
JSONB and Redis spelling, without binary float rounding. A shared Go/Python
corpus covers precise integers, decimal/scientific spelling, negative zero,
string escaping, duplicate/deep malformed objects and bounded exponents.
Changed target hashes require a freshly captured and approved target before
intent; no installed witness is automatically repaired or replaced.

The real native coordinator publishes the journal/target used by an actual
Python startup/claim probe. Nine reversible owned-fixture evidence faults block
both admission paths without changing any Redis value/expiry class or canonical
state. A real B0 inflight lease remains compatible with legacy admission. These
checks run inside the fourth installed executable fixture on both architectures.

The supported all-writer wrapper, complete PG-derived B0 transfer, full cold reversal,
remaining interruption seams and real production container/public-fetch proof
also remain before ordinary native selection. Full migration scope is unchanged.
