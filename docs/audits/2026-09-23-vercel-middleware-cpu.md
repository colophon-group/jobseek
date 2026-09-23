# Middleware CPU follow-up — 2026-09-23

Issue: [#9917](https://github.com/colophon-group/jobseek/issues/9917).
The parent [audit](2026-09-23-vercel-fluid-cpu.md) attributes 56s of the exact
12-hour billed CPU window to middleware. This follow-up measures a specific
source of that work, not a percentage reduction in the production bill.

## Measurement

Two production builds of Next 16.3.4 were measured on the same machine using
Node **24.21.0**, matching production's Node major. The baseline includes the
company caching fix in PR #9925; the sole application change in the comparison
is deferred imports in `proxy.ts`. Each scenario ran in eight fresh processes.
The harness requires the **compiled** `.next/server/middleware.js`, measures
`process.cpuUsage()` around module loading, then calls its exported framework
handler six times. It awaits the response body and `waitUntil` work. The warm
value is the median of requests 2–6 within each process. Table entries select
the upper median of the eight observations. Raw observations are retained.

| Scenario | Baseline module CPU | Deferred module CPU | Baseline first request | Deferred first request | Baseline warm | Deferred warm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Locale redirect | 187.810ms | 100.889ms | 11.845ms | 8.671ms | 0.801ms | 0.881ms |
| Obsolete action rejection | 186.466ms | 105.959ms | 8.900ms | 6.611ms | 0.637ms | 0.679ms |
| Anonymous legacy watchlist 404 | 187.215ms | 100.304ms | 9.682ms | 7.172ms | 0.731ms | 0.738ms |
| Unknown company guard | 189.083ms | 101.031ms | 9.803ms | 40.552ms | 0.694ms | 0.730ms |

These scenarios use the CI secretless configuration, making **zero external
calls**. The unknown-company case still loads the resource-status module when
needed, hence its higher first-request cost. It still avoids auth loading.
The authenticated branch retains session verification and then the owner
lookup; no claim is made that authenticated requests avoid those dependencies.
The suite of 106 proxy tests passes with the change, including successful and
rejected public-read rate-limit checks, session verification, owner access,
missing/private resource behavior, and the real matcher cases. ESLint and a
full production build pass.

A separate microbenchmark ruled out locale negotiation as an attractive
optimization: `Negotiator` plus `@formatjs/intl-localematcher` used only
0.006–0.034ms median CPU for representative empty/English/German/French/Chinese
Accept-Language inputs (25 observations each).

## Production relevance and limits

The original 130-request retained sample contains 48 middleware events:
36 hot and 12 cold. Its route/method split is 19 Explore POSTs, 18 UUID
watchlist POSTs, three company POSTs, three UUID watchlist GETs, three root
GETs, and two legacy watchlist GETs. These are event counts, not CPU weights;
POSTs alone do not establish Server Action traffic because PPR uses internal
POST resumes. The sample is short and cannot establish the full-day cold rate.

The compiled module-load reduction is **43–46%** across the tested scenarios,
roughly 80–88ms of local CPU per fresh process. Warm CPU is essentially
unchanged. Production combines instance reuse, concurrency, network clients,
and platform initialization; this benchmark does not multiply that local
saving by all invocations or predict a 43–46% middleware bill reduction.

## Reproduction

Build the app with the repository's secretless CI environment, then from
`apps/web` run Node 24 against
`../../docs/audits/2026-09-23-vercel-middleware-cpu/proxy-compiled-bench.cjs`
with an absolute output JSON path. The harness launches a fresh Node process
for each observation and reads the compiled output in the current directory.
Rebuild before comparing another variant. All evidence is in the adjacent
[directory](2026-09-23-vercel-middleware-cpu/); it contains no credentials or
raw client identifiers.
