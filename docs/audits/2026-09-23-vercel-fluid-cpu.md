# Vercel Fluid CPU audit — 2026-09-23

Company-page rendering is the largest identifiable application cost in the measured traffic. Middleware is a separate, material cost. A local experiment demonstrates a way to stop repeated company-page PPR resumes; production savings have not yet been measured.

This audit used an isolated worktree based on freshly fetched `origin/main`, commit `479d4734767f6f1e5fe3c740d9dc7b755b34b92f`. The production deployment at collection was `b72bcd59a02528d5d9a3b398e1c0a58519d93210` (`dpl_GyvJQ4RBbuvXF1e5x4YAXsufdrJP`). There are no `apps/web` or `packages/mcp-server` differences between those commits. Vercel CLI was upgraded from 59.15.1 to 59.25.4. No deployment, subscription, firewall rule, or production configuration was changed.

## What the allowance is paying for

The authenticated [team Usage overview](https://vercel.com/viktor-shcherbakovs-projects/~/usage) showed **4h 3m / 4h Fluid Active CPU, 101.25% of the Hobby allowance**, at approximately 16:17 UTC. Function invocations were only 97K / 1M, and provisioned memory was 71.5 / 360 GB-hours. CPU is the exhausted resource.

The displayed last-30-days window was “Aug 24, 7pm – Sep 23”. An earlier capture of its CPU breakdown showed Functions **3h 11m (78.8%)** and middleware **51m 30s (21.2%)**. Project attribution was **99.9% jobseek-web**; jobseek-web-ui-review used approximately 15 seconds. These are rounded dashboard values captured a few minutes before the overview, so they should not be expected to sum to its refreshed total.

The displayed last-seven-days breakdown was Functions **42m 3s** plus middleware **10m 20s**, or **52m 23s**. This establishes that middleware's roughly one-fifth share is also present in recent traffic. It does not identify the historical Functions total by route.

## Exact 12-hour observation

All following production aggregates use **2026-09-23 04:05:00–16:05:00 UTC** (06:05–18:05 Europe/Zurich). This window spans multiple deployments, so it is an attribution sample, not a controlled before/after result or a pass of the repository's release gate.

The [billing view](https://vercel.com/viktor-shcherbakovs-projects/~/usage/vercel-functions-fluid-cpu-duration?view=Type&from=1790136300000&to=1790179500000) reports **215 seconds Functions + 56 seconds middleware = 271 seconds**. Grouping by project shows only jobseek-web in this window.

The [Functions route table](https://vercel.com/viktor-shcherbakovs-projects/jobseek-web/observability/vercel-functions?environment=all&pageSize=50&from=1790136300&to=1790179500) contains 32 rows, totaling **1,577 invocations and 168.205 seconds visible Active CPU**. Production and all-environment queries returned the same totals.

| Route/group | Invocations | Visible CPU | Share of visible route CPU |
| --- | ---: | ---: | ---: |
| Company pages, all four locales | 416 | 87s | 51.7% |
| `/en/watchlists/[watchlistId]` | 275 | 19s | 11.3% |
| `/mcp` | 344 | 15s | 8.9% |
| `/en/explore` | 84 | 9s | 5.4% |
| `/api/typesense-key` | 102 | 9s | 5.4% |
| All other visible rows | 356 | 29.205s | 17.4% |

Company CPU splits into English 44s, French 19s, German 12s, Italian 12s. Company pages average approximately **209ms CPU/invocation**, versus **44ms for MCP**. MCP has more invocations than any single route, but is not the largest CPU consumer.

**An unresolved discrepancy remains:** 215s billed Functions versus 168.205s summed visible route CPU, approximately **46.8s unassigned**. Middleware is already separated in billing and is not that gap. Switching the route query from Production to all environments did not explain it. No evidence collected identifies the remaining difference as cold-start overhead, hidden routes, or anything else. Route shares above use 168.205s, never the billed total. Dashboard row values are rounded.

The Functions overview also showed Active CPU P75 205ms, throttle P75 12.9%, cold starts 7.8%, errors 0.1%, and zero timeouts. These aggregate indicators do not identify the CPU-heavy stack frames.

## Why company cache hits still cost CPU

The [PPR dashboard](https://vercel.com/viktor-shcherbakovs-projects/jobseek-web/observability/ppr?from=1790136300&to=1790179500) reported 421 shell hits, 38 stale shells, 402 misses, and 771 dynamic invocations. The English company route had a 75% shell hit rate and 191 dynamic invocations. A shell hit is not proof of a complete cached response.

The retained runtime-log sample contains 130 unique requests between 15:14:20.729 and 16:04:51.780 UTC, including 18 company requests:

- **12 HIT requests:** all had `pprState: resuming` and a company Function event.
- **3 STALE requests:** all resumed, with additional background revalidation events.
- **3 BYPASS requests:** separate company requests with middleware events.

This proves that cached company shells still execute server work in the current deployment. Log `durationMs` measures wall duration; it was not counted as CPU. POST is also not evidence of abuse: the documented [PPR adapter protocol](https://nextjs.org/docs/app/api-reference/adapters/runtime-integration) sends internal resume requests as POST. The sample contains both browsers and named automated clients, but it cannot establish what fraction of billed CPU is unwanted traffic.

The source explains the caching boundary:

- `apps/web/next.config.ts` enables `cacheComponents`, but not `partialPrefetching`.
- `apps/web/app/[lang]/layout.tsx` supplies the four locale params.
- `apps/web/app/[lang]/(app)/company/[slug]/page.tsx` caches the company snapshot and page for a day, but has no `generateStaticParams` for company slugs.
- The baseline build has no concrete company routes in `prerender-manifest.json`; locale-specific company fallbacks retain unresolved `slug` params.
- The default company data path is anonymous. Filtered/personalized search and posting refresh already happen browser-direct through Typesense. Increasing the data TTL or moving that search to the browser again would not address the demonstrated resume.

The installed Next.js 16.3.4 documentation describes [ISR with Cache Components](https://nextjs.org/docs/app/guides/incremental-static-regeneration-cache-components): explicit params can be prerendered, and Partial Prefetching can upgrade an unlisted route's shell after its first visit. The following experiment tests this behavior in this application.

## Local experiment, with measurements

Three production builds were run on the same machine, Node 25.2.1 / Next 16.3.4. Each variant received six sequential anonymous HTML GETs to `/en/company/aircall`, then six to `/fr/company/hellofresh`, with one second between requests. The first request to each path is reported separately in the evidence and excluded from warm medians. Server CPU is `process.cpuUsage()` user + system delta from request dispatch to response finish. The HTTP client ran in a separate Python process.

| Variant | Aircall warm CPU median, n=5 | HelloFresh warm CPU median, n=5 | Cache behavior |
| --- | ---: | ---: | --- |
| Unchanged main | 46.880ms | 64.960ms | All 12 responses postponed; private/no-store |
| Add only `partialPrefetching: true` | 53.436ms | 34.645ms | All 12 still postponed; private/no-store |
| Add flag plus `generateStaticParams() => [{slug: "aircall"}]` | **7.701ms** | **7.343ms** | Aircall cached immediately; unlisted HelloFresh cached after first visit |

The final variant reduced the measured warm medians by **83.6% and 88.7%** relative to baseline. Its cached responses carried `x-nextjs-cache: HIT`, no `x-nextjs-postponed`, and `Cache-Control: s-maxage=3600, stale-while-revalidate=82800`. HelloFresh was deliberately absent from the seed, demonstrating on-demand upgrade rather than only a hot build-time key.

All three builds completed compilation, TypeScript, and prerendering. They emitted safe external database fallback warnings while building watchlist content; therefore these builds do not prove all upstream services were healthy. All 36 measured HTML responses returned 200 and contained the correct company name. A local browser smoke check additionally showed company facts, similar companies, and the job list hydrating on the final variant. Applying the `engineer` title filter updated the UI and similar companies but the job results showed an error. A fourth build of unchanged main reproduced the same error in the same browser/environment. It is not specific to the prototype in this test; the underlying cause was not established, and the filter functionality gate remains unpassed.

These are **local comparative measurements**, not Vercel billing estimates: production runs Node 24 on Fluid, `next start` handles the full request itself, the Vercel adapter splits CDN delivery from resumes, and the instrumented interval excludes work after response finish. The local server also emits the known standalone-output warning. Five warm observations per path and two company keys are insufficient for a production savings claim. The cache transition and the reduction in repeated server work are nonetheless directly observed.

The experimental changes were saved as [a patch](2026-09-23-vercel-fluid-cpu/experiment.patch) and removed from application source after the test. This is not a deployment-ready hardcoded Aircall change.

## Recommended work, in order

1. **Implement company-route prerender seeding plus Partial Prefetching.** Use a small, deterministic set of valid company slugs from the canonical registry, preserve on-demand handling for the long tail, and avoid generating the entire company × locale matrix. The one-seed experiment shows that all companies do not need to be built ahead of time. Preserve existing missing-company behavior, metadata, anonymous/personalized boundaries, and browser filtering. The config affects the whole application, so test navigation, auth, watchlists, and invalidation before release. Increasing the TTL alone and enabling the flag alone are not supported fixes.
2. **Profile middleware next.** Its 56s is 20.7% of this billed window and roughly 21% of the month. `proxy.ts` eagerly imports auth and public-resource database services, and eligible public reads execute two Upstash rate-limit checks. Canonical company GETs already bypass the proxy; removing that matcher again will not solve the company rendering problem. Measure startup/import CPU and route-specific CPU, then isolate heavyweight auth/database loading to paths that need it. Preserve rate limiting and private-resource access checks. The current evidence identifies the middleware budget, but does not quantify which dependency dominates or promise a savings percentage.
3. **Keep measuring total billed CPU and visible route CPU separately.** Capture a clean 12-hour window after deployment and the last relevant WAF publication, using `docs/18-vercel-fluid-cpu.md`. Include at least 20 distinct long-tail keys and real bot traffic, compare invocations and CPU per company request, confirm cached responses no longer resume, check Typesense/Upstash call counts, and run all functionality gates. Ask Vercel to reconcile the approximately 46.8s billing-versus-Observability gap if it persists. No CPU release gate was declared passed in this audit.

The company route's entire observed 87s is only about **32% of the 271s billed window**, a useful ceiling on the directly attributed opportunity, not an achievable-savings forecast. Middleware remains significant even after company rendering improves.

Company/site OG generation is already off Vercel in this source and no OG route appears in this window. The two visible AI-filter read endpoints total only 0.14s. There is no measurement supporting an OG rewrite, AI workload migration, or a blanket bot block as the first intervention for this sample.

## Evidence and reproduction

[Evidence directory](2026-09-23-vercel-fluid-cpu/) contains the complete route table, timestamped sanitized company request events, dashboard extracts, summary arithmetic, HTTP/CPU observations, prototype patches, and local request/server harnesses. Credentials, raw user identifiers, query strings, and raw runtime logs are excluded.

The CLI metrics endpoint returned HTTP 402 requiring Observability Plus; the billing-cost endpoint returned 404 for this Hobby team. Included dashboard data was used instead. Panels explicitly labeled DEMO DATA were excluded. CLI `logs --limit 500` repeated the same 50 request IDs, so log counts were deduplicated and subsequent collection used the read endpoint employed by the installed CLI with shrinking time bounds. Runtime logs do not retain the full 12-hour window on this plan.

To reproduce the local experiment in an isolated checkout: install locked dependencies; build `@jseek/mcp-server`; run `pnpm build` from `apps/web` with the authorized environment file loaded; start the supplied `start.cjs` from that directory with absolute `AUDIT_ENV_FILE` and a fresh `AUDIT_CPU_LOG`; run `python3 requests.py /absolute/path/http-results.json` from a separate terminal. Stop the server, apply the relevant patch with `git apply --unidiff-zero`, rebuild, and repeat into fresh output files. The server listens only on `127.0.0.1:43189`. Do not mix browser smoke requests into the 12-request CPU sample.

Vercel bills active execution rather than time awaiting network I/O; database or Typesense latency alone cannot establish the CPU bottleneck. See [Fluid usage and pricing](https://vercel.com/docs/functions/usage-and-pricing).

## Delivery follow-up (#9916)

The implementation derives one lexical seed from the canonical registry at build
configuration time, so only four company documents are built and the CSV parser
stays outside the page's runtime dependency graph. The manifest assertion checks
both that bound and each locale's on-demand fallback. The service-backed smoke
check requires two unseeded company paths to return full cached HTML.

A further production-build comparison tested `prefetch = "partial"` on the
company page with the same seed, leaving the global flag off. Ten repeated
visits failed the full-cache assertion; a direct request still returned
`x-nextjs-postponed: 1` and private/no-store. Therefore the shipped candidate
uses the global `partialPrefetching` option, not the segment-only option.

The filtered-search failure above was traced to an invalid/revoked browser-key
parent in the main checkout's env file. A child minted from that parent is
rejected with HTTP 403. The **same browser search provider and queries**, using
a public scoped child issued by production's `/api/typesense-key`, succeed:
Aircall 77 unfiltered / 30 engineer results; HelloFresh 189 / 89; each first
page returns 20 valid rows. No production secret was extracted or changed.
This resolves the local credential diagnosis; deployed preview verification
still needs to exercise the complete UI and actual preview key issuance.

The final global-flag candidate passes the secretless production build, all
20 build-classifier assertions, and the full existing browser smoke suite
(localized initial HTML, filtered fallback, navigation action counts, SPA
navigation, company-request redirect, and real missing-resource 404s). Its
secretless Settings prefetch logs a cache-warming miss for the deliberately
unavailable Typesense language lookup; all smoke assertions still pass. The
service-backed build's unseeded company cache checks passed separately. These
checks establish build and local behavior; they do not establish production
CPU savings.
