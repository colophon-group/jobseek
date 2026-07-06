import type { HistogramFilters } from "@/lib/search/types";
import { canonicalizeFilters } from "@/lib/search/canonicalize-filters";

/**
 * Shape produced by {@link normalizeHistogramFilters}. Each field is
 * required (sentinel `""` / `[]` for missing inputs) so the `'use cache'`
 * boundary in `_fetchSalaryHistogram` / `_fetchExperienceHistogram` keys
 * off a stable object shape instead of a "field present" / "field
 * undefined" discriminator.
 */
export interface NormalizedHistogramFilters {
  companyId: string;
  keywords: string[];
  locationIds: number[];
  occupationIds: number[];
  seniorityIds: number[];
  technologyIds: number[];
  languages: string[];
}

/**
 * Normalize a {@link HistogramFilters} into a stable cache-key shape for
 * the salary / experience histogram `'use cache'` boundaries. Delegates
 * the per-field sort to {@link canonicalizeFilters} so every cache-key
 * site in the app stays on one canonicalization rule
 * (locale-independent `Intl.Collator("en", { sensitivity: "base" })` for
 * strings; numeric comparator for numeric IDs). See #3276 (follow-up to
 * #3221/#3187).
 *
 * Lives in a sibling module (not in `actions/search.ts`) because
 * `"use server"` modules may only export async functions — the sync
 * helper must be importable by both the action file and its unit tests.
 */
export function normalizeHistogramFilters(filters?: HistogramFilters): NormalizedHistogramFilters {
  const f = filters ?? {};
  return canonicalizeFilters({
    companyId: f.companyId ?? "",
    keywords: f.keywords ?? [],
    locationIds: f.locationIds ?? [],
    occupationIds: f.occupationIds ?? [],
    seniorityIds: f.seniorityIds ?? [],
    technologyIds: f.technologyIds ?? [],
    languages: f.languages ?? [],
  }) as NormalizedHistogramFilters;
}
