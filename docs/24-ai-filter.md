# Jev watchlist filter

Status: the backend service boundary is implemented on
`codex/jev-ai-filter-service`; subscriber-facing UI is the next product slice
and remains behind the repository's human taste gate. The live issue tree under
[#8323](https://github.com/colophon-group/jobseek/issues/8323) is authoritative.

## Product contract

An actively subscribed owner can attach one bounded natural-language preference
to an existing watchlist. Existing structured filters remain hard constraints.
They select candidates through the canonical Typesense watchlist matcher and are
never sent to a model for interpretation. TypeSafe Jev receives only the
normalized soft query and normalized job evidence and returns one binary
`accepted`/`rejected` decision per job.

The production model route is frozen:

- direct `POST https://api.typesafe.ai/v1/systemone`;
- pinned `jev-1.13.0` (never an alias);
- five jobs and five source-bound Choice questions per request;
- one safe retry for transient/rate-limit/overload failures;
- no alternate model, provider router, or automatic fallback;
- explicit model, prompt, schema, normalizer, price, and cache-key versions.

Enabling or changing a query automatically walks the complete rolling 30-day
candidate horizon newest-first in durable segments of at most 50 decisions. A
zero-acceptance segment still continues. New matches use the same idempotent
catch-up service. Refreshing, reconnecting, listing decisions, or reading
progress never starts Jev.

There are no 25-per-day, 750-initial-candidate, or fixed request-quota gates.
Power use is bounded by actual model spend and fairness concurrency, not by an
artificial request allowance.

## Why Postgres owns the cache

The global exact cache is part of the paid-call consistency boundary, not merely
a latency optimization. One atomic transaction must be able to coordinate:

1. the global singleflight claim and lease;
2. the per-user and project spend reservation;
3. the provider usage ledger;
4. the ready decision and per-watchlist materialization; and
5. the progress event.

Postgres is therefore the authoritative store. Redis/Upstash would make the
fast-path lookup slightly cheaper, but it would introduce a dual-write crash
window between the claim, budget, and durable decision. Vercel Runtime Cache is
regional and ephemeral, and Typesense does not provide the required
transactions or compare-and-swap semantics. Neither is suitable for preventing
duplicate paid calls. A future Redis read-through layer may accelerate ready
hits, but Postgres remains authoritative and every Redis miss must still resolve
through the Postgres singleflight transaction.

Ordinary reads first use materialized per-watchlist decisions. A semantic miss
then checks the cross-user global cache. Only its HMAC key, content digest,
version provenance, binary decision, bounded usage, lease, and hard expiry are
stored globally; raw query and job description are not. The key covers the
normalized query, exact normalized Jev payload, pinned model, prompt, schema,
normalizer, and key version. Structured filters are excluded because Jev never
sees them. Cache hits never extend expiry beyond posting first-seen plus 30
days.

User moves and Undo are local overrides on the materialized decision. They do
not mutate the shared Jev result and cannot affect another user.

## Spend and capacity policy

Money is stored as integer nanodollars. TypeSafe's current Jev price is encoded
as versioned policy at `$0.042 / 1M input tokens`; output tokens are recorded and
currently priced at zero.

Before every call the service atomically reserves a conservative two-attempt
maximum, then reconciles to returned input-token usage and immediately releases
the remainder. Each user may accrue up to `$10.00` of reconciled Jev spend per
UTC calendar month. A call is allowed whenever actual spend plus outstanding
reservations plus the next bounded reservation fits. This lets a power user
approach the cap to within one maximum call reservation without crossing it.
The project budget is separately required through configuration.

Current direct-API synthetic observations:

| Fixture | Five-job input tokens | Five-job cost | Approx. cost/decision |
|---|---:|---:|---:|
| Compact | 1,711 | $0.000071862 | $0.000014373 |
| Five 12k-character descriptions | 8,234 | $0.000345828 | $0.000069166 |

At those observations, `$10` represents roughly 695,700 compact decisions or
144,500 maximum-description decisions before cache reuse. These are estimates,
not quotas. The estimate endpoint returns both ranges and current actual/reserved
spend.

Default fairness controls are one active segment per watchlist (database
constraint), at most two active segments per user, and a configurable project
segment ceiling (20 by default). Jev requests within a segment are serial, so
the implementation is stricter than the two-in-flight-per-segment ceiling.
Feature, model-route, project-budget, and credential failures pause before paid
work.

## Persistence and execution

Migration `0092_jev_ai_filter_foundation` creates:

- watchlist configuration and immutable query versions;
- resumable selection segments with lease, cursor, snapshot, and typed status;
- global pending/ready/failed exact-cache rows;
- per-watchlist decisions with optional local override;
- fixed-point user/project budget accounts and an idempotent usage ledger;
- persisted progress events; and
- move/Undo/mistake feedback.

The Next.js Workflow SDK runs automatic catch-up. Workflow code only loops over
a Node.js step; database, Typesense, R2, and Jev access all remain inside that
step. A provider failure records a typed pause. Ambiguous transport failure is
charged conservatively and is not automatically replayed, preventing a crash
or reconnect from blindly duplicating paid Jev work.

Required runtime configuration:

```text
AI_FILTER_ENABLED=true
AI_FILTER_JEV_1_13_0_ENABLED=true
AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS=<positive integer>
AI_FILTER_CACHE_HMAC_SECRET=<at least 32 bytes>
TYPESAFE_AI_TOKEN=<secret>

# Optional fairness controls
AI_FILTER_MAX_SEGMENTS_PER_USER=2
AI_FILTER_MAX_SEGMENTS_PROJECT=20
```

The bounded synthetic live smoke uses three direct calls and prints only decision
labels, latency, token counts, attempts, and fixed-point cost:

```bash
pnpm test:jev-live
```

## Owner-only API

All endpoints return `private, no-store`. Anonymous and cross-owner requests
share not-found behavior. Public watchlist DTOs, search indexes, REST/MCP
representations, and metadata do not include AI state.

| Operation | Behavior |
|---|---|
| `GET /api/web/watchlists/{id}/ai-filter/estimate` | Candidate count, observed cost range, entitlement, and monthly spend |
| `PUT /api/web/watchlists/{id}/ai-filter` | Idempotently enable/replace query and start catch-up |
| `DELETE /api/web/watchlists/{id}/ai-filter` | Disable and cooperatively cancel new work |
| `GET /api/web/watchlists/{id}/ai-filter` | Persisted query/status/count/budget snapshot; never starts Jev |
| `GET /api/web/watchlists/{id}/ai-filter/events?after=N` | Cursor-resumable NDJSON event snapshot |
| `POST /api/web/watchlists/{id}/ai-filter/reconcile` | Idempotently start access/scheduler catch-up |
| `GET /api/web/watchlists/{id}/ai-filter/decisions?bucket=...` | Persisted accepted/rejected decisions; never starts Jev |
| `PATCH /api/web/watchlists/{id}/ai-filter/decisions/{decisionId}` | Idempotent local move |
| `DELETE /api/web/watchlists/{id}/ai-filter/decisions/{decisionId}` | Undo the latest identified move |
| `POST /api/web/watchlists/{id}/ai-filter/decisions/{decisionId}` | Idempotent bounded mistake report |

## Remaining gates

Migration `0092` and the web/API surfaces are deployed independently of
activation. Enabling precise matching still requires the separately held
Typesense producer change, its reviewed production-shaped memory benchmark,
the complete candidate-order backfill/readiness receipt, production
secrets/budgets, and the operator canary/pilot gates. Hourly
catch-up/budget-resume and bounded retention cleanup are already wired through
the authenticated maintenance cron.

The repository's first notification release is currently providerless and is
explicitly independent of AI results (#8317/#8366), so there is no live
notification-assembly path to wire in this slice. A future decision to make
notifications consume Jev decisions needs its own delivery/readiness contract;
ordinary notification matching must not silently become an AI execution
trigger.

The rendered UI covers Explore, company, and owned-watchlist search surfaces,
including login/subscription return-state restoration, lazy catch-up progress,
and accepted-result controls. No eval dataset, provider bakeoff, expensive
fallback, or formal privacy/legal workstream is part of this MVP.
