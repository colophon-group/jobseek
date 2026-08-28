# Vercel Fluid CPU regression gate

This runbook prevents a hot-key benchmark from being mistaken for a real
production improvement. A Fluid CPU intervention is successful only after a
clean, fully post-deployment 12-hour window passes route, cache, traffic,
external-call, error, and functionality gates.

## 2026-08-11 baseline

| Metric | Clean 12h baseline |
|---|---:|
| Invocations | 1,500 |
| Visible Active CPU | 277s |
| Active CPU P75 | 308ms |
| CPU throttle P75 | 7.6% |
| Company OG | 120s / 271 invocations |
| Company pages | 91s |
| Public watchlists | 39s |
| Explore | 10s |
| Typesense calls | 5,900 |
| Upstash calls | 2,900 |
| Errors / timeouts | 0% / 0% |

The release target is at least a 50% reduction in visible Active CPU: no more
than 138.5 seconds in a comparable 12-hour window.

## Stable Server Action deployments

Production must define `NEXT_SERVER_ACTIONS_ENCRYPTION_KEY` as the canonical
base64 encoding of exactly 32 random bytes. Without a persistent key, a new
Next.js build can rotate the encrypted Server Action metadata. Clients or
automation retaining an older action ID then invoke Fluid only to receive a
`Failed to find Server Action` response.

Vercel Sensitive values are intentionally absent from the environment file
downloaded by a prebuilt CI deployment. Store the same value in both the Vercel
Production environment as Sensitive and the GitHub `Production` environment as
an Actions secret. The production workflow injects the GitHub secret only into
the validation and `vercel build --prod` steps. The check reports only validity
and never prints the value.

Generate a replacement only during an intentional incident rotation, and
update both secret stores together; an ordinary deployment must keep the
existing value. After a deliberate rotation, treat old-action traffic as
deployment skew, mitigate confirmed obsolete IDs before Functions, and restart
the 12-hour measurement window.

## Measurement protocol

A Vercel WAF publication resets an active measurement window, including a
publication that only consolidates equivalent deny branches or adds a
`log`-only rule. Firewall evaluation changes the production ingress boundary,
so a window spanning that publication is not comparable. Record the publish
timestamp and start a fresh 12-hour window after both the production deployment
`Ready` timestamp and the last WAF publication in scope. An unpublished draft
does not reset the window because it does not affect traffic.

The #8120 WAF publication completed between `2026-08-28T09:37:58Z` and
`2026-08-28T09:38:06Z`. Any measurement that was active then is invalid; its
replacement window must start no earlier than `2026-08-28T09:38:06Z`.

1. Record the production deployment SHA and its Vercel `Ready` timestamp.
2. Let at least 12 hours of production traffic accumulate after that timestamp.
   Never include traffic from the previous deployment.
3. In Vercel Observability, select Production and the exact absolute start/end
   timestamps. Do not use a moving “Past 12 hours” window while transcribing.
4. Record overall invocations, visible Active CPU, CPU P75, throttle P75,
   errors, timeouts, Typesense calls, and Upstash calls.
5. Record invocations and Active CPU for these exhaustive route groups:
   `companyOg`, `companyPages`, `publicWatchlists`, `explore`, and `other`.
   The sum must reconcile to overall visible CPU within 0.5 seconds.
6. Record company-OG R2 hits/misses and PPR shell hits/misses.
7. Confirm the window includes recognized bots and at least 20 distinct
   long-tail route keys. Synthetic repeated requests do not satisfy this gate.
8. Run the live checks below from production, then evaluate the JSON report.

## Production deployment identity

Do not start a measurement window until the deployment holding the production
aliases is the most recent web-relevant GitHub `main` SHA. Production can
legitimately lag crawler-, data-, documentation-, or ops-only commits. Vercel
completed two 2026-08-11 main deployments out of order and let an older parent
commit reacquire `jseek.co`, temporarily undoing the company-OG cutover.

The `Guard Vercel production order` workflow remains a secondary check for
successful Vercel Production deployment-status events. It compares such an
event with the live default-branch SHA, but owned CLI promotions do not
reliably emit a matching event and an exact-main comparison is intentionally
stricter than the web-relevant invariant. Treat a guard failure as a signal to
inspect the deployment and cumulative web delta, not as the primary promotion
assertion.

`apps/web/vercel.json` disables connected-Git production deployments for
`main`. The `Deploy web production` workflow owns that path instead: it runs on
every main push and deterministically classifies the entire delta from the
currently promoted Vercel SHA to live `main`. This cumulative comparison means
a newer queued ops-only push cannot hide an earlier web change. The workflow
stages an exact-SHA production artifact with no domains, smoke-tests it,
rechecks the live main SHA, and only then promotes it. After promotion, a
bounded API poll must prove that `jseek.co` resolves to both the staged
deployment ID and SHA. The workflow then refetches main and fails with both
SHAs, the deployment identity, and relevant paths if a web change landed during
promotion. If main moves earlier during the build, the staged artifact is not
promoted and the latest serialized run re-evaluates the cumulative delta. PR
branches remain on the Vercel Git integration. Web inputs are `apps/web/**`,
`packages/mcp-server/**`, the production deployment workflow and helpers, root
pnpm and Turbo configuration, and dependency patches. Crawler code, crawler
board configuration, documentation, and company CSV changes do not deploy the
web.

