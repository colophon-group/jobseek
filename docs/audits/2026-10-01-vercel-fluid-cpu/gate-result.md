# Vercel Fluid CPU gate: FAIL (INCOMPLETE EVIDENCE)

Deployment: `dae3f51fa96804c0a8cca0c4e1a3f52bc289f386`
Window: 2026-09-30T20:00:00.000Z → 2026-10-01T08:00:00.000Z (12.00h)

| Check | Actual | Budget | Result |
|---|---:|---:|:---:|
| Window starts after deployment ready | 2026-09-30T20:00:00.000Z | ≥ 2026-09-30T18:21:50.332Z | PASS |
| Clean window duration | 12h | ≥ 12h | PASS |
| Visible Active CPU | 171.5s | ≤ 138.5s | FAIL |
| Active CPU P75 | 273ms | ≤ 308ms | PASS |
| CPU throttle P75 | 13.7% | ≤ 7.6% | FAIL |
| Error rate | 0.1% | ≤ 0.5% | PASS |
| Timeout rate | 0% | ≤ 0.1% | PASS |
| Typesense calls / invocation | unknown | ≤ 2.5 | INCOMPLETE |
| Upstash calls / invocation | unknown | ≤ 1.5 | INCOMPLETE |
| companyOg Active CPU | 0s | ≤ 24s | PASS |
| companyPages Active CPU | 86s | ≤ 60s | FAIL |
| watchlistRoutes Active CPU | 11.4s | ≤ 25s | PASS |
| explore Active CPU | 10s | ≤ 10s | PASS |
| other Active CPU | 64.1s | ≤ 19.5s | FAIL |
| Route CPU reconciliation | 0s difference | ≤ 0.5s difference | PASS |
| PPR shell hit rate | 28.3% | ≥ 35% | FAIL |
| Recognized bot requests represented | 11 | ≥ 1 | PASS |
| Long-tail unique keys represented | 13 | ≥ 20 | FAIL |
| Traffic source includes all requests | false (Authenticated dashboard request-logs endpoint, observed working; no documented all-traffic or sampling contract established) | true | FAIL |
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

Visible CPU reduction vs baseline: 38.1%
