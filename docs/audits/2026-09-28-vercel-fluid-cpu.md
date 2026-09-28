# Fluid CPU feature attribution — September 28, 2026

Jobseek used approximately **11m 39s of billed Fluid CPU in 24 hours**.
The requested target is **under 5 minutes per day**: approximately **399 seconds,
or 57%, must be removed** at this traffic level. This audit identifies the
consumers; it does not claim that the reduction has been implemented.

Company-page generation is the first optimization target. It accounts for
approximately **68% of visible Function CPU**, and its shell-cache hit rate is
only **36.3%**. Public REST search is second among visible Function routes.
Middleware is a separate, smaller consumer. A material billing-versus-route
gap prevents a complete allocation of the quota to features.

## Measurement boundaries

- Isolated branch: `fix-crawler/fluid-cpu-audit`, based on freshly fetched
  `origin/main` at `123a3e928c1a06137c4ad643bae7afa1bf6a683e`.
- Latest production deployment inspected: `dpl_BoU6LRTVfDZRN7UnaHMD2qqwHPP7`,
  commit `fb18106269ba42a672b89617f3d31dc5d8e92aac`, ready September 28 at
  08:30:27 UTC.
- Billing window: **September 27 09:30–September 28 09:30 UTC**, 24 hours.
- Feature attribution: **September 27 21:30–September 28 09:30 UTC**, 12 hours,
  Production. Both route-table pages were captured: **62 rows, 1,597 invocations**.
- Windows include several deployments. These are descriptive usage measurements,
  not a controlled before/after comparison or a clean release-gate result.

The authenticated dashboard provided the measurements. CLI metrics returned
`payment_required` for Observability Plus; CLI billing-cost data was unavailable.
Attempting a 24-hour route query made the dashboard substitute **Last hour**;
that result was excluded. The daily billing total is measured over 24 hours,
not extrapolated from the 12-hour route sample.

## Billed CPU and quota

| Jobseek billing | Measured 24 hours | Last 12 hours |
| --- | ---: | ---: |
| Functions | 10m 35s | 5m 58s |
| Middleware / Next.js Proxy | 1m 4s | 30s |
| Sum of displayed Type values | **11m 39s** | **6m 28s** |

The project-grouped 24-hour total displays 11m 38s; the separately rounded Type
values sum to 11m 39s. Keep this one-second display discrepancy explicit.
Other projects are immaterial: the project view shows Jobseek at 99.9%, with
Colabel at approximately one second. Project-filtered Type values above were
also checked directly.

The rolling 30-day overview showed **4h 18m / 4h**, approximately 107.7% of the
Hobby CPU allowance. Function invocations were about 101K / 1M and provisioned
memory 72.1 / 360 GB-hours. CPU is the constrained allowance. Five minutes per
day would be 2.5 CPU-hours over 30 days, leaving headroom under four hours.

