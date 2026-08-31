import { describe, expect, expectTypeOf, it } from "vitest";
import type { WatchlistCandidateFilters } from "@/lib/watchlist-matcher-contract";

import {
  buildWatchlistCandidateSearchParams,
  buildWatchlistCandidateWindowFilter,
  WATCHLIST_CANDIDATE_WINDOW_BOUNDARY,
} from "../watchlist-candidate-query";

const companyId = "11111111-1111-1111-1111-111111111111";

describe("canonical watchlist candidate query", () => {
  it("keeps candidate company membership readonly at the compiler boundary", () => {
    expectTypeOf<WatchlistCandidateFilters["companyIds"]>().toEqualTypeOf<
      readonly string[]
    >();
  });

  it("preserves every interactive structured-filter dimension", () => {
    const search = buildWatchlistCandidateSearchParams({
      filters: {
        companyIds: [companyId],
        keywords: ["staff", "engineer"],
        locationIds: [1],
        occupationIds: [2],
        seniorityIds: [3],
        technologyIds: [4],
        workMode: ["remote"],
        employmentType: ["full_time"],
        salaryMin: 100_000,
        salaryMax: 180_000,
        experienceMin: 3,
        experienceMax: 7,
        languages: ["de", "en"],
      },
      offset: 20,
      limit: 20,
    });

    expect(search).toMatchObject({
      q: "staff engineer",
      query_by: "title",
      sort_by: "_text_match:desc,first_seen_at:desc",
      per_page: 20,
      page: 2,
    });
    expect(search.filter_by).toContain("is_active:true");
    expect(search.filter_by).toContain("has_content:!=false");
    expect(search.filter_by).toContain(`company_id:[${companyId}]`);
    expect(search.filter_by).toContain("location_ids:[1]");
    expect(search.filter_by).toContain("occupation_ids:[2]");
    expect(search.filter_by).toContain("seniority_id:[3]");
    expect(search.filter_by).toContain("technology_ids:[4]");
    expect(search.filter_by).toContain("location_types:[remote]");
    expect(search.filter_by).toContain("employment_type:[full_time]");
    expect(search.filter_by).toContain("salary_eur:[100000..180000]");
    expect(search.filter_by).toContain("locales:[de,en,_none]");
  });

  it("uses an explicit half-open whole-second UTC window", () => {
    const windowStart = new Date("2026-08-24T00:00:00.000Z");
    const windowEnd = new Date("2026-08-31T00:00:00.000Z");
    const filter = buildWatchlistCandidateWindowFilter({
      windowStart,
      windowEnd,
    });

    expect(WATCHLIST_CANDIDATE_WINDOW_BOUNDARY).toBe(
      "[windowStart, windowEnd)",
    );
    expect(filter).toBe(
      `first_seen_at:>=${windowStart.getTime() / 1_000} && ` +
        `first_seen_at:<${windowEnd.getTime() / 1_000}`,
    );

    const search = buildWatchlistCandidateSearchParams({
      filters: { companyIds: [], anyCompany: true },
      offset: 0,
      limit: 20,
      window: { windowStart, windowEnd },
      order: "newest",
    });
    expect(search.filter_by).toContain("is_active:true");
    expect(search.filter_by).toContain(filter);
    expect(search.sort_by).toBe("first_seen_at:desc");
  });

  it("rejects overlapping/ambiguous window bounds", () => {
    const instant = new Date("2026-08-31T00:00:00.000Z");
    expect(() =>
      buildWatchlistCandidateWindowFilter({
        windowStart: instant,
        windowEnd: instant,
      }),
    ).toThrow("windowStart must be earlier than windowEnd");
    expect(() =>
      buildWatchlistCandidateWindowFilter({
        windowStart: new Date("2026-08-24T00:00:00.001Z"),
        windowEnd: instant,
      }),
    ).toThrow("whole-second Typesense precision");
  });

  it("fails closed on unsafe company identifiers", () => {
    expect(() =>
      buildWatchlistCandidateSearchParams({
        filters: { companyIds: ["safe] || is_active:false"] },
        offset: 0,
        limit: 20,
      }),
    ).toThrow("invalid Typesense identifier");
  });
});
