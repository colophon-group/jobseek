# Fluid CPU verification — September 25, 2026

The first complete window after the current production deployment **fails the release gate**. Billed Active CPU fell from 296s to 260s (12.2%), almost entirely in middleware. Company rendering remains the largest visible Function cost: 124s of 182.498s (67.9%). This is an observed comparison between different traffic mixes, not causal attribution of the entire decrease to the fixes.

## Identity and fixed window

The window is **September 24 21:15:00 to September 25 09:15:00 UTC**, recorded before any synthetic live checks. Production was `dpl_8uQxcstjhqTq2QtJu3YqcbUt1V6Z`, SHA `3e9e737c4cc2f37136ec37ccb32d9990349a3bb4`, Ready at `2026-09-24T20:17:40.685Z`. [Owned workflow 36053535652](https://github.com/colophon-group/jobseek/actions/runs/36053535652) verified promotion at `20:19:20.785819Z`; both staged company HIT guards passed without Functions. This matched the latest web-relevant main commit; main at collection was `b506166411d3834e4a72f8d483600c2a89ea260c` (later crawler changes).

The freshly read active WAF remains version 7, updated `2026-08-30T18:27:57.013Z`. Version 8, updated August 31, remains an unpublished draft. Thus the entire window follows both production promotion and WAF publication.

## Billing and exhaustive Functions attribution

Both billing windows were re-read in the same session. [September 23 04:05–16:05 UTC baseline](https://vercel.com/viktor-shcherbakovs-projects/~/usage/vercel-functions-fluid-cpu-duration?view=Type&from=1790136300000&to=1790179500000) now shows 235s Functions + 61s middleware = **296s**. [The after window](https://vercel.com/viktor-shcherbakovs-projects/~/usage/vercel-functions-fluid-cpu-duration?view=Type&from=1790284500000&to=1790327700000) shows 234s Functions + 26s middleware = **260s**, all attributed to jobseek-web. Middleware decreased by 35s (57.4%); Functions decreased by 1s (0.4%). Dashboard amounts are rounded, not raw billing precision.

The baseline **Functions route** window is no longer retained on Hobby: Vercel replaced the absolute query with `period=1h`, and September 23 was disabled in the date picker. Those one-hour results were excluded. Previously recorded baseline route values remain historical evidence; they were not reverified today.

The [after Functions table](https://vercel.com/viktor-shcherbakovs-projects/jobseek-web/observability/vercel-functions?pageSize=50&from=1790284500&to=1790327700), Production, Show 50, page 1 of 1, contained 31 rows. [measurement.json](2026-09-25-vercel-fluid-cpu/measurement.json) preserves every row, including zero-CPU rows.

| Exhaustive group | Invocations | Visible CPU | CPU limit | Result |
|---|---:|---:|---:|---|
| Company OG | 0 | 0s | 24s | Pass |
| Company pages | 513 | 124s | 60s | Fail |
| Public watchlists | 88 | 12.790s | 25s | Pass |
| Explore | 193 | 16.360s | 10s | Fail |
| Other | 439 | 29.348s | 19.5s | Fail |
| **Total** | **1,233** | **182.498s** | **138.5s** | **Fail** |

The groups reconcile exactly to the sum of all displayed rows. Company CPU by locale: FR 36s, EN 35s, DE 29s, IT 24s. Largest other routes: Typesense-key 8s, MCP 7s, v1/search 3.3s. **51.502s remains unassigned between billed Functions (234s) and summed visible route CPU (182.498s)**. Middleware is separate and does not explain that gap; no evidence identifies its cause.

Active CPU P75 **323ms** exceeds 308ms; throttle P75 **8%** exceeds 7.6%. Errors and timeouts both display **0%**, and all 31 route rows display zero errors. Cold starts display 11.3%; wall-clock duration was never counted as CPU.

## Cache and real requests

[PPR for the same window](https://vercel.com/viktor-shcherbakovs-projects/jobseek-web/observability/ppr?from=1790284500&to=1790327700) shows 210 HIT, 32 STALE, and 646 MISS: the runbook hit/(hit+miss) ratio is **24.53%**, below 35%. Including stale in the denominator would give 23.65%. Company shell hit rate displays 23.5%. The overall dynamic invocation count is 455 while all route dynamic counts show zero; this inconsistency is preserved, not resolved by assuming either is correct.

The retained request sample was collected before probes: **93 unique requests**, September 25 `08:19:29.763–09:14:47.269 UTC`, two deduplicated pages. This is less than an hour and **does not include all traffic in the 12-hour window**. All requests belong to the measured deployment.

[Sanitized company events](2026-09-25-vercel-fluid-cpu/company-requests.json) contain 53 requests:

- **25 GET HITs:** static PPR, only static events, **zero Functions**. This includes one curl request; the other 24 are retained natural traffic.
- **25 GET REVALIDATED:** `cacheReason=delete_tag`, static response plus a background Function.
- **1 GET STALE:** `cacheReason=stale_time`, static plus background Function; this request used curl and is not treated as natural bot coverage.
- **2 BYPASS:** one POST and one GET to a literal placeholder company path, both `prerender_bypass` with Function work.

There are **31 distinct company keys** after excluding curl and the literal placeholder. Three requests claim PetalBot and one claims Applebot; these are user-agent classifications, not cryptographically verified bot identities. The observed bot/key coverage is real retained traffic, but the complete-window coverage gate remains unavailable. No hot-key synthetic results substitute for it.

## Targeted next fix: viewer preferences evict shared company pages

[Issue #10013](https://github.com/colophon-group/jobseek/issues/10013) tracks a concrete unnecessary invalidation. `updatePreferences({jobLanguages})` calls `revalidatePath("/[lang]/(app)/company/[slug]", "page")` after anonymous cookie writes, authenticated updates, and authenticated inserts. Next.js documents that a dynamic page pattern invalidates every matching page. Company SSR uses anonymous defaults; the browser loader applies saved viewer languages. One viewer's preference write therefore needlessly evicts all company/locale shells.

Remove that company pattern, preserve preference persistence and existing public-watchlist invalidation, and cover all three write branches. Explore already follows this model. This matches the observed invalidation mechanism, but request logs do not expose the deleted tag or triggering action: **the 25 delete_tag events cannot all be attributed to this action**. Savings require a new production window.

A competing crawler-deployment hypothesis was checked, not acted on. Three actual successful crawler deploy logs during the window (runs 36100644584, 36095456629, 36063447337) report `invalidate.typeahead.skipped`, because the web invalidation URL or token is unset. Those runs do not establish crawler-triggered invalidation. One retained time-based expiry does not justify changing company cache lifetimes.

## External calls, unavailable fields, and functionality

External APIs displays Typesense **2.8K**, Upstash **1.6K**, assets 421, jseek.co 128, Vercel Data Cache 22, Jev 10, Google OAuth 2. Typesense/Upstash are rounded labels, so exact counts and exact per-invocation gate values remain null. Company-OG R2 hit/miss counts are not exposed by these Vercel views. Vercel CLI 60.0.1 metrics still returns HTTP 402 requiring Observability Plus; upgrading the local CLI did not change plan access. The activity query returned no events and adds no attribution evidence.

Live checks ran after the measurement window: localized home and company HTTP 200; Aircall facts and hydrated postings; keyword `engineer` reduced active company postings from 77 to 30; company OG HTTP 200 with valid 1200×630 PNG; scanner-shaped company path HTTP 403. Explore initial results and keyword filtering passed; the existing session loaded Account settings and connected-account controls. Sign-in returned 200. Results are preserved in [functionality.json](2026-09-25-vercel-fluid-cpu/functionality.json).

The public-watchlist check remains **false**, not a pass inferred from HTTP 200. UUID detail URLs redirected to the private overview; the former public `/en/colophongroup/maangplus` returned the not-found document. Current source intentionally permits only an owner redirect on the legacy route, since #8795 (`766d37dfc`, September 10). The runbook predates that product change. This is an obsolete gate requirement, not evidence that this small cache change broke access. Access protections were not changed.

The machine report intentionally retains nulls for unavailable numeric fields. The unmodified `cpu:gate` rejects an incomplete report; no null is replaced with zero or a rounded external-call estimate. Independently, the measured CPU, P75, throttle, and PPR results already fail. The remediation is not complete.

Validation for #10013: regression test first failed on the old implementation; after the change, 26 tests passed across preference revalidation, company browser data, and browser filter-state suites. Changed-file ESLint and `git diff --check` passed. Production deployment must use the owned workflow, then restart the 12-hour window.
