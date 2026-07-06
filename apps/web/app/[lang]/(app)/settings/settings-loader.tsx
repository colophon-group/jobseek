import { GeneralSettings } from "@/components/settings/GeneralSettings";
import {
  getPreferences,
  getAvailableJobLanguages,
  getViewerJobLanguages,
} from "@/lib/actions/preferences";
import { getCurrencyRates } from "@/lib/actions/search";
import { getSession } from "@/lib/sessionCache";

/**
 * Server-side data fetcher for the /settings (general) page.
 *
 * Runs all four data dependencies in parallel inside the React server
 * tree so the client receives the form fully hydrated — no
 * post-mount `useEffect` waterfall, no spinner. This is the second
 * half of the #2918 fix: PR #2921 cut `getAvailableJobLanguages` from
 * 4s to ~700ms by switching it to a Typesense facet, but the four
 * server actions still ran *sequentially after hydration* in the
 * client `useEffect`, so the slowest call (Typesense round-trip)
 * gated "fully settled" on every cold load. Moving the awaits into
 * the server tree:
 *
 * - merges the four POST round-trips into the initial document GET
 * - lets the cacheable calls (`getAvailableJobLanguages`,
 *   `getCurrencyRates`, both `'use cache'`) be served from the
 *   per-region in-memory cache without ever crossing the wire
 * - keeps the session-scoped reads (`getPreferences`,
 *   `getViewerJobLanguages`) on their existing dynamic path; they
 *   read `headers()` via Better Auth so the parent `<Suspense>` in
 *   `page.tsx` is what makes them PPR-compatible.
 *
 * The leading `getSession()` await is the load-bearing dynamic gate:
 * it reads `headers()` via Better Auth, so under cacheComponents the
 * build-time prerender bails *before* the parallel Typesense /
 * Postgres reads ever fire. Without this gate, the Typesense calls
 * (inside `'use cache'`) populate during the prerender's parallel
 * sweep across 974 pages and contend with the rest of the build for
 * Typesense's request budget — producing build-time HTTP 429s. With
 * the gate first, the function only ever executes at request time,
 * where the per-region `'use cache'` layer absorbs the read cost.
 *
 * The remaining three awaits stay in a single `Promise.all` so the
 * slowest read is the bound rather than the sum (~932ms cold vs
 * ~1.2s sequential under cacheComponents per-boundary clean-snapshot
 * semantics).
 */
export async function SettingsLoader({ locale }: { locale: string }) {
  // Dynamic gate — see comment above. Discarded result; the same
  // session is re-resolved (per-request memoised) inside
  // `getPreferences` and `getViewerJobLanguages` below.
  await getSession();

  const [prefs, jobLanguages, availableLanguages, currencyRates] = await Promise.all([
    getPreferences(),
    getViewerJobLanguages(),
    getAvailableJobLanguages(),
    getCurrencyRates(),
  ]);

  return (
    <GeneralSettings
      savedJobLanguages={jobLanguages}
      savedDisplayCurrency={prefs?.displayCurrency ?? "EUR"}
      savedSalaryPeriod={prefs?.salaryPeriod ?? null}
      availableCurrencies={currencyRates.map((r) => r.currency)}
      availableLanguages={availableLanguages}
      locale={locale}
    />
  );
}
