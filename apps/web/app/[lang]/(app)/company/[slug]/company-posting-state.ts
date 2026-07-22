export type CompanyPostingListState =
  | "loading"
  | "unavailable"
  | "no-matches"
  | "no-active"
  | "results";

interface CompanyPostingListStateInput {
  isSearching: boolean;
  hasFilters: boolean;
  postingCount: number;
  isTruncated: boolean;
  activeCount: number;
}

export function getCompanyPostingListState({
  isSearching,
  hasFilters,
  postingCount,
  isTruncated,
  activeCount,
}: CompanyPostingListStateInput): CompanyPostingListState {
  if (isSearching) return "loading";
  if (postingCount > 0) return "results";
  if (!hasFilters && (isTruncated || activeCount > 0)) return "unavailable";
  if (hasFilters) return "no-matches";
  return "no-active";
}
