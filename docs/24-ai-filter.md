# Jev narrow-results filter

Status: implemented production reference. Activation is fail-closed behind the
runtime switches and budget described below.

## Product contract

Narrow results is a paid, second-stage filter over an ordinary Jobseek search.
Structured filters remain hard constraints and are evaluated by the canonical
Typesense watchlist matcher. TypeSafe Jev receives only the normalized request
and normalized evidence for each candidate, then returns one binary
`accepted`/`rejected` decision per job.

The control is available on Explore, company pages, and owned watchlists when:

- the viewer has an active Unlimited subscription;
- at least one search constraint is present (a company scope counts);
- Typesense reports between 1 and 10,000 matching active jobs; and
- the candidate count is available.

On Explore and company pages, applying a request creates a watchlist and
navigates to it. The company page preserves its company scope. On an existing
owned watchlist, applying or editing a request creates a new immutable query
revision only when the normalized request, hard-filter fingerprint, company
membership, or language scope changes.

The product UI deliberately describes the behavior rather than the provider:
the request is evaluated against every posting in the feed. Provider and model
names remain implementation details in technical and operational surfaces.

## Access, sharing, and anonymous drafts

The normal detail URL is `/{lang}/watchlists/{watchlistId}` for owned, shared,
and browser-backed watchlists. This keeps one page data contract and one detail
component across all three modes.

- Owners may configure narrow results only while entitled.
- Anyone with an unlisted shared URL may read the owner's persisted accepted
  feed while the watchlist is shared, the filter is enabled, and the owner
  remains entitled. The saved matching request is visible to link viewers.
- Anonymous shared viewers retain the normal 20-job anti-scraping cap. Signed-in
  non-owners may page the full broad and narrowed feeds. The cap is enforced by
  the accepted-results API as well as the page loader.
- A shared viewer never starts or extends Jev evaluation. Shared reads page
  durable accepted postings only; decision IDs, model outcomes, overrides, and
  timestamps are omitted from the shared response.
- Cloning copies standard filters, not the owner's natural-language request or
  decisions. Free and anonymous viewers receive an explicit warning before
  cloning a watchlist that has narrow results.

Anonymous users may create or clone ordinary watchlists. Up to ten validated
drafts are stored in `sessionStorage` for two hours and rendered through the
same overview/detail components at normal UUID paths. The UI warns that the
draft may be lost. After authentication, the overview imports drafts into
Postgres until the account's ten-watchlist limit is reached; overflow drafts
are discarded with an explicit notice. Share and alert controls remain visible
but disabled with an account-required explanation. Narrow-results setup is not
available to anonymous or Free accounts.

Shared feeds always use the owner's stored language scope. Viewer language
preferences and the language-change control do not rewrite a shared watchlist.

## Provider route and input contract

The production model route is frozen:

- direct `POST https://api.typesafe.ai/v1/systemone`;
- pinned `jev-1.13.0` (never an alias);
- five jobs and five source-bound Choice questions per request;
- one safe retry for transient, rate-limit, or overload failures;
- no alternate model, provider router, or automatic fallback; and
- explicit model, prompt, schema, normalizer, price, and cache-key versions.

Candidate selection is deterministic, newest-first, and requires the deployed
Typesense stable-order receipt. Each segment scans at most 50 usable jobs.
Description HTML is bounded, normalized, and supplemented with indexed job
facts. Missing description objects fall back to bounded title and indexed
metadata instead of making otherwise valid jobs disappear.

## Demand-driven historical evaluation

Each immutable query revision describes the full active historical horizon,
starting at `2000-01-01T00:00:00.000Z` and ending at a whole-second boundary.
This is a selection boundary, not an instruction to evaluate the entire feed
at setup time.

The initial configuration writes state but does not walk 10,000 jobs. Opening
or scrolling the narrowed surface requests a 500-candidate runway beyond the
current candidate cursor. A Workflow execution may run at most ten durable
50-candidate steps for that demand. More work begins only when the result
surface needs it. The horizon end advances in one-minute freshness buckets so
new jobs can be reconciled without creating a new revision or empty segment on
every poll.

