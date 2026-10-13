# Reduce idle narrowed refresh work

The minute trigger previously claimed and released the shared Postgres worker
even when every saved scope had been repaired recently and all demand was idle.
It opened empty repair and work-claim transactions. The candidate admits a
worker only when current, enabled, entitled demand is ready or a rotating repair
is due. A truly idle tick performs one SQL statement and leaves the worker row
unchanged, compared with nine statements at baseline `5a98c747a` in the same
isolated Postgres fixture. SQL statement counts are not billed CPU measurements.

The ready-work check uses the existing availability, work-lease, revision and
entitlement rules. A shared repair predicate retains the existing five-minute
cutoff. No timer, endpoint authorization, repair limit, checkpoint limit,
provider backoff, crash lease, or saved criteria changes. Freshness does not
depend on an owner opening a page. New demand after an empty tick can be claimed
without an artificial 55-second delay from that empty tick. An idle tick does
not reset accumulated worker failures; admitted successful ticks still do.

## Matched resource evidence

The completed read-only observation archived one full UTC day, October 12,
00:00–October 13, 00:00, with 24 aligned Production CPU/ISR route tables. The
private source records are `day-observation-20261012.json` and the
`hour-observation-*.json` records selected by their absolute window bounds,
not their capture filename date. Runtime logs remain sampled with unknown
coverage; `sourceIncludesAllTraffic` remains false.

| Measurement | October 12 UTC |
| --- | ---: |
| Project billed CPU, displayed | 1,029 seconds |
| Function / middleware CPU, displayed | 913 / 116 seconds |
| All visible Production route CPU | approximately 794.449 seconds |
| Function CPU minus visible Production routes | approximately 118.551 seconds |
| Refresh route CPU / invocations, displayed | 396 seconds / 1,440 |
| Company route CPU | approximately 202.28 seconds |
| Project billed and Production ISR write units | 9,608 |
| Company ISR write units | 9,392 |
| Company page / segment write units | 4,554 / 4,838 |
| Time-based / tag revalidations, displayed | 0 / 0 |

CPU labels are rounded; hourly CPU labels sum to one second more than the
daily label. The project bill and Production tables have different environment
scopes. The 118.551-second residual is unassigned, not proved waste or an idle
refresh charge. The candidate does not allocate it to the refresh route.

Company tables aggregate route templates and segment artifacts, rather than
individual company identities or regeneration causes. They cannot separate
cold locale/slug fills from stale rewrites. The route already uses a one-day
shell lifetime with company and CSV tags, and browser-direct postings and peers.
The recorded zero revalidation counters do not prove all writes were cold fills.
Extending the lifetime, removing segments or changing invalidation is therefore
not part of this candidate. Company gross ceilings of 202.28 CPU seconds and
9,392 units exclude replacement cost and are not promised savings.

## Estimate and overlap

This change reduces work within idle invocations; it retains every host trigger
and every due repair. The idle fraction and idle-versus-active CPU split are
unknown. The savings model is the number of truly idle invocations multiplied
by the before/after mean billed CPU cost of those invocations. Neither input
is measured in the retained logs. The entire 396-second refresh route pool is
only a gross ceiling before unavoidable invocation and admission costs, not a
forecast. It must not be added to the timer-only scenario: those changes overlap.

At unchanged mean invocation cost, an hourly timer instead of a minute timer
would avoid approximately 389.4 seconds of that day's refresh CPU, leaving
approximately 639.6 billed seconds. That remains above the 336-second/day
operating pacing guide. Larger scope counts can keep a repair due on every
tick, reducing this candidate's upside. There is still no demonstrated
functionality-preserving, budget-compliant bundle.

## Verification and post-deployment acceptance

The service-backed fixture verifies one-statement idle ticks for 1, 10 and 100
recent scopes, unchanged worker row versions, immediate foreground demand,
due repairs with idle demand, live/expired work leases, availability/backoff,
disabled or unentitled scopes and obsolete revisions. Existing tests cover
concurrent admission, alternating lanes, circuit recovery, bounded progress,
late-indexed postings and expired decisions without owner traffic. Focused unit
tests cover route authorization, narrowing, company reads and direct search;
TypeScript and changed-file lint checks also apply. CI executes the same
Postgres suite against its isolated fixture database.

After a separately authorized deployment, measure a complete UTC day using
the same project bill and exactly aligned Production route tables. Preserve
all table pages, rounded-label limits and environment-scope uncertainty. Check
actual current-criteria progress, lane fairness, provider retries and unattended
repair, not merely HTTP 200 or zero claimed counters. Compare refresh CPU with
backlog and repair activity; do not ascribe a total-day change to this PR.
Inspect the remaining Function residual across environment/deployment scopes
and obtain per-fill versus rewrite evidence before changing company caching.
The completed 48-hour automation remains paused; this PR does not restart it,
change production, or schedule another observation.
