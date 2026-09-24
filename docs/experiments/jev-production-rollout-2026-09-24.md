# Jev search routing production rollout

The implementation and frozen evaluation are in PR #9945 and issue #9941.
Production rollout uses the existing search flag plus a stable browser cohort.
Vercel Rolling Releases are unavailable on this project's current plan, so
the cohort limits calls to Jev without changing the site's deployment pipeline.

## Configuration

- `SEARCH_QUERY_JEV_ENABLED=true` enables the server route.
- `NEXT_PUBLIC_SEARCH_QUERY_JEV_ENABLED=true` enables the browser integration.
- `NEXT_PUBLIC_SEARCH_QUERY_JEV_ROLLOUT_PERCENT=25` enables Jev for 25% of
  browsers. The value is an integer from 0 to 100; invalid values fail closed.
  Each browser stores a random bucket under `search-query-jev-rollout-v1` so
  page loads and shared search surfaces remain in the same cohort.
- A change to either public setting needs a fresh production build. A rapid
  rollback can promote the previous verified deployment. Do not assume a
  Vercel environment edit changes a deployment already serving traffic.

Users outside the cohort keep the existing Typesense suggestions and Enter
parser. Inside it, term suggestions still arrive first; Jev runs after 900 ms
idle or immediately on Enter for eligible text. The client waits at most 1.8 s
before the deterministic fallback. Existing explicit dropdown choices win.

## Release sequence

1. Set the three production settings before the build. Confirm the Jev token
   and Typesense search credentials exist without printing their values.
2. Merge the CI-green PR. Let `Deploy web production` build and smoke-test an
   exact `main` SHA, then promote and verify its deployment ID and SHA.
3. In the 25% cohort, check a fast Enter, a long-idle proposal, an ambiguous
   place correction, and a typo on the production site. Also check the
   homepage, company page, public watchlist, sign-in, and mobile search.
4. Compare Jev route invocations, 5xx rate, active CPU, peak memory, and
   duration with the rest of production via Vercel Metrics. Sample the browser
   Enter-to-results and idle-proposal times. Check provider usage and the
   number of Typesense normalization calls. Keep query text out of telemetry.
5. Widen to 100% only after the canary is healthy; rebuild from exact `main`
   with the new public percentage, then repeat smoke checks. Keep the old
   deployment available for rollback.
6. Capture a clean, fully post-deployment 12-hour Fluid CPU window following
   `docs/18-vercel-fluid-cpu.md`. Do not represent a short synthetic probe as
   the all-traffic CPU gate.

The new route's Vercel metrics measure server cost and errors. The current
implementation does not emit aggregate correction events or provider token
usage. Human review of the frozen cases and synthetic browser measurements
can identify errors during the canary, but cannot establish a production
correction rate; add privacy-preserving counters before treating that metric
as a widening criterion.