Before requesting more work, the client reads the persisted accepted page. A
complete cached page causes no reconcile request. One reconcile mutation starts
or joins durable work; one consolidated decisions response returns both the
accepted page and current owner state. Foreground polling is bounded and uses a
1.5-second interval. Background state polling uses a five-second interval and
stops after 24 reads; hidden tabs suspend reads. The former duplicate
decisions/state polling loops and
automatic full-history cascade are intentionally absent.

Reads never start paid work. The only execution trigger is the owner-authorized
reconcile path. Refreshes, reconnects, shared views, event reads, and ordinary
decision reads are side-effect free.

## Why Postgres owns the cache

The global exact cache is part of the paid-call consistency boundary, not only
a latency optimization. One atomic transaction coordinates:

1. the global singleflight claim and lease;
2. per-user and project spend reservations;
3. the provider usage ledger;
4. the ready global result and per-watchlist materialization; and
5. progress events.

Postgres is therefore authoritative. Redis/Upstash would introduce a dual-write
crash window between claim, budget, and decision state. Vercel Runtime Cache is
regional and ephemeral; Typesense lacks the required transaction and
compare-and-swap boundary. Neither can safely prevent duplicate paid calls.

Ordinary reads use per-watchlist decisions. A semantic miss checks the
cross-user global cache. The cache stores an HMAC key, content digest, version
provenance, binary result, bounded usage, lease, and expiry; it does not store
the raw request or job description. The key covers the normalized request,
exact normalized Jev payload, model, prompt, schema, normalizer, and key
version. Structured filters are excluded because Jev never sees them.

Ready decisions expire no later than 30 days after `decided_at`. This permits
an old but still-active job to be evaluated lazily while keeping reuse bounded.
The user-facing feed reads accepted decisions only; rejected decisions remain
internal to the classifier and never appear in a separate result view.

## Economics and capacity

Money is stored as integer nanodollars. The versioned policy uses TypeSafe's
`$0.042 / 1M input tokens`; output tokens are recorded and currently cost zero.
Before each call, the service atomically reserves the conservative two-attempt
maximum, reconciles to returned usage, and immediately releases the remainder.

Monthly per-user and project spend ceilings are optional. When one is set, a
call is allowed only while actual spend plus outstanding reservations plus the
next bounded reservation fits. With both unset, Jev spend is uncapped; both
scopes still have durable accounting and every paid call has a bounded
reservation.

Maintained direct-API observations:

| Fixture | Five-job input tokens | Five-job cost | Approx. cost/decision |
|---|---:|---:|---:|
| Compact | 1,711 | $0.000071862 | $0.000014373 |
| Five 12k-character descriptions | 8,234 | $0.000345828 | $0.000069166 |

At these observations, `$10` corresponds to about 695,700 compact decisions or 144,500
maximum-description decisions before global cache reuse. A 500-candidate runway
costs at most about `$0.035` at the conservative observed bound. These are
estimates, not quotas.

Fairness defaults are one active segment per watchlist (database constraint),
two active segments per user, and 20 active project segments. Jev calls inside
a segment are serial. Feature, route, credential, entitlement, project-budget,
and provider failures pause before unsafe work or further paid calls.

## Persistence and execution

Migration `0092_jev_ai_filter_foundation` creates configuration, immutable query
versions, leased segments, the global exact cache, materialized decisions,
fixed-point budgets and usage, events, and the historical feedback table.
Owner correction and feedback actions are not part of the product. Migration
`0093` changes decision retention to a 30-day interval from evaluation time so
historical active jobs can be evaluated lazily. `0094` persists the exact
candidate language scope on each query version so it can be included in the
hard-filter fingerprint.

The Next.js Workflow SDK owns durable catch-up. Workflow code loops only over a
bounded Node.js step; Postgres, Typesense, R2, and Jev access remain inside that
step. Ambiguous provider transport failure is charged conservatively and is
not blindly replayed.

