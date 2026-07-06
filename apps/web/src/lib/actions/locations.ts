"use server";

// Thin `"use server"` wrapper around the plain location service tier from
// `@/lib/services/locations`. The service module holds the implementation
// (no `"use server"` directive) and is the boundary used by public REST
// route handlers under `apps/web/app/api/v1/*` — see issues #3231 / #3330.
//
// Keep these as declared async wrappers, not `export { foo } from "..."`.
// Next.js / Turbopack scans `"use server"` files for declared async
// functions and turns them into server-action references. Plain re-exports
// can disappear from the server-action transform and break client imports.

import * as service from "@/lib/services/locations";

export async function suggestLocations(
  ...args: Parameters<typeof service.suggestLocations>
): ReturnType<typeof service.suggestLocations> {
  return service.suggestLocations(...args);
}

export async function expandLocationIds(
  ...args: Parameters<typeof service.expandLocationIds>
): ReturnType<typeof service.expandLocationIds> {
  return service.expandLocationIds(...args);
}

export async function expandLocationIdsBatch(
  ...args: Parameters<typeof service.expandLocationIdsBatch>
): ReturnType<typeof service.expandLocationIdsBatch> {
  return service.expandLocationIdsBatch(...args);
}

export async function resolveLocationSlugs(
  ...args: Parameters<typeof service.resolveLocationSlugs>
): ReturnType<typeof service.resolveLocationSlugs> {
  return service.resolveLocationSlugs(...args);
}

export async function getGlobalLocationsGrouped(
  ...args: Parameters<typeof service.getGlobalLocationsGrouped>
): ReturnType<typeof service.getGlobalLocationsGrouped> {
  return service.getGlobalLocationsGrouped(...args);
}

export async function getGlobalLocationsPage(
  ...args: Parameters<typeof service.getGlobalLocationsPage>
): ReturnType<typeof service.getGlobalLocationsPage> {
  return service.getGlobalLocationsPage(...args);
}

export async function searchGlobalLocations(
  ...args: Parameters<typeof service.searchGlobalLocations>
): ReturnType<typeof service.searchGlobalLocations> {
  return service.searchGlobalLocations(...args);
}

export type {
  LocationSuggestion,
  ResolvedLocation,
  GlobalLocationGroup,
  GlobalMacroRegion,
  GlobalLocationsResponse,
  GlobalLocationsPage,
  GlobalLocationSearchHit,
} from "@/lib/services/locations";
