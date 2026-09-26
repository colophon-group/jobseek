# Vercel Fluid CPU verification — September 26, 2026

The clean **2026-09-26 00:20–12:20 UTC** window **fails** the CPU gate. It was fixed before any synthetic live probes. Billing and exhaustive visible route CPU are separate measurements. All values below are dashboard observations, not wall-time estimates.

## Identity and billing

Production was `dpl_4V7ivfskfQ8vqZFwStsXmy7jawYv`, SHA `c0066936b6a9f334c447ff5aa2ec1aff436c95ef` (#10026), Ready September 25 **16:17:10.481 UTC**. [Owned workflow 36159311384](https://github.com/colophon-group/jobseek/actions/runs/36159311384) verified promotion at **16:18:34.6665605 UTC**. Both staged company HIT traces contained zero Function invocations. The cumulative classifier from production to freshly fetched main `36d3c27e2ae5cde41110b0a4c0672f754f597daa` returned no web-relevant changes.

Active WAF was re-read: v7, published August 30 **18:27:57.013 UTC**. Draft v8 remains unpublished. The entire window follows both promotion and publication.

| Billing measure | Sep 23 04:05–16:05 UTC baseline, re-read | Sep 26 00:20–12:20 UTC |
|---|---:|---:|
| Functions CPU | 235s | 316s |
| Middleware CPU | 61s | 26s |
| Total | 296s | 342s |

Total billed CPU increased **15.5%** over this baseline. Middleware remains **57.4% lower**. The previous clean September 25 report had 260s total. Neither comparison isolates a code change from differences in traffic. The preference-invalidation fix is present, but its individual savings cannot be inferred from these totals.

## Exhaustive route reconciliation

All **42 rows**, page 1 of 1, Production, exact absolute window:

| Group | Invocations | Visible Active CPU | Budget | Result |
|---|---:|---:|---:|---|
| Company OG | 0 | 0s | 24s | Pass |
| Company pages | 698 | 133s | 60s | Fail |
| Watchlist detail/legacy routes | 80 | 9.670s | 25s | Pass |
| Explore | 83 | 7.717s | 10s | Pass |
| Other | 1,176 | 97.128s | 19.5s | Fail |
| Total | 2,037 | 247.515s | 138.5s | Fail |

The groups reconcile exactly to the sum of displayed route rows. Displayed values are rounded. Billed Functions CPU exceeds the visible route sum by **68.485s**; that unexplained difference is retained, not allocated to routes. CPU P75 **264ms** passes; throttle P75 **8.5%** fails. Errors and timeouts both display **0%**. Cold starts display 6.3%.

The largest individual non-company route is `/api/v1/search`: **55s / 766 invocations** (versus 3.3s / 33 in the prior window). Workflow step/flow routes add 16.29s / 40. This is measured traffic growth, not proof of a cache regression. A later synthetic public-search request was MISS then HIT, demonstrating that its CDN cache can work.

## Cache and natural traffic

PPR totals: **166 HIT, 66 STALE, 475 MISS**, so the gate's HIT/(HIT+MISS) ratio is **25.9%**, below 35%. Overall dynamic invocations display 487 while every route breakdown displays zero; preserve that inconsistency.

Before probes, the authenticated request-log endpoint yielded **656 deduplicated requests**, but only **11:25:05.043–12:19:54.888 UTC** was retained. Fourteen shrinking-bound queries were used; timestamp ties and an undocumented sampling contract prevent a completeness claim. Raw session IDs, query strings, client details and referrers stay outside Git.

The company subset contains 55 requests: **36 PRERENDER/resuming**, **13 STALE/stale_time**, and **6 static-only HITs**. Resuming rows include both a background Function and a PPR Function event. No `delete_tag` occurs in this short sample; that does not prove that no invalidation happened earlier. Excluding curl leaves **48 company/locale keys**. The whole retained sample contains 41 bot-named user agents, which are claims rather than verified identities.

The search subset contains 401 successful GETs from one application user-agent family, 48 distinct query combinations, each appearing 6–10 times. All are MISS. Raw query values are private. This does not contradict the later synthetic HIT: headers, regions and caller behavior require investigation before selecting a cache fix.

The independent Hobby archive provides no queried coverage of this selected window: its summary reports zero retained runtime IDs, a full-window query gap and 14 query errors. It cannot fill the missing hours. `sourceIncludesAllTraffic` remains false and sampling is unknown.

Exact outbound counts remain unavailable. The UI displays Typesense **12K**, Upstash **3K**, assets **1.3K**; those rounded labels are not exact counts. CLI was upgraded to **60.1.3** and still returns `payment_required` for `vercel.external_api_request.count`. The owner's decision against a paid telemetry upgrade remains in force. The evaluator now accepts an unknown sampling percentage as null and reports it INCOMPLETE, instead of requiring an invented percentage.

## Targeted fix: shared layout shortened daily company shells

[Issue #10036](https://github.com/colophon-group/jobseek/issues/10036) records the remaining expiration defect. A fresh main build with the configured database proves all four seeded company prerenders and locale fallback routes had **3,600-second revalidation**, despite explicit daily company body/metadata caches. The shared app layout's hourly currency-rate read was outside those daily boundaries.

A daily cached **display-rate snapshot** around that read changes all four compiled company prerenders and fallback lifetimes to **86,400 seconds**. Server-side filtering retains its hourly currency service. Display conversion rates now use daily stale-while-revalidate behavior (one-week hard expiry), appropriate to the ECB daily feed; the first visit after an idle period may show an older snapshot while refreshing. Job data still refreshes in the browser and access protections are unchanged.

The new compiled-artifact guard rejects the old build with `[3600,3600]` and passes the patched build. This proves the cache-policy correction, **not billed CPU savings** or attribution of all 133 company seconds to that defect.

Validation: full baseline and patched builds; bundle/cache guard; 72 salary/filter/bootstrap tests and nine CPU-gate tests. New production deployment requires another clean 12-hour window.

## Live functionality and evidence

Checks ran after the fixed measurement window. Home, Explore results, Aircall facts/postings, sign-in and an existing authenticated session were usable. An owner saw an actual UUID watchlist title and results. A pre-existing explicitly shared UUID watchlist showed its title and 126 jobs anonymously; no sharing permission was changed. A private UUID denied both anonymous and signed-in cross-owner visitors by returning them to the watchlist overview. Legacy routes denied anonymous/cross-owner visitors, while the owner navigated to the owned UUID detail.

The company OG metadata URL returned a valid **1200×630 PNG** directly from R2. A legacy hashed OG URL returned **308** to a valid R2 PNG. The configured fallback source version differs from the current pointer version; both objects exist. A scanner-shaped company path returned **403**.

Sanitized evidence: [measurement and complete route rows](2026-09-26-vercel-fluid-cpu/measurement.json), [company request sample](2026-09-26-vercel-fluid-cpu/company-requests.json), [compiled cache lifetimes](2026-09-26-vercel-fluid-cpu/cache-lifetimes.json), [functionality](2026-09-26-vercel-fluid-cpu/functionality.json), and [gate input](2026-09-26-vercel-fluid-cpu/gate-report.json). No raw client/session/query data is committed.