Required runtime configuration:

```text
AI_FILTER_ENABLED=true
AI_FILTER_JEV_1_13_0_ENABLED=true
AI_FILTER_CACHE_HMAC_SECRET=<at least 32 bytes>
TYPESAFE_AI_TOKEN=<secret>

# Optional monthly ceilings and fairness controls
AI_FILTER_USER_MONTHLY_BUDGET_NANODOLLARS=<positive integer>
AI_FILTER_PROJECT_MONTHLY_BUDGET_NANODOLLARS=<positive integer>
AI_FILTER_MAX_SEGMENTS_PER_USER=2
AI_FILTER_MAX_SEGMENTS_PROJECT=20
```

Runtime variables are forwarded in both root and web build tasks in
`turbo.json`. Configure secrets and switches for the intended Vercel environment
before promotion. Keep both switches false or absent until migrations, the
stable Typesense receipt and Workflow deployment have been verified. If a
monthly ceiling is configured, verify its amount before promotion. Paid
execution requires both switches, a valid credential, a Pro entitlement,
and a database-authorized reservation for the current watchlist and segment.

The bounded live smoke makes three direct provider calls and prints only labels,
latency, token counts, attempts, and fixed-point cost:

```bash
cd apps/web
pnpm test:jev-live
```

## API and authorization

All responses are `private, no-store`. Invalid IDs, absent resources, and
unauthorized access share a not-found boundary.

| Operation | Authorization and behavior |
|---|---|
| `GET /api/web/watchlists/{id}/ai-filter/estimate` | Owner only; entitled owners receive candidate count, cost range, and monthly spend; ineligible owners receive entitlement and spend without an extra search count |
| `PUT /api/web/watchlists/{id}/ai-filter` | Owner only; validate 1-10,000 candidate scope and enable/replace query |
| `DELETE /api/web/watchlists/{id}/ai-filter` | Owner only; disable and cooperatively cancel new work |
| `GET /api/web/watchlists/{id}/ai-filter` | Owner-only state read; never starts Jev |
| `GET /api/web/watchlists/{id}/ai-filter/events?after=N` | Owner-only cursor-resumable event snapshot |
| `POST /api/web/watchlists/{id}/ai-filter/reconcile` | Owner-only demand trigger; rate-limited to 12 per minute, validates scope, clamps the requested cursor to the persisted frontier, and starts/joins Workflow |
| `GET /api/web/watchlists/{id}/ai-filter/decisions?bucket=accepted` | Owner reads accepted results plus state; shared viewers may read persisted accepted results only |

## Fluid Compute profile

The web path is designed around fewer invocations and bounded active CPU:

- the cached app layout does not read cookies or session state;
- authenticated bootstrap is one conditional client request, not four layout
  reads;
- initial broad watchlist data resolves count and page reads concurrently;
- persisted accepted pages hydrate at most 100 IDs in one Typesense query;
- page-zero totals use a Postgres aggregate rather than hydrating every match;
- owner decisions and progress return from one endpoint invocation;
- polling is bounded, cancellable for safe GETs, and suspended while a
  foreground load is active or the browser tab is hidden;
- background prefetch starts one 500-candidate demand per visible result cursor,
  not an automatic loop through the full horizon; and
- long-running provider work runs in Workflow rather than holding a route
  invocation open.

Do not move authoritative paid-call state into an in-memory or regional cache,
add a second client polling loop, hydrate all accepted decisions to compute a
count, or make app layouts request-bound. Validate regressions with
`pnpm cpu:gate` and the production procedure in
[`18-vercel-fluid-cpu.md`](18-vercel-fluid-cpu.md).

## Deliberate MVP exclusions

There is no eval-dataset creation, provider bakeoff, fallback model, or formal
privacy/legal workstream in this release. Notifications remain independent of
narrowed decisions. Making notification delivery consume Jev results requires a
separate delivery and readiness contract; notification matching must never
silently become an execution trigger.
