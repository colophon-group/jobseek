# Vercel Fluid CPU gate: FAIL (INCOMPLETE EVIDENCE)

Deployment: `c0066936b6a9f334c447ff5aa2ec1aff436c95ef`
Window: 2026-09-26T00:20:00.000Z → 2026-09-26T12:20:00.000Z (12.00h)

| Check | Actual | Budget | Result |
|---|---:|---:|:---:|
| Window starts after deployment ready | 2026-09-26T00:20:00.000Z | ≥ 2026-09-25T16:17:10.481Z | PASS |
| Clean window duration | 12h | ≥ 12h | PASS |
| Visible Active CPU | 247.5s | ≤ 138.5s | FAIL |
| Active CPU P75 | 264ms | ≤ 308ms | PASS |
| CPU throttle P75 | 8.5% | ≤ 7.6% | FAIL |
| Error rate | 0% | ≤ 0.5% | PASS |
| Timeout rate | 0% | ≤ 0.1% | PASS |
| Typesense calls / invocation | unknown | ≤ 2.5 | INCOMPLETE |
| Upstash calls / invocation | unknown | ≤ 1.5 | INCOMPLETE |
| companyOg Active CPU | 0s | ≤ 24s | PASS |
| companyPages Active CPU | 133s | ≤ 60s | FAIL |
| watchlistRoutes Active CPU | 9.7s | ≤ 25s | PASS |
| explore Active CPU | 7.7s | ≤ 10s | PASS |
| other Active CPU | 97.1s | ≤ 19.5s | FAIL |
| Route CPU reconciliation | 0s difference | ≤ 0.5s difference | PASS |
| PPR shell hit rate | 25.9% | ≥ 35% | FAIL |
| Recognized bot requests represented | 41 | ≥ 1 | PASS |
| Long-tail unique keys represented | 48 | ≥ 20 | PASS |
| Traffic source includes all requests | false (Retained dashboard request-log rows; paged and deduplicated; 11:25:05–12:19:54 UTC only; bot names are user-agent claims) | true | FAIL |
| Traffic source sampling | unknown | 100% | INCOMPLETE |
| Functionality: home | true | true | PASS |
| Functionality: explore | true | true | PASS |
| Functionality: companyPage | true | true | PASS |
| Functionality: companyOgDirect | true | true | PASS |
| Functionality: companyOgLegacyRedirect | true | true | PASS |
| Functionality: ownedWatchlist | true | true | PASS |
| Functionality: sharedWatchlist | true | true | PASS |
| Functionality: privateWatchlistAnonymousDenied | true | true | PASS |
| Functionality: privateWatchlistCrossOwnerDenied | true | true | PASS |
| Functionality: legacyWatchlistAnonymousDenied | true | true | PASS |
| Functionality: legacyWatchlistCrossOwnerDenied | true | true | PASS |
| Functionality: legacyWatchlistOwnerRedirects | true | true | PASS |
| Functionality: authentication | true | true | PASS |

Visible CPU reduction vs baseline: 10.6%
