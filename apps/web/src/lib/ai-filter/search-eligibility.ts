/**
 * Product boundary for the interactive search AI filter.
 *
 * Jev is a second-stage filter, never a replacement for the existing search
 * controls. The exact posting count comes from Typesense's pre-grouping
 * `found_docs` value (or the ungrouped `found` value), not the company count
 * rendered by Explore.
 */
export const AI_FILTER_MAX_SEARCH_CANDIDATES = 10_000;

export type AiSearchEligibility = Readonly<{
  status:
    | "subscription_required"
    | "add_filters"
    | "count_unavailable"
    | "too_broad"
    | "no_matches"
    | "eligible";
  candidateCount: number | null;
  maxCandidates: typeof AI_FILTER_MAX_SEARCH_CANDIDATES;
}>;

export function getAiSearchEligibility(input: {
  isSubscribed: boolean;
  hasSearchFilters: boolean;
  candidateCount: number | null | undefined;
}): AiSearchEligibility {
  const result = (status: AiSearchEligibility["status"]): AiSearchEligibility =>
    Object.freeze({
      status,
      candidateCount: input.candidateCount ?? null,
      maxCandidates: AI_FILTER_MAX_SEARCH_CANDIDATES,
    });

  if (!input.isSubscribed) return result("subscription_required");
  if (!input.hasSearchFilters) return result("add_filters");
  if (
    input.candidateCount == null ||
    !Number.isSafeInteger(input.candidateCount) ||
    input.candidateCount < 0
  ) {
    return result("count_unavailable");
  }
  if (input.candidateCount === 0) return result("no_matches");
  if (input.candidateCount > AI_FILTER_MAX_SEARCH_CANDIDATES) {
    return result("too_broad");
  }
  return result("eligible");
}
