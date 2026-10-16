# Go and Lightpanda five-monitor checkpoint — 2026-10-16

The full migration goal is **active and incomplete**. This batch reuses the
installed **0.13.1075** source `cbd1f37f3803a29dcd6693aa0a120992513edda6`;
there is no image or runtime-code change. Lightpanda **1.0.1** remains the
[latest official stable release](https://github.com/lightpanda-io/browser/releases/tag/1.0.1).

## Delivered ownership

**Independently serving at epoch 358.** The active cohort
is **7,838 monitors / 7,832 details**, preserving every previous 7,833 monitor and
7,832 detail member. No proxy configuration enters ownership in this batch.

The original ordinary retirement and B0 reversal succeeded. Independent readback
verified cold retirement epoch **357**; original selector clearing/restaging and
B0 activation then established epoch **358**. The original ordinary wrapper staged
and activated the full cohort after complete writer quiescence and natural SQL
lease expiry. Activation completed at **11:23:17 UTC**. Independent SQL, Redis,
receipt, image, writer and health readback passed at **11:24:17 UTC**. All ten
actors returned to service. No leases, epochs, receipts or queue state were edited
outside those original supported operations.

At 11:39 UTC all five additions remain enabled with zero current failures; three
have a successful monitor run after activation. At 11:51 UTC the ordinary worker
is running with zero restarts, OOMs or classified fatal configuration failures.
These are bounded observations, not sustained fleet freshness proof.

## Qualification

Two Brassring monitors have equal complete original/native inventories totaling
**664 jobs and positive descriptions**. Canonical preparation and private real
Postgres/Redis insert/description-upsert replay agree on the compared fields.
Lookup/location fixtures are controlled and identical; production geography and
original complete rich-board-finalization SQL are not established by that replay.

Three DOM monitors have equal complete empty inventories and currently have zero
active or scheduled postings. Private real Postgres/Redis replay of the original
empty/metadata SQL and the native opaque-claim lifecycle agrees on compared board
state, scheduling, queue conservation and lease release. Explicit fixture HTTP200
provenance is used for captured-inventory lifecycle replay.

The postactivation epoch-358 census covers **7,900 enabled records**. The installed-source
SDK validates every active monitor/detail configuration and every **1,723,120
owned scheduled posting URL**, with **zero owned cache mismatches**. All five added monitor
profiles still match their qualified original/native configuration fingerprints.
The postactivation non-atomic capture has **1,723,119 queued owned IDs**, a captured
gap of **one**. Of the previous capture's queued IDs, **1,717,602** remain queued;
the **2,107** outgoing-only IDs are currently **2,008 inactive / 99 unscheduled**.
No outgoing-only ID remains canonically eligible or missing. There are **5,517**
incoming-only queued IDs. This is current exact-ID accounting across non-atomic
captures, not a historical or global zero-loss guarantee.

The remaining gap is a newly discovered posting from a previously owned browser
API monitor, outside the five additions. It has a description and requires
location/employment enrichment, but the bounded readback finds no queue, cache,
inflight, token, deadletter or native detail-write fence. Both original and native
rich writers require detail publication for that enrichment configuration. The
cause remains unresolved. Resolve it through normal code/deployment workflows;
do not treat it as intentional no-scrape behavior or apply an ad hoc requeue.

See the [aggregate evidence](evidence/go-native1075-five-monitors-2026-10-16.json).
Raw configurations, IDs, bodies, receipts and host evidence remain protected.

## Continue delivery

Resolve the new-posting publication gap first, preserving current ownership and
the protected case evidence. Recheck the five additions' next successful runs.
The captured remaining frontier is **59 monitors / 65 details**, excluding the
three B0 records; refresh it before the next admission. Preserve every active
member and use only committed supported cutover/recovery operations.

Draft [PR #10643](https://github.com/colophon-group/jobseek/pull/10643) adds the
shared configured browser-proxy document transport across DOM, JSON-LD, embedded
and Nextdata profiles. Required CI, installed-image parity, both Linux architecture
suites, actual renderer ARM build/deployment/cold rollback smoke and all 16 ARM
whole-lane arms pass. Independent controller replay verifies all eight complete
output pairs and exact cleanup with zero unexpected fixture requests. These
isolated measurements do not establish matched production fleet cost/freshness.
The actual deploy gate remains red because the PR is deliberately draft.

Proxy ownership remains held until upstream destination enforcement is qualified;
local URL/DNS guards do not pin the remote proxy resolver. Repeated live proxy
observations include HTTP403 and changing inventories. Two matched detail outputs
have no positive descriptions. Keep those limits explicit. Continue substantial
batches of API-browser, inline, Njoyn and remaining configuration controls, then
native maintenance/deployment consumers, production freshness, matched resource
cost, supported reversal and the recovery window. Retire production Python,
Playwright and Chromium only after replacement authority is established; preserve
useful isolated offline Python tooling and every enabled board.
