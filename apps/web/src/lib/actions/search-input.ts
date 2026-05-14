"use server";

// Thin `"use server"` wrapper re-exporting the pure service tier from
// `@/lib/services/search-input`. The service module holds the
// implementation (no `"use server"` directive) and is the boundary used
// by public REST route handlers under `apps/web/app/api/v1/*` — see
// issue #3231.
//
// Existing UI callers (search-bar, search-page, company-page-data,
// explore-data, etc.) continue importing from this module so their
// invocation remains a server-action call. The public REST routes go
// straight to `@/lib/services/search-input` so they don't pay the
// server-action machinery cost.
//
// Type re-exports below preserve the existing `import type {
// ParsedSearchFilters } from "@/lib/actions/search-input"` callers.

export { parseSearchFilters } from "@/lib/services/search-input";

export type {
  ParsedSearchLocation,
  ParsedSearchFilters,
} from "@/lib/services/search-input";