Company CSV changes are safe to exclude because the external OG prewarm job
publishes `og/company/<renderer>/_complete/current.json` only after the full
company/locale matrix and its immutable source-version marker exist. Company
metadata resolves that pointer through one shared five-minute cache entry and
keeps the build-time source marker as a rollout/outage fallback. This preserves
fresh OG URLs without giving every company ingestion commit a new Next.js build
ID and cold PPR cache.

The follow-up static trace audit also found that a root
`app/opengraph-image.tsx` caused the `next/og` Resvg/WASM/font stack to be
included in ordinary page Functions. The site-wide card is now pre-rendered to
the immutable `og/site/<version>.png` R2 key, and ordinary metadata references
that object directly. Company metadata already uses completed R2 namespaces;
legacy root and company OG URLs are deployment redirects. This removes both
renderers from normal page traces while preserving previously shared URLs.
The build-smoke bundle gate inspects every ordinary `page.js.nft.json` trace
and fails if the `@vercel/og`/Resvg payload leaks back into one.

Trusted company auto-merges use `GITHUB_TOKEN`, whose resulting main push does
not recursively start path-filtered workflows. Their post-merge helper
explicitly dispatches both production CSV sync and the company OG prewarm so a
new company cannot become visible before its off-platform cards are ready. The
helper waits for the prewarm to succeed before dispatching CSV sync at the
exact prewarmed main SHA.

In the 16-hour audit sample that exposed this coupling, 42 of 58 main commits
changed company OG data, five were otherwise unrelated to the web, ten changed
the web, and one changed a shared root input. The classifier would reduce that
sample from 58 production deployments to 11 (81%) without suppressing a real
web input.

## Live functionality checks

All checks are mandatory:

- Home page returns a successful localized document.
- Explore renders initial results and filter interaction still works.
- A known company page renders its company data and postings.
- That company page's `og:image` returns a valid PNG.
- A known public watchlist renders, while scanner-shaped paths return non-200.
- Sign-in loads and an existing authenticated session remains usable.

Set the corresponding `functionality` fields to `true` only after observing the
production behavior. CPU gains never override a failed functionality check.

## Report format

```json
{
  "schemaVersion": 1,
  "deployment": {
    "sha": "0123456789abcdef",
    "readyAt": "2026-08-11T13:00:00.000Z"
  },
  "window": {
    "start": "2026-08-11T13:05:00.000Z",
    "end": "2026-08-12T01:05:00.000Z"
  },
  "totals": {
    "invocations": 1000,
    "visibleActiveCpuSeconds": 130,
    "activeCpuP75Ms": 240,
    "cpuThrottleP75Pct": 5,
    "errorRatePct": 0,
    "timeoutRatePct": 0,
    "typesenseCalls": 2000,
    "upstashCalls": 1000
  },
  "routes": {
    "companyOg": { "invocations": 200, "activeCpuSeconds": 20 },
    "companyPages": { "invocations": 300, "activeCpuSeconds": 58 },
    "publicWatchlists": { "invocations": 150, "activeCpuSeconds": 24 },
    "explore": { "invocations": 100, "activeCpuSeconds": 9 },
    "other": { "invocations": 250, "activeCpuSeconds": 19 }
  },
  "cache": {
    "companyOgR2Hits": 195,
    "companyOgR2Misses": 5,
    "pprShellHits": 400,
    "pprShellMisses": 600
  },
  "traffic": {
    "recognizedBotRequests": 250,
    "longTailUniqueKeys": 300,
    "sourceIncludesAllTraffic": true
  },
  "functionality": {
    "home": true,
    "explore": true,
    "companyPage": true,
    "companyOg": true,
    "publicWatchlist": true,
    "authentication": true
  }
}
```

Run locally:

```bash
pnpm --filter @jobseek/web cpu:gate -- --input /absolute/path/report.json
```

Or dispatch the `Fluid CPU regression gate` workflow and paste the same JSON
into `metrics_json`. The workflow writes the complete gate table to its job
summary and fails when any budget or functionality check fails.

## Rollback and escalation

- Roll back immediately for production functionality loss, authentication
  failure, data corruption, new timeouts, or a sustained error rate above 0.5%.
- Investigate before declaring success when total CPU improves but any route
  exceeds its budget, external calls per invocation regress, or cache hit rates
  miss their thresholds.
- Continue remediation when visible Active CPU is above 138.5 seconds, even if
  P75 improves. Total CPU is the bottleneck and the release gate.
- Preserve the failed report in issue #6616 with the deployment SHA and exact
  timestamps so subsequent comparisons use the same traffic definition.
