import type { LocationSuggestion } from "@/lib/actions/locations";

/**
 * Domain shape for a user-selected location across search/watchlist
 * surfaces. Lives in `lib/` (not `components/`) so server actions, API
 * route handlers, and page data loaders can consume it without crossing
 * a layer boundary into a `"use client"` UI component. See issue #3220.
 *
 * Fields:
 * - `id` / `slug` — primary identifiers from the location taxonomy.
 * - `name` — locale-resolved display label.
 * - `type` — the geographic level (city/region/country/macro), reused
 *   from `LocationSuggestion["type"]` to stay in sync with the
 *   typeahead source.
 * - `parentName` — optional parent label used when rendering pills
 *   ("Geneva, Switzerland") and for disambiguation.
 */
export interface SelectedLocation {
  id: number;
  slug: string;
  name: string;
  type: LocationSuggestion["type"];
  parentName: string | null;
}

