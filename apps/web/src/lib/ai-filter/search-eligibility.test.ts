import { describe, expect, it } from "vitest";

import {
  AI_FILTER_MAX_SEARCH_CANDIDATES,
  getAiSearchEligibility,
} from "./search-eligibility";

describe("getAiSearchEligibility", () => {
  it("requires a subscription before revealing execution eligibility", () => {
    expect(getAiSearchEligibility({
      isSubscribed: false,
      hasSearchFilters: true,
      candidateCount: 4,
    }).status).toBe("subscription_required");
  });

  it("requires the ordinary search to have filters", () => {
    expect(getAiSearchEligibility({
      isSubscribed: true,
      hasSearchFilters: false,
      candidateCount: 2,
    }).status).toBe("add_filters");
  });

  it("fails closed when an exact posting count is unavailable", () => {
    expect(getAiSearchEligibility({
      isSubscribed: true,
      hasSearchFilters: true,
      candidateCount: undefined,
    }).status).toBe("count_unavailable");
  });

  it("rejects a search above the Jev candidate ceiling", () => {
    expect(getAiSearchEligibility({
      isSubscribed: true,
      hasSearchFilters: true,
      candidateCount: AI_FILTER_MAX_SEARCH_CANDIDATES + 1,
    }).status).toBe("too_broad");
  });

  it("accepts the exact ceiling and distinguishes zero matches", () => {
    expect(getAiSearchEligibility({
      isSubscribed: true,
      hasSearchFilters: true,
      candidateCount: AI_FILTER_MAX_SEARCH_CANDIDATES,
    }).status).toBe("eligible");
    expect(getAiSearchEligibility({
      isSubscribed: true,
      hasSearchFilters: true,
      candidateCount: 0,
    }).status).toBe("no_matches");
  });
});
