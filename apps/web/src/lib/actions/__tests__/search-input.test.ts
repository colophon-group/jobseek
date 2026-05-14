import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  suggestLocations: vi.fn(),
  suggestOccupations: vi.fn(),
  suggestSeniorities: vi.fn(),
  suggestTechnologies: vi.fn(),
  resolveLocationSlugs: vi.fn(),
  resolveOccupationSlugs: vi.fn(),
  resolveSenioritySlugs: vi.fn(),
  resolveTechnologySlugs: vi.fn(),
}));

vi.mock("server-only", () => ({}));
vi.mock("@/lib/actions/locations", () => ({
  suggestLocations: mocks.suggestLocations,
  resolveLocationSlugs: mocks.resolveLocationSlugs,
}));
// `lib/services/search-input.ts` (the implementation reached via the
// `lib/actions/search-input` wrapper) imports taxonomy helpers
// directly from the service tier — see #3329. Mock the service module
// rather than the action wrapper so the substitution actually
// intercepts the call chain.
vi.mock("@/lib/services/taxonomy", () => ({
  suggestOccupations: mocks.suggestOccupations,
  suggestSeniorities: mocks.suggestSeniorities,
  suggestTechnologies: mocks.suggestTechnologies,
  resolveOccupationSlugs: mocks.resolveOccupationSlugs,
  resolveSenioritySlugs: mocks.resolveSenioritySlugs,
  resolveTechnologySlugs: mocks.resolveTechnologySlugs,
}));

import { parseSearchFilters } from "../search-input";

beforeEach(() => {
  vi.clearAllMocks();
  // Default: no taxonomy/location matches at all — we want pure
  // work-mode tokenization paths exercised here.
  mocks.suggestLocations.mockResolvedValue([]);
  mocks.suggestOccupations.mockResolvedValue([]);
  mocks.suggestSeniorities.mockResolvedValue([]);
  mocks.suggestTechnologies.mockResolvedValue([]);
  mocks.resolveLocationSlugs.mockResolvedValue(new Map());
  mocks.resolveOccupationSlugs.mockResolvedValue(new Map());
  mocks.resolveSenioritySlugs.mockResolvedValue(new Map());
  mocks.resolveTechnologySlugs.mockResolvedValue(new Map());
});

// =====================================================================
// Issue #2983: free-text tokenization recognizes work-mode tokens
// (`remote`, `hybrid`, `onsite`, plus a small synonym set) and reports
// them on the parsed `workMode` field. Synonyms list is intentionally
// narrow to avoid colliding with common job titles.
// =====================================================================

describe("parseSearchFilters — workMode tokenization (#2983)", () => {
  it("returns empty workMode when q is empty and no `wm` param", async () => {
    const r = await parseSearchFilters({ locale: "en" });
    expect(r.workMode).toEqual([]);
  });

  it("recognizes a bare `remote` token", async () => {
    const r = await parseSearchFilters({ q: "remote", locale: "en" });
    expect(r.workMode).toEqual(["remote"]);
    expect(r.keywords).toEqual([]);
  });

  it("recognizes `hybrid` and `onsite` single-word tokens", async () => {
    const hyb = await parseSearchFilters({ q: "hybrid", locale: "en" });
    expect(hyb.workMode).toEqual(["hybrid"]);
    const ons = await parseSearchFilters({ q: "onsite", locale: "en" });
    expect(ons.workMode).toEqual(["onsite"]);
  });

  it("recognizes the `wfh` synonym as remote", async () => {
    const r = await parseSearchFilters({ q: "wfh engineer", locale: "en" });
    expect(r.workMode).toEqual(["remote"]);
    // engineer falls through to keywords here because suggestOccupations
    // is mocked to return no matches.
    expect(r.keywords).toEqual(["engineer"]);
  });

  it("recognizes `work from home` as remote (multi-word)", async () => {
    const r = await parseSearchFilters({ q: "work from home", locale: "en" });
    expect(r.workMode).toEqual(["remote"]);
    expect(r.keywords).toEqual([]);
  });

  it("recognizes `in office` as onsite", async () => {
    const r = await parseSearchFilters({ q: "in office", locale: "en" });
    expect(r.workMode).toEqual(["onsite"]);
  });

  it("does not match the bare `office` word (would clash with job titles)", async () => {
    const r = await parseSearchFilters({ q: "office", locale: "en" });
    expect(r.workMode).toEqual([]);
    expect(r.keywords).toEqual(["office"]);
  });

  it("composes free-text matches with the `wm` URL param without dupes", async () => {
    const r = await parseSearchFilters({
      q: "remote",
      wm: "hybrid",
      locale: "en",
    });
    expect(r.workMode).toEqual(["hybrid", "remote"]);
  });

  it("dedupes when free-text and URL agree", async () => {
    const r = await parseSearchFilters({
      q: "remote",
      wm: "remote",
      locale: "en",
    });
    expect(r.workMode).toEqual(["remote"]);
  });

  it("ignores invalid wm tokens but parses free text", async () => {
    const r = await parseSearchFilters({
      q: "remote",
      wm: "bogus",
      locale: "en",
    });
    expect(r.workMode).toEqual(["remote"]);
  });

  it("case-insensitive matching", async () => {
    const r = await parseSearchFilters({ q: "REMOTE", locale: "en" });
    expect(r.workMode).toEqual(["remote"]);
  });
});
