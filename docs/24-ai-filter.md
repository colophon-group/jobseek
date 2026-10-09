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

## Continuous freshness and historical evaluation

Each immutable query revision describes the full active historical horizon,
starting at `2000-01-01T00:00:00.000Z` and ending at a whole-second boundary.
This is a selection boundary, not an instruction to evaluate the entire feed
at setup time.

An enabled saved request authorizes continuous matching, including while its
owner is away. The Hetzner `jobseek-ai-filter-refresh.timer` calls
`/api/internal/ai-filter-refresh` every minute in production. This runs on the
existing crawler host and avoids Vercel Hobby's daily-only cron limit. The authenticated endpoint claims at most 20 eligible watchlists,
oldest dispatch first, with PostgreSQL row locks and a 45-second dispatch floor.
Only the next target is claimed at a time, within a 45-second preparation
budget, so a timed-out invocation cannot repeatedly starve the end of its page.
Disabled configurations, expired entitlement, preview deployments, unavailable
credentials and either disabled execution switch contribute no work. Preparation is serial,
so a minute trigger cannot burst parallel scope scans. Two live freshness
segments are allowed project-wide, enforced under the shared project admission
lock; a full freshness lane also skips dispatch preparation until capacity frees.
Count-only scope checks retain the readiness receipt and use one search per
company batch, without sorting and hydrating UUID guard rows. Candidate row
selection still proves UUID ordering before and after every page.
 Lost
Workflow dispatches retry on the next scheduled pass. The prepared revision is
checked again so a stale dispatch cannot re-enable or overwrite an edited prompt.

Each refresh Workflow runs at most ten 50-candidate segments. It reads the full
active scope newest first and excludes current, unexpired decisions **before**
Typesense pagination. Each segment starts at offset zero in the remaining set;
new arrivals take priority even during a large initial backlog. This also covers
late indexing/enrichment of jobs whose `first_seen_at` is older than the last
refresh, and renews expired decisions. The exclusion set is bounded at 50,000
UUIDs and is sent in a POST search body, including stable-order guards and
company fanout. Oversized exclusions or a scope outside the existing 1–10,000
candidate boundary fail closed. The classifier, semantic cache, TDM policy,
spend reservations and configured budgets remain the execution boundary.

Under normal load, new searchable jobs begin evaluation on the next minute's
pass and appear after classification. Initial/changed requests and backlogs may
need multiple passes; provider failures, budget limits, capacity and scheduler
jitter can delay completion. The endpoint emits only aggregate dispatch counts.
The original historical cursor remains available for owner-driven paging, in a
separate segment lane. An old historical segment cannot block the refresh lane
or move its completed coverage backwards. Both lanes share the existing per-owner
and project concurrency limits. Opening or scrolling can still request a bounded
500-candidate historical runway, but freshness does not depend on those actions.

Reads remain side-effect free. Returning owners and shared viewers read persisted
accepted decisions through the ordinary page bootstrap. Reopening a narrowed
drawer, refocusing a visible tab, or restoring it from the browser cache reloads
the first persisted accepted page with a safe GET, even when `hasMore` was false.
React Activity route restoration also reloads after reconnecting its effects.
Changed results restart the accepted cursor; concurrent resume events coalesce
and late responses abort on scope changes. A complete cached page needs no
reconcile request. Foreground progress polling remains bounded at 1.5
seconds, with five-second background state polling while work is active and no
reads in hidden tabs. No paid work is started by a shared viewer or GET request.

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

Fairness defaults are one active segment per watchlist and lane (database constraint),
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

Migration `0103_ai_filter_freshness` adds the dispatch timestamp and independent
historical/freshness segment lanes. Apply it through the allowlisted routine
migration workflow before deploying the refreshed web worker. Then dispatch
`deploy-ai-filter-refresh.yml` at current main to install and activate the host
timer. Confirm local/public Typesense health and memory headroom before activation.
Its preflight requires the deployed web endpoint's `narrowed-refresh-v1`
contract before changing an existing timer. Leave the timer disabled after a search
OOM or while index recovery is in progress; a successful deployment alone does
not prove that background matching can run safely.

The Next.js Workflow SDK owns durable catch-up. Workflow code loops only over a
bounded Node.js step; Postgres, Typesense, R2, and Jev access remain inside that
step. Ambiguous provider transport failure is charged conservatively and is
not blindly replayed.

Required runtime configuration:

```text
AI_FILTER_ENABLED=true
AI_FILTER_JEV_1_13_0_ENABLED=true
AI_FILTER_CACHE_HMAC_SECRET=<at least 32 bytes>
AI_FILTER_REFRESH_SECRET=<dedicated random bearer, shared with the host timer>
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
| `GET /api/internal/ai-filter-refresh` | Scheduler bearer only; production-only bounded background Workflow dispatch |
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
- owner prefetch starts one 500-candidate demand per visible result cursor;
- minute refresh dispatch claims at most 20 watchlists and starts bounded workflows; and
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

## Freshness verification

- `pnpm exec vitest run src/lib/ai-filter src/lib/services/__tests__/watchlist-matcher.test.ts app/api/internal/ai-filter-refresh`
- `AI_FILTER_TEST_DATABASE_URL=postgresql://…@127.0.0.1:PORT/jobseek_ai_filter_fixture pnpm exec vitest run src/lib/ai-filter/refresh-pg.test.ts`

The PostgreSQL suite requires an explicit localhost database ending in `_fixture`
and recreates its public schema. It applies the real AI migrations and exercises
separate lanes, duplicate scheduler/worker claims, expiry renewal, disabled and
obsolete prompts, returned accepted results and late-indexed arrivals. Only the
external search, mining-policy and classifier surfaces are replaced. CI runs it
against a dedicated PostgreSQL 17 fixture. For production acceptance, require
successful minute trigger/dispatch logs and a decision for a newly indexed eligible job
without opening or scrolling its narrowed watchlist; a scheduled dispatch alone
is not completion evidence.

### Host scheduling and rollback

The host runner performs one fixed-URL HTTPS request with a 55-second timeout,
rejects redirects and oversized responses, and logs only aggregate counters.
Systemd uses `DynamicUser` and `LoadCredential`; the bearer source is root-owned
mode 0600 under `/etc/jobseek-ai-filter-refresh`. The runner has no Docker socket
or database credentials. A single oneshot service prevents overlapping host
triggers; PostgreSQL dispatch claims, leases and the semantic cache handle an
ambiguous/lost response and duplicate requests.

The manual deployment workflow binds an owner dispatch to current main and uses
host-key-pinned SSH and the dedicated protected `AI_FILTER_REFRESH_SECRET`.
Provision the same random value in the production GitHub environment and Vercel
project before deploying the web revision. It authorizes only this endpoint and
is separate from the existing, unreadable sensitive Vercel cron credential.
Credentials move over SSH stdin, never command arguments or logs. The
installer records the revision, verifies the web contract before changing host
state, and restores the prior timer/files on a failed installation. It does not
restart crawler writers or any other timers. Rotate the host credential by
rerunning the same deployment workflow after rotating the shared `AI_FILTER_REFRESH_SECRET`.

Check `systemctl is-active jobseek-ai-filter-refresh.timer` and bounded
`journalctl -u jobseek-ai-filter-refresh.service` evidence. Stop and disable only
`jobseek-ai-filter-refresh.timer` to roll back background scheduling; owner-driven
matching remains available. Roll back web code only to a compatible reviewed
revision, leaving the additive migration intact.
