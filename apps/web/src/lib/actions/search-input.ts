"use server";

// Thin `"use server"` wrapper around the pure service tier from
// `@/lib/services/search-input`. The service module holds the
// implementation (no `"use server"` directive) and is the boundary used
// by public REST route handlers under `apps/web/app/api/v1/*` — see
// issue #3231.
//
// Why declared async wrappers instead of `export { foo } from "..."`?
// Next.js / Turbopack processes `"use server"` files by scanning for
// **declared** async functions and converting each into a server-action
// reference. Pure `export { foo } from "..."` re-exports yield zero
// exports from the wrapper's perspective and break production builds
// at every client component that imports from
// `@/lib/actions/search-input` (PR #3335 build regression). So we
// declare each wrapper explicitly and delegate to the service
// implementation.
//
// Existing UI callers (search-bar, search-page, company-page-data,
// explore-page-data, etc.) continue importing from this module so their
// invocation remains a server-action call. The public REST routes go
// straight to `@/lib/services/search-input` so they don't pay the
// server-action machinery cost.

import * as service from "@/lib/services/search-input";

export async function parseSearchFilters(
  ...args: Parameters<typeof service.parseSearchFilters>
): ReturnType<typeof service.parseSearchFilters> {
  return service.parseSearchFilters(...args);
}

// Type re-exports below preserve the existing
// `import type { ParsedSearchFilters } from "@/lib/actions/search-input"`
// callers. Type-only re-exports are erased at compile-time and don't go
// through the server-action transform, so plain `export type { ... }
// from "..."` is safe here.
export type {
  ParsedSearchFilters,
} from "@/lib/services/search-input";
