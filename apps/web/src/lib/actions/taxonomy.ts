"use server";

// Thin `"use server"` wrapper around the pure service tier from
// `@/lib/services/taxonomy`. The service module holds the implementation
// (no `"use server"` directive) and is the boundary used by public REST
// route handlers under `apps/web/app/api/v1/*` — see issues #3231 / #3329.
//
// Why declared async wrappers instead of `export { foo } from "..."`?
// Next.js / Turbopack processes `"use server"` files by scanning for
// **declared** async functions and converting each into a server-action
// reference. Pure `export { foo } from "..."` re-exports yield zero
// exports from the wrapper's perspective and break production builds at
// every client component that imports from `@/lib/actions/taxonomy`
// (PR #3335 build regression). So we declare each wrapper explicitly
// and delegate to the service implementation.
//
// Type re-exports remain plain `export type { ... } from "..."` because
// they are erased at compile-time and don't go through the
// server-action transform.
//
// Why a wrapper at all (vs. having UI callers import the service
// directly)? Existing client callers (search-bar typeaheads, modal
// counts, watchlist resolve flows) still invoke these as server
// actions, and a single import path in the UI avoids a sprawling
// migration. The REST route handlers go straight to
// `@/lib/services/taxonomy` so they don't pay the server-action
// machinery cost (per-call RPC URL, serialization boundary, security
// IDs) for what is already a public REST surface.

import * as service from "@/lib/services/taxonomy";

export async function suggestOccupations(
  ...args: Parameters<typeof service.suggestOccupations>
): ReturnType<typeof service.suggestOccupations> {
  return service.suggestOccupations(...args);
}

export async function suggestSeniorities(
  ...args: Parameters<typeof service.suggestSeniorities>
): ReturnType<typeof service.suggestSeniorities> {
  return service.suggestSeniorities(...args);
}

export async function suggestTechnologies(
  ...args: Parameters<typeof service.suggestTechnologies>
): ReturnType<typeof service.suggestTechnologies> {
  return service.suggestTechnologies(...args);
}

export async function resolveOccupationSlugs(
  ...args: Parameters<typeof service.resolveOccupationSlugs>
): ReturnType<typeof service.resolveOccupationSlugs> {
  return service.resolveOccupationSlugs(...args);
}

export async function resolveSenioritySlugs(
  ...args: Parameters<typeof service.resolveSenioritySlugs>
): ReturnType<typeof service.resolveSenioritySlugs> {
  return service.resolveSenioritySlugs(...args);
}

export async function expandOccupationIds(
  ...args: Parameters<typeof service.expandOccupationIds>
): ReturnType<typeof service.expandOccupationIds> {
  return service.expandOccupationIds(...args);
}

export async function expandOccupationIdsBatch(
  ...args: Parameters<typeof service.expandOccupationIdsBatch>
): ReturnType<typeof service.expandOccupationIdsBatch> {
  return service.expandOccupationIdsBatch(...args);
}

export async function resolveTechnologySlugs(
  ...args: Parameters<typeof service.resolveTechnologySlugs>
): ReturnType<typeof service.resolveTechnologySlugs> {
  return service.resolveTechnologySlugs(...args);
}

export async function getAllOccupationsGrouped(
  ...args: Parameters<typeof service.getAllOccupationsGrouped>
): ReturnType<typeof service.getAllOccupationsGrouped> {
  return service.getAllOccupationsGrouped(...args);
}

export async function getAllSeniorities(
  ...args: Parameters<typeof service.getAllSeniorities>
): ReturnType<typeof service.getAllSeniorities> {
  return service.getAllSeniorities(...args);
}

export async function getAllTechnologiesGrouped(
  ...args: Parameters<typeof service.getAllTechnologiesGrouped>
): ReturnType<typeof service.getAllTechnologiesGrouped> {
  return service.getAllTechnologiesGrouped(...args);
}

export async function getEmploymentTypeCounts(
  ...args: Parameters<typeof service.getEmploymentTypeCounts>
): ReturnType<typeof service.getEmploymentTypeCounts> {
  return service.getEmploymentTypeCounts(...args);
}

export async function getWorkModeCounts(
  ...args: Parameters<typeof service.getWorkModeCounts>
): ReturnType<typeof service.getWorkModeCounts> {
  return service.getWorkModeCounts(...args);
}

// Type re-exports below preserve the existing
// `import type { TaxonomySuggestion } from "@/lib/actions/taxonomy"`
// callers. Type-only re-exports are erased at compile-time and don't go
// through the server-action transform, so plain `export type { ... }
// from "..."` is safe here.
export type {
  TaxonomySuggestion,
  OccupationItem,
  OccupationSubGroup,
  OccupationGroup,
  SeniorityOption,
  TechnologyItem,
  TechnologyGroup,
} from "@/lib/services/taxonomy";
