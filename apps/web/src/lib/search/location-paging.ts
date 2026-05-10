/**
 * Pure constants for the paged location modal fetch path (#2982).
 *
 * Lives outside `apps/web/src/lib/actions/locations.ts` because that file
 * has the `"use server"` directive — Next.js 16's server-actions runtime
 * rejects non-async exports from such files (a runtime `const` export
 * crashes `next build`). Type-only `export interface` declarations are
 * erased and remain safe; runtime values like this page-size constant
 * must live here.
 *
 * Imported by both the server action (`getGlobalLocationsPage` default
 * limit) and the client modal (`location-search-modal.tsx`).
 */

/**
 * Default page size for the location modal. 30 countries per page
 * renders fast (~50ms scripting on a mid-tier laptop) and matches
 * typical screen heights so the user reaches the bottom sentinel after
 * one or two scroll motions before the next page is needed.
 */
export const LOCATION_PAGE_SIZE = 30;
