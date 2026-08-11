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

## Measurement protocol

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
