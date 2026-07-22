import { describe, expect, it } from "vitest";
import { getCompanyPostingListState } from "../company-posting-state";

describe("getCompanyPostingListState", () => {
  it("treats historical-only companies as a normal empty state", () => {
    expect(
      getCompanyPostingListState({
        isSearching: false,
        hasFilters: false,
        postingCount: 0,
        isTruncated: false,
        activeCount: 0,
      }),
    ).toBe("no-active");
  });

  it("reports an unavailable search when active results disappear", () => {
    expect(
      getCompanyPostingListState({
        isSearching: false,
        hasFilters: false,
        postingCount: 0,
        isTruncated: false,
        activeCount: 3,
      }),
    ).toBe("unavailable");
  });

  it("distinguishes filtered empty results from an empty company", () => {
    expect(
      getCompanyPostingListState({
        isSearching: false,
        hasFilters: true,
        postingCount: 0,
        isTruncated: false,
        activeCount: 0,
      }),
    ).toBe("no-matches");
  });
});
