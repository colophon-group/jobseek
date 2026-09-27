# Vercel Fluid CPU gate: FAIL (INCOMPLETE EVIDENCE)

Deployment: `e65fb972e4fbb349d10ed66dc9418b1d271f52ca`
Window: 2026-09-26T23:10:00.000Z → 2026-09-27T11:10:00.000Z (12.00h)

| Check | Actual | Budget | Result |
|---|---:|---:|:---:|
| Window starts after deployment ready | 2026-09-26T23:10:00.000Z | ≥ 2026-09-26T13:06:20.750Z | PASS |
| Clean window duration | 12h | ≥ 12h | PASS |
| Visible Active CPU | 161s | ≤ 138.5s | FAIL |
| Active CPU P75 | 310ms | ≤ 308ms | FAIL |
| CPU throttle P75 | 10.1% | ≤ 7.6% | FAIL |
| Error rate | 0% | ≤ 0.5% | PASS |
| Timeout rate | 0% | ≤ 0.1% | PASS |
| Typesense calls / invocation | unknown | ≤ 2.5 | INCOMPLETE |
| Upstash calls / invocation | unknown | ≤ 1.5 | INCOMPLETE |
| companyOg Active CPU | 0s | ≤ 24s | PASS |
| companyPages Active CPU | 106s | ≤ 60s | FAIL |
| watchlistRoutes Active CPU | 2.5s | ≤ 25s | PASS |
| explore Active CPU | 11s | ≤ 10s | FAIL |
| other Active CPU | 41.5s | ≤ 19.5s | FAIL |
| Route CPU reconciliation | 0s difference | ≤ 0.5s difference | PASS |
| PPR shell hit rate | 55.2% | ≥ 35% | PASS |
| Recognized bot requests represented | 1 | ≥ 1 | PASS |
| Long-tail unique keys represented | 17 | ≥ 20 | FAIL |
| Traffic source includes all requests | false (Retained dashboard request-log rows; deduplicated; 10:19:50–11:08:59 UTC only; bot names are user-agent claims) | true | FAIL |
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

Visible CPU reduction vs baseline: 41.9%
