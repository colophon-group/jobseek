import type { ParsedSearchFilters } from "@/lib/services/search-input";
import { parseEmploymentTypeParam, parseWorkModeParam } from "@/lib/search/query-params";

export const EMPTY_PARSED_FILTERS: ParsedSearchFilters = {
  keywords: [],
  locations: [],
  occupations: [],
  seniorities: [],
  technologies: [],
  workMode: [],
  employmentTypes: [],
};

function splitExplicitSlugs(raw: string | undefined): string[] {
  if (!raw) return [];
  const seen = new Set<string>();
  const slugs: string[] = [];
  for (const value of raw.split(",")) {
    const slug = value.trim();
    const key = slug.toLowerCase();
    if (!slug || seen.has(key)) continue;
    seen.add(key);
    slugs.push(slug);
  }
  return slugs;
}

/**
 * Parse the parts of an Explore URL that do not require taxonomy I/O.
 *
 * Explicit taxonomy slugs deliberately remain unresolved. Consumers must
 * render an unavailable result instead of treating the empty ID arrays as an
 * unfiltered search. The helper is client-safe so a rejected Server Action can
 * still preserve the browser URL's filter state without restoring queryless
 * prerender data.
 */
export function parseOfflineSearchFilters(params: {
  q?: string;
  loc?: string;
  occ?: string;
  sen?: string;
  tech?: string;
  wm?: string;
  etype?: string;
}): ParsedSearchFilters {
  const query = params.q?.trim();
  const unresolvedExplicitSlugs = Object.fromEntries(
    (["loc", "occ", "sen", "tech"] as const)
      .map((kind) => [kind, splitExplicitSlugs(params[kind])] as const)
      .filter(([, slugs]) => slugs.length > 0),
  ) as NonNullable<ParsedSearchFilters["unresolvedExplicitSlugs"]>;

  return {
    ...EMPTY_PARSED_FILTERS,
    keywords: query ? [query] : [],
    workMode: parseWorkModeParam(params.wm),
    employmentTypes: parseEmploymentTypeParam(params.etype),
    ...(Object.keys(unresolvedExplicitSlugs).length > 0
      ? { unresolvedExplicitSlugs }
      : {}),
  };
}
