# Watchlist Detail (`/:lang/watchlists/:watchlistId`)

**Route group:** `(app)` | **Rendering:** cached app shell plus a Suspense-bound,
request-specific watchlist loader

The UUID route is the single detail surface for owned, unlisted shared, and
browser-backed session watchlists. The former
`/:lang/:userSlug/:watchlistSlug` route is a private compatibility handler: it
redirects an authenticated owner to the UUID route and otherwise returns the
same private 404 boundary.

## Server-side data fetching

The cached app layout resolves only viewer-independent currency rates. Inside
the route Suspense boundary, `OwnedWatchlistLoader`:

1. resolves the session and exact UUID;
2. tries the owned row, then an explicitly shared row;
3. resolves language scope, clone limit, and optional narrowed state in
   parallel;
4. builds the initial broad page and initial persisted accepted page in
   parallel; and
5. renders one `WatchlistPageData` contract through `WatchlistViewPage`.

If no database row exists, `SessionWatchlistLoader` validates an anonymous
draft from `sessionStorage`, materializes companies and filters through one
bounded server action, then renders the same detail component with a local
persistence adapter.

Search reads use Typesense; watchlist metadata, ownership, sharing,
subscriptions, and narrowed decisions use web Postgres. A six-second Typesense
budget aborts an unavailable initial search and returns the editable shell with
an explicit degraded result state instead of failing the whole route.

## Client requests

| Request | Trigger |
|---|---|
| Browser-direct Typesense or bounded server fallback | Broad infinite scroll and filter edits |
| `GET .../ai-filter/decisions` | Open/page persisted narrowed results; owner response also includes current state |
| `POST .../ai-filter/reconcile` | Entitled owner opens or scrolls beyond evaluated demand |
| `GET .../ai-filter` | Bounded owner progress polling while durable work is active |
| Posting-detail and saved-job actions | Open or save a job |
| Watchlist mutation actions | Persist owner edits, sharing, alerts, or delete |
| Session-watchlist action | Validate/materialize an anonymous draft or its overview previews |

Shared viewers never start Jev work. Anonymous viewers are capped at 20 broad
or narrowed jobs and receive the same login prompt as other truncated search
surfaces. Signed-in non-owners may page the complete shared feed. Shared
language scope always comes from the owner and cannot be changed by the viewer.

## Fluid Compute

- No session read occurs in the shared app layout.
- Independent database, page, state, and count reads are parallelized.
- Accepted pages hydrate only requested IDs through one Typesense query; the
  total is a Postgres aggregate, not an all-result hydration fan-out.
- A cached accepted page is consumed before reconcile is considered.
- Owner decisions and state share one response; foreground polling is bounded
  at 1.5 seconds and safe GETs abort on scope change or unmount.
- Durable Jev work runs in Workflow in at most ten 50-candidate steps per
  500-candidate demand, so a route invocation does not wait for provider work.

See `docs/24-ai-filter.md` for the execution, cache, budget, and activation
contract.
