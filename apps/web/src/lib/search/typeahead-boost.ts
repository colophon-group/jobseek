import { getSearchClient } from "./typesense-client";
import { buildFilterString } from "./typesense-filters";

export interface TypeaheadBoostFilters {
  companyId?: string;
  keywords?: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  languages?: string[];
}

/**
 * Re-rank typeahead candidates so that those with ≥1 matching posting
 * under `filters` rank above those with zero matches. Original order is
 * preserved within each group.
 *
 * Falls back to the input order on any Typesense error — typeahead must
 * never break.
 */
export async function boostByFilterMatches<T>(
  candidates: T[],
  facetField: string,
  idOf: (c: T) => number | string,
  filters: TypeaheadBoostFilters,
): Promise<T[]> {
  if (candidates.length === 0) return candidates;

  // No active filter dimensions → boost would be a no-op (every taxonomy
  // candidate already has has_active_postings:true). Skip the round-trip.
  const filterStr = buildFilterString(filters);
  if (!filterStr) return candidates;

  try {
    const client = getSearchClient();
    const ids = candidates.map(idOf);

    const filterParts = [
      "is_active:true",
      `${facetField}:[${ids.join(",")}]`,
      filterStr,
    ];

    const hasKeywords = filters.keywords && filters.keywords.length > 0;
    const q = hasKeywords ? filters.keywords!.join(" ") : "*";

    const result = await client
      .collections("job_posting")
      .documents()
      .search({
        q,
        query_by: "title",
        filter_by: filterParts.join(" && "),
        facet_by: facetField,
        facet_strategy: "exhaustive",
        max_facet_values: ids.length,
        per_page: 0,
      });

    const facet = result.facet_counts?.find(
      (f) => (f as { field_name: string }).field_name === facetField,
    ) as { counts: Array<{ value: string; count: number }> } | undefined;

    if (!facet) return candidates;

    const matched = new Set<string>();
    for (const fc of facet.counts) {
      if ((fc.count as number) > 0) matched.add(String(fc.value));
    }

    const withMatches: T[] = [];
    const withoutMatches: T[] = [];
    for (const c of candidates) {
      if (matched.has(String(idOf(c)))) withMatches.push(c);
      else withoutMatches.push(c);
    }
    return [...withMatches, ...withoutMatches];
  } catch {
    return candidates;
  }
}
