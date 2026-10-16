# Go and Lightpanda runtime checkpoint — 2026-10-16

The full migration goal is **active and incomplete**. **0.13.1077** is
independently serving source `25901ab3e61b5c9866aaff03eba5771b8e49d20a` with
Lightpanda **1.0.1**, the [latest stable release](https://github.com/lightpanda-io/browser/releases/tag/1.0.1)
verified through the release API on October 16.

## Delivered runtime and ownership

The full **7,838-monitor / 7,832-detail** cohort is active at epoch **360**.
Every member of the previous cohort is preserved; this runtime fix adds no
ownership. At **13:57 UTC**, independent SQL, Redis, receipts, exact images,
all ten actors and health readback agree. The **14:02 UTC** ordinary-worker
readback has zero restarts, OOMs or classified fatal configuration failures.
These are bounded observations.

[PR #10651](https://github.com/colophon-group/jobseek/pull/10651) fixes single
and batch publication of still-scheduled enrichment when a description upload
changes only the canonical content hash. It retains first-time priority and the
existing active, due, publisher, claim, lease and ownership guards. Deterministic
regressions fail against the outgoing code and pass against the fix; full
Required CI and installed runtime parity pass on qualified head
`950b8105619bd0d32f603948a6a09ddd95ba5e39`. The installed merge has identical
crawler/runtime/data/deployment code; three separately reviewed Narrowed helper
files differ outside that scope. This is not a whole-repository tree equality claim.

Original ordinary retirement and outgoing B0 cold reversal established epoch
**359**. Incoming B0 activation established **360**. The original ordinary wrapper
staged and activated the complete floor, waiting for natural SQL lease expiry
before fleet validation/projection. Activation completed at **13:56:38 UTC**.
No production queue, receipt or lease was manually edited.

The first crawler deployment failed its environment-drift check and the original
rollback restored outgoing source and images. The original retry succeeded using
the committed loader's existing `COMPOSE_FILE` normalization. Both complete original
deployment workflows then passed with immutable images; renderer deployment also
passed. No live rsync, mutable image tags, partial writer restarts or ad hoc state edits
were used.

## Current queue accounting

The postactivation census and compiled SDK replay validate every owned route and
all **1,729,024 owned scheduled posting URLs**, with **zero owned cache mismatches**.
All **1,729,024** are queued in that capture. The preactivation non-atomic capture
had 1,728,108 scheduled and 1,727,108 queued postings.

Exact queued-ID comparison retains **1,726,913** IDs, with **195 outgoing-only** and
**2,111 incoming-only** IDs. Read-only current canonical accounting classifies all
195 outgoing-only IDs as **191 inactive / four unscheduled**; none remains eligible
and unqueued. The previously held API-browser enrichment posting is active,
scheduled and queued. No epoch-360 native completion is observed for that case.
Supported projection may explain its queue presence; this does not establish the
historical cause or attribute its recovery specifically to the race fix.

These are exact-ID and current-eligibility observations across non-atomic captures,
not a historical or global zero-loss guarantee. See the
[aggregate evidence](evidence/go-native1077-serving-checkpoint-2026-10-16.json).
Raw configurations, IDs, bodies, receipts and host evidence remain protected.
The [previous checkpoint](34-go-lightpanda-five-monitor-checkpoint-2026-10-16.md)
retains the five-monitor inventory and canonical-write qualification.

## Continue in substantial batches

[PR #10653](https://github.com/colophon-group/jobseek/pull/10653) adds an explicit
4,000,000-byte complete-document mode shared by DOM, JSON-LD, embedded-data and
inline workers. It preserves existing B0/stdio ceilings, integrity, policy,
cancellation and cleanup checks. Both Linux architecture integration suites pass;
remaining CI/admission checks are pending at this checkpoint. It is undeployed
and grants no new ownership.

Two fresh full-config DOM comparisons agree on ten positive URLs and one complete
empty inventory. A fresh browser-API detail comparison agrees on all populated
fields, including a 10,326-character description. Private canonical worker replay
passes for these captures. The proposed next cohort is **7,840 monitors / 7,833
details**, preserving the full current floor. Revalidate current configs, every
scheduled URL and supported source-bound cutover before admitting it.

The remaining snapshot is **59 monitors / 65 details**, excluding three B0 records.
Continue multiple profile families and configuration controls per iteration. A
seven-document same-original-HTML diagnostic agrees with original extraction;
two live DOM losses arise before parsing, and their original positive content is
present in a shared public POST JSON response. Qualify a native HTTP/API route
where rendering is unsuitable, with exact request, policy, content and lifecycle
proof. Do not admit truncated, failed or content-losing inventories.

Draft [PR #10643](https://github.com/colophon-group/jobseek/pull/10643) remains
separate and undeployed. Proxy ownership is held until upstream destination
policy is qualified. Local URL/DNS checks do not pin a remote proxy resolver.

Finish remaining routes, native maintenance/deployment consumers, production
freshness and matched whole-lane attributable cost. The earlier snapshot still
had about **1.61 million postings more than 24 hours overdue**; current route and
queue conservation do not establish sustained freshness. Isolated ARM density
is not matched production fleet cost. Prove final supported reversal and the
recovery window before retiring production Python, Playwright, Chromium and
runtime-only assets. Preserve useful isolated offline Python and every enabled board.