[Jobseek daily billing](https://vercel.com/viktor-shcherbakovs-projects/~/usage/vercel-functions-fluid-cpu-duration?view=Type&from=1790501400000&to=1790587800000&projectId=prj_NqkWn9aYWWGsrxGjQf69vkR1POAi)
and [team monthly usage](https://vercel.com/viktor-shcherbakovs-projects/~/usage).

## Features consuming Function CPU

These percentages use **visible route CPU**, not total billed CPU. Durations are
approximate: the dashboard rounds its labels, including English company pages
to `1m`. The retained arithmetic sums those displayed labels to 238.930 seconds;
that is not a millisecond-precision measurement.

| Feature, last 12 hours | Invocations | Visible CPU | Share |
| --- | ---: | ---: | ---: |
| Company pages, four locales plus segment requests | 695 | ~163s | 68.4% |
| Public REST search `/api/v1/search` | 200 | ~20s | 8.4% |
| Hosted MCP `/mcp` | 288 | ~13s | 5.4% |
| Watchlists, including legacy routes and counts | 134 | ~8.85s | 3.7% |
| Explore, all locales | 67 | ~8.01s | 3.4% |
| Browser search-key issuance `/api/typesense-key` | 93 | ~7s | 2.9% |
| All other routes | 120 | ~18.62s | 7.8% |

Company-page CPU is approximately 60s English, 50s French, 26s Italian, 26s
German, and 1.45s for English segment requests. The approximate average is
235ms per company invocation, versus 100ms for REST search and 45ms for MCP.
These are route averages, not profiles of individual functions or stack frames.

Small consumers include sitemap generation (2.49s), job-alert delivery (0.8s,
one invocation), and the legacy blog OG route (0.16s). No company OG route or
AI-filter route appears in this table. Absence in this window is not proof of
zero lifetime cost. Typesense queries made directly by browsers execute on the
Typesense host; the Vercel key endpoint and server-rendered defaults are the
associated Vercel work.

[Production route table](https://vercel.com/viktor-shcherbakovs-projects/jobseek-web/observability/vercel-functions?environment=production&pageSize=50&from=1790544600&to=1790587800).

## Company pages: why they still cost CPU

The company route seeds one company at build time and generates other
company/locale shells on demand. Its outer snapshot is cached for one day.
On a cache miss, `fetchCompanyPageDefaults` assembles company details, the first
20 postings and counts, and 10 similar companies. Next.js then produces the
HTML/RSC payload and metadata. Browser hydration subsequently refreshes data
directly through Typesense.

Source entry points:

- [Company route](../../apps/web/app/[lang]/(app)/company/[slug]/page.tsx),
  `generateStaticParams`, `getCompanyRouteSnapshot`, and `CompanyPageRoute`.
- [Company defaults](../../apps/web/src/lib/actions/company-page-data.ts),
  `fetchCompanyPageDefaults`.
- [Company data services](../../apps/web/src/lib/services/company.ts),
  `getCompanyPostingsAnonymous` and `getSimilarCompanies`.

The [PPR dashboard](https://vercel.com/viktor-shcherbakovs-projects/jobseek-web/observability/ppr?environment=production&pageSize=50&from=1790544600&to=1790587800)
shows 322 shell hits and 607 misses overall, with **36.3% hits on company
shells**. Explore is 12%. These observations indicate substantial generation
work remains; they do not identify how much came from first visits, deployments,
expiration, or invalidation. Its overall dynamic-invocation count is 564 while
all route dynamic columns show zero; that inconsistent breakdown is not used
for attribution.

The retained request sample contains nine company request records, including
PRERENDER/resuming requests and a later static HIT for the same French company.
This is consistent with a generated shell becoming reusable. It does not
re-establish the previously fixed repeated-resume-on-HIT defect. One POST is
present; method alone does not establish a Server Action or unwanted traffic.

There is also a concrete invalidation mechanism worth measuring: the current
Go taxonomy sync calls `notifySyncTypeahead`, and the web endpoint revalidates
the shared `company-csv-data` tag along with taxonomy tags. Company snapshots,
metadata, and pages all carry that shared tag. Thus a sync can make every
company shell eligible for regeneration before its nominal day-long lifetime.
The collected evidence does **not** quantify how often that mechanism caused
misses in this window.

- [Go sync caller](../../apps/crawler/go/typesense-exporter/taxonomy_sync_run.go)
- [Invalidation endpoint](../../apps/web/app/api/internal/invalidate-typeahead/route.ts)
- [Shared invalidation registry](../../apps/web/src/lib/cache-registry.ts)

## Other features and existing optimizations

REST search parses/resolves filters, searches Typesense, shapes the grouped
response, and records post-response telemetry. Its successful public responses
already have a five-minute Vercel CDN lifetime. The next useful measurement is
cache misses and distinct normalized queries, followed by CPU profiling of
result processing and initialization; merely adding a cache duplicates existing
behavior. Hosted MCP can make internal REST calls, so their combined cost must
include both actual Function invocations without attributing all REST traffic
to MCP. Origin telemetry distinguishes that provenance when configured.

The search-key endpoint is already session-independent and generates a small
HMAC-derived key. Its CDN lifetime is 510 seconds against a 600-second key
expiry. Any change must preserve the expiry margin. Its ~7 seconds per 12 hours
is a small opportunity compared with company generation.

Middleware handles locale routing, access checks, public-read rate limits and
recovery paths. Auth loading is already conditional, and registered canonical
company documents already bypass Proxy. Those earlier optimizations should
not be proposed again as new fixes. Even eliminating all measured middleware
CPU would leave approximately 10m 35s/day of Functions.

## Unassigned CPU and the five-minute target

For the same 12 hours, billed Functions are **358 seconds**, while the sum of
displayed route CPU is approximately **239 seconds**: approximately **119
seconds remain unassigned**. Middleware's 30 seconds are separate and do not
explain this gap. All-environment route inspection adds three MCP invocations
but leaves every displayed CPU label unchanged, so visible preview traffic
does not account for it.

Do not label the difference cold starts, framework overhead, background work,
or company rendering without further evidence. Wall-clock log durations and
outbound-service latency cannot substitute for active CPU. Vercel meters CPU
during code execution, excluding time awaiting external I/O; see
[Fluid compute pricing](https://vercel.com/docs/functions/usage-and-pricing).

At the observed 12-hour rate, the daily target corresponds to 150 seconds per
12 hours. Removing all directly visible company CPU would still leave roughly
225 of the 388 billed seconds. That is a bound using this half-day mix, not a
daily prediction, but it demonstrates why a company-only percentage improvement
cannot yet guarantee the target.

Recommended order:

1. **Reduce company generation work.** Profile cold and warm long-tail company
   requests separately. Compare the current full defaults payload with a lighter
   shell that preserves company facts/metadata and lets existing browser-direct
   search load jobs and peers. Check initial loading behavior, share metadata,
   missing-company handling, and filtered navigation before adopting it.
2. **Measure and narrow broad company invalidation.** Record which syncs actually
   changed company data and correlate invalidations with cache reasons. Consider
   per-company and affected-industry invalidation for details and similar-company
   caches. Increasing the TTL alone does not address first visits or sync-driven
   invalidation. Broad prewarming also consumes CPU and requires a net-cost trial.
3. **Resolve the billed-versus-visible discrepancy alongside that work.** Keep
   both counters in each report and compare fixed, settled windows. Obtain a
   supported explanation from Vercel or additional profiling evidence before
   assigning savings to that bucket.
4. **Then optimize public search/MCP and remaining middleware.** Preserve current
   cache, access-control, key-expiry, and rate-limit behavior. Watchlists, alerts,
   and OG work are lower priorities at this traffic mix.

Acceptance is **less than 300 seconds of project-filtered billed Functions plus
middleware in a complete 24-hour window**, with representative traffic and
working product flows. The existing visible-CPU regression gate remains a
separate check; passing it alone does not prove the new billed daily target.

## Retained evidence and validation

[Measurement JSON](2026-09-28-vercel-fluid-cpu/measurement.json) contains all 62
routes, grouped totals, billing windows, target arithmetic, and limitations.
[Dashboard snapshots](2026-09-28-vercel-fluid-cpu/dashboard-snapshots.json)
retain the displayed values and selected windows.
[Sanitized request evidence](2026-09-28-vercel-fluid-cpu/recent-company-requests.json)
records the nine company requests from a CLI result containing 100 rows but only
50 unique IDs, spanning 09:09:39–09:27:48 UTC. It is partial evidence, not complete
traffic coverage. Client/session identifiers, queries, and raw messages are not
included.

Validated row uniqueness, invocation sums, exhaustive group reconciliation,
CPU unit conversion, and target arithmetic. This change contains audit documents
only; application code and production configuration were not changed.

## Optimization delivery

The subsequent implementation targets the company-page work identified above:

- With browser-direct search enabled, the shared server snapshot fetches company
  facts only. Job results and related companies load directly from Typesense.
  The jobs area stays in a loading state until the first bounded read settles;
  failures use the existing unavailable state. Environments with direct search
  disabled retain the anonymous server snapshot.
- The public snapshot imports the small company-detail service directly; auth
  and personalized-search services are loaded only by the disabled-direct-search
  fallback. This removes the broad server-action dependency from the cached read.
- Related companies always use global active-position totals. Entry filters and
  subsequent filter changes do not change the ranking/counts or trigger a server
  action. Links open the related company without inherited filters.
- Related-company pagination is also browser-direct, using explicit offset/limit
  and the existing 15-company anonymous cap. Failed reads retain visible peers
  and let the scroll hook back off. The server fallback remains available when
  the direct-search feature flag is disabled.
- The shared metadata/body snapshot, localized company facts, noindex directive,
  missing-company handling, and one-day cache lifetime are retained. Crawler sync
  invalidation is unchanged; narrowing it requires a reliable changed-company
  contract across publishing, retries and cache invalidation.

Local production-build comparison (eight company/locale paths, three requests
per path, one excluded module warm-up) is recorded in
[local-optimization-benchmark.json](2026-09-28-vercel-fluid-cpu/local-optimization-benchmark.json).
Cold response bytes fell from 2,516,575 to 2,069,495 (17.8%). Median cold wall
latency fell from 1,575.9 ms to 115.3 ms after removing the duplicate server
searches. All eight paths reached complete `HIT` responses without postponed
content. Aggregate cold CPU fell from 855.6 ms to 743.7 ms, but median CPU rose
from 64.6 ms to 83.3 ms; locale initialization and concurrent local test work make
this a noisy comparison, **not evidence that billed daily CPU meets the target**.

Verification before rollout: production build/typecheck, ESLint, 64 focused
regression tests, and the full web unit suite (2,854 passed, 41 skipped). The local
build classifier passed all 16 route/cache checks; its four fixture-oriented
Explore-content checks require the separate secretless CI build and do not pass
against this service-backed local build. CI must pass before merge. Browser checks
used real search results via the public production scoped-key endpoint because
local browser-parent credentials were stale: Aircall's Remote filter showed 3
active jobs, clearing it showed 77, and related-company global totals stayed
identical with unfiltered links. No credential configuration was changed remotely.

The acceptance criterion remains a clean 24-hour production window below 300
billed seconds (Functions plus Routing Middleware). This patch removes duplicate
reads and related-company server actions; it does not establish that daily target.
