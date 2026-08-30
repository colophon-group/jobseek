# Explore Page (`/:lang/explore`)

**Route group:** `(app)` | **Rendering:** cached, query-agnostic anonymous shell

## Initial document contract

`page.tsx` calls `fetchExplorePageDefaults({ locale })` inside a one-day Cache
Component and passes the serializable result to `ExploreContent`. Raw HTML for
every supported locale must contain:

- the localized page H1;
- at least one `data-search-result-company` company link;
- no `Loading results` fallback in place of the result list.

When server-side Typesense configuration is present, those nodes are live job
result cards and a legitimate empty response remains empty. When the build is
intentionally secretless, the page instead uses ten real company identities
from the repository-owned blog mention snapshot. That offline surface is
explicitly labelled as temporarily degraded and offers refresh guidance. It
contains profile links only: no fabricated posting IDs or titles, no activity
counts, and no save/star actions. Missing configuration also short-circuits the
search/taxonomy calls, so deterministic builds do not attempt external reads.
A configured production outage is kept distinct and continues to use the
normal localized unavailable/refresh state in both the raw and hydrated trees;
repository data must not mask a live provider failure or a legitimate
zero-result search.

Filter-bearing document requests are normalized by `proxy.ts` to the same
queryless cached shell. The address bar keeps the original query. After
hydration, `ExploreContent` reads `window.location.search`, requests the
personalized/filtered result directly through the scoped browser Typesense key,
and replaces the anonymous defaults. Explicit taxonomy slugs resolve in the
same browser `multi_search`; semantic free text alone uses the narrow canonical
parser action because it needs request geolocation.

Do not add request-bound `searchParams`, `headers()`, or `cookies()` reads to
the cached page. Do not put `useSearchParams()` back in the result-owning
client subtree: with Cache Components it suspends that subtree and previously
replaced all meaningful raw result markup with `loading.tsx` (#2640).

## Navigation-time requests

For an anonymous, unfiltered navigation:

| Request | Boundary | Compute behavior |
|---------|----------|------------------|
| `/:lang/explore` document | CDN / Cache Component | Anonymous defaults are cached by locale |
| Currency-rate table | Embedded in app layout | Hourly server cache; no browser request |
| App bootstrap | Client provider | Skipped when `logged_in` hint is absent |
| Default inventory refresh | Browser-direct Typesense | No Vercel Server Action fallback |
| JS, CSS, fonts, logos | Static/image CDN | Normal asset caching applies |
| Analytics | Vercel telemetry | Post-load, not application data |

The whole page therefore emits **zero Next Server Action POSTs** on an
anonymous unfiltered mount. `script/smoke-built-app.ts` verifies this against
the production build.

Filtered URLs and job-language-hinted viewers initialize through browser-direct
Typesense after hydration. Signed-in preferences reuse `fetchAppBootstrap()`
from the shared app layout; Explore does not issue a second page-data action.
The anonymous result shell remains visible in raw HTML while JavaScript loads;
the client shows the busy skeleton only while replacing stale defaults with
filtered data. A failed scoped read keeps the URL-derived filters and an
explicit unavailable state—it never restores the broader queryless snapshot.

## Interactive requests

| Operation | Preferred path | Server Action fallback / mutation |
|-----------|----------------|-----------------------------------|
| Default inventory refresh | Browser-direct Typesense | None |
| Filter-bearing initial mount | Browser-direct Typesense; narrow semantic parser for free text | None for result data |
| Search/filter change | Browser-direct Typesense runner | Search Server Action when direct access is unavailable or request exceeds the public bound |
| Load more companies/postings | Browser-direct where supported | Bounded read action |
| Posting detail | Cached posting-detail read | `getPostingDetail()` |
| Save/star/watchlist changes | N/A | Authenticated mutation Server Action |

All links keep `prefetch={false}` so cards and navigation controls do not spend
route requests before the user expresses intent.

## Currency and personalization

`app/[lang]/(app)/layout.tsx` resolves `getCurrencyRates()` from the pure server
service. That function is viewer-independent and has an hourly `use cache`
profile. The layout passes the serialized table through `AppBootstrapProvider`
to `SalaryDisplayProvider`, so salary controls have conversion data on their
first client render without a mount-time Server Action.
The service's database-error path uses the full approximate migration seed,
not a EUR-only list, so a cached cold/error shell keeps supported currencies
functional while a later uncached server attempt can recover current rates.

Signed-in preferences still arrive from `fetchAppBootstrap()` and override
anonymous/local display-currency and salary-period state. Anonymous local
preferences continue to rehydrate from local storage. Moving the rate table
does not change either preference authority.

## Acceptance checks

Run these after changing the route, provider tree, or URL synchronization:

```bash
pnpm test
pnpm build
pnpm smoke
```

The built-app smoke covers all four localized raw documents, a filter-bearing
document, and the zero-navigation-Server-Action contract. The unit suite pins
browser URL observation and ensures the salary provider has no Server Action
import/effect.
