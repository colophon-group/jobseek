/** Fetch one extra raw group so pagination does not depend on estimated totals. */
export function groupedPageRequest(offset: number, limit: number) {
  if (!Number.isSafeInteger(offset) || offset < 0 ||
      !Number.isSafeInteger(limit) || limit < 1 || limit > 250) {
    throw new Error("Invalid grouped search page");
  }
  return { offset, limit: Math.min(limit + 1, 250) };
}

export function readGroupedPage<T>(
  result: {
    found: number;
    found_docs?: number;
    grouped_hits?: T[];
    search_cutoff?: boolean;
  },
  offset: number,
  limit: number,
) {
  if (!Number.isSafeInteger(result.found) || result.found < 0 || result.search_cutoff) {
    throw new Error("Invalid or incomplete grouped search response");
  }
  const rawGroups = result.grouped_hits ?? [];
  // 27.1 found is exact; v29+ estimates groups. Neither controls exhaustion.
  // At the engine's 250-group page cap, a full page needs one further probe.
  const hasMore = rawGroups.length > limit || (limit === 250 && rawGroups.length === 250);
  return {
    groups: rawGroups.slice(0, limit),
    totalCompanies: result.found,
    nextOffset: hasMore ? offset + limit : null,
    ...(typeof result.found_docs === "number" && Number.isSafeInteger(result.found_docs) && result.found_docs >= 0
      ? { totalPostings: result.found_docs }
      : {}),
  };
}
