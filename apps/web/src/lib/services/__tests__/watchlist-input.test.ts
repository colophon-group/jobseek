import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  WATCHLIST_COMPANY_MAX,
  WATCHLIST_DESCRIPTION_MAX_LENGTH,
  WATCHLIST_HANDOFF_COMPANY_MAX,
  WATCHLIST_TITLE_MAX_LENGTH,
  normalizeCreateWatchlistInput,
  normalizeHandoffWatchlistInput,
  normalizeSharedWatchlistMetadata,
  normalizeUpdateWatchlistInput,
  normalizeWatchlistCompaniesForRead,
  normalizeWatchlistCompanyIdsForRead,
  normalizeWatchlistFiltersForRead,
  normalizeWatchlistFiltersForSharedRead,
} from "@/lib/services/watchlist-input";

const WATCHLIST_ID = "10000000-0000-4000-8000-000000000001";
const COMPANY_ID_1 = "20000000-0000-4000-8000-000000000001";
const COMPANY_ID_2 = "20000000-0000-4000-8000-000000000002";

describe("watchlist server input normalization", () => {
  it("serializes scalar membership cap checks under a parent-row lock", () => {
    const source = readFileSync(join(process.cwd(), "src/lib/services/watchlists.ts"), "utf8");
    const addCompanySource = source.slice(
      source.indexOf("export async function addCompanyToWatchlist"),
      source.indexOf("export async function clearWatchlistCompanies"),
    );
    expect(addCompanySource).toContain('.for("update")');
    expect(addCompanySource).toContain("existing.length >= WATCHLIST_COMPANY_MAX");
    expect(addCompanySource.indexOf('.for("update")')).toBeLessThan(
      addCompanySource.indexOf("existing.length >= WATCHLIST_COMPANY_MAX"),
    );
    expect(addCompanySource.indexOf("existing.length >= WATCHLIST_COMPANY_MAX")).toBeLessThan(
      addCompanySource.indexOf(".insert(watchlistCompany)"),
    );
  });

  it("normalizes valid create input before persistence", () => {
    expect(normalizeCreateWatchlistInput({
      title: "  Engineering roles  ",
      description: "Keep whitespace\nthat the editor accepts.",
      companyIds: [` ${COMPANY_ID_1.toUpperCase()} `, COMPANY_ID_1, COMPANY_ID_2],
      filters: {
        keywords: ["Engineer", " engineer ", "Platform"],
        locationSlugs: ["zürich", "zürich", "switzerland"],
        workMode: ["REMOTE", "hybrid"],
        employmentType: ["FULL_TIME", "contract"],
        salaryCurrency: " chf ",
        salaryMin: 80_000,
        salaryMax: 140_000,
        experienceMin: 2,
        experienceMax: 8,
        anyCompany: false,
      },
    })).toEqual({
      ok: true,
      value: {
        title: "Engineering roles",
        description: "Keep whitespace\nthat the editor accepts.",
        companyIds: [COMPANY_ID_1, COMPANY_ID_2],
        filters: {
          keywords: ["Engineer", "Platform"],
          locationSlugs: ["zürich", "switzerland"],
          workMode: ["remote", "hybrid"],
          employmentType: ["full_time", "contract"],
          salaryCurrency: "CHF",
          salaryMin: 80_000,
          salaryMax: 140_000,
          experienceMin: 2,
          experienceMax: 8,
          anyCompany: false,
        },
        isPublic: undefined,
      },
    });
  });

  it.each([
    { title: " ", companyIds: [] },
    { title: "x".repeat(WATCHLIST_TITLE_MAX_LENGTH + 1), companyIds: [] },
    { title: "Valid", description: "x".repeat(WATCHLIST_DESCRIPTION_MAX_LENGTH + 1), companyIds: [] },
    {
      title: "Valid",
      companyIds: Array.from(
        { length: WATCHLIST_COMPANY_MAX + 1 },
        (_, index) => `20000000-0000-4000-8000-${index.toString().padStart(12, "0")}`,
      ),
    },
    { title: "Valid", companyIds: ["not-a-uuid"] },
    { title: "Valid", companyIds: [], filters: { keywords: Array.from({ length: 21 }, (_, i) => `k${i}`) } },
    { title: "Valid", companyIds: [], filters: { locationSlugs: ["unsafe slug"] } },
    { title: "Valid", companyIds: [], filters: { workMode: ["sometimes"] } },
    { title: "Valid", companyIds: [], filters: { employmentType: ["gig"] } },
    { title: "Valid", companyIds: [], filters: { employmentType: ["internship"] } },
    { title: "Valid", companyIds: [], filters: { salaryMin: Number.POSITIVE_INFINITY } },
    { title: "Valid", companyIds: [], filters: { salaryMin: 2, salaryMax: 1 } },
    { title: "Valid", companyIds: [], filters: { experienceMin: 16 } },
    { title: "Valid", companyIds: [], filters: { experienceMin: 1.5 } },
    { title: "Valid", companyIds: [], filters: { anyCompany: "yes" } },
  ])("rejects invalid or excessive create input before database work", (input) => {
    expect(normalizeCreateWatchlistInput(input)).toEqual({ ok: false });
  });

  it("normalizes partial updates while preserving null description semantics", () => {
    expect(normalizeUpdateWatchlistInput({
      watchlistId: ` ${WATCHLIST_ID} `,
      title: " Renamed ",
      description: null,
      companyIds: [COMPANY_ID_1, COMPANY_ID_1],
      filters: {},
    })).toEqual({
      ok: true,
      value: {
        watchlistId: WATCHLIST_ID,
        title: "Renamed",
        description: null,
        companyIds: [COMPANY_ID_1],
        filters: {},
        isPublic: undefined,
      },
    });
  });

  it("keeps the existing 25-company handoff limit and canonicalizes slugs", () => {
    const companySlugs = Array.from(
      { length: WATCHLIST_HANDOFF_COMPANY_MAX },
      (_, index) => `Company-${index}`,
    );
    const normalized = normalizeHandoffWatchlistInput({
      title: " Imported list ",
      companySlugs,
    });
    expect(normalized).toMatchObject({
      ok: true,
      value: {
        title: "Imported list",
        companySlugs: companySlugs.map((slug) => slug.toLowerCase()),
      },
    });
    expect(normalizeHandoffWatchlistInput({
      title: "Imported list",
      companySlugs: [...companySlugs, "one-too-many"],
    })).toEqual({ ok: false });
  });
});

describe("watchlist defensive JSON reads", () => {
  it("drops malformed filter values, deduplicates, and bounds legacy arrays", () => {
    expect(normalizeWatchlistFiltersForRead({
      keywords: ["Platform", "platform", 7, "", ...Array.from({ length: 20 }, (_, i) => `k${i}`)],
      locationSlugs: ["switzerland", "bad slug", null],
      workMode: ["remote", "sometimes", "remote"],
      employmentType: ["contract", "gig", "contract"],
      salaryCurrency: " chf ",
      salaryMin: 200,
      salaryMax: 100,
      experienceMin: Number.NaN,
      experienceMax: 8,
      anyCompany: "true",
    })).toEqual({
      keywords: [
        "Platform",
        ...Array.from({ length: 16 }, (_, index) => `k${index}`),
      ],
      locationSlugs: ["switzerland"],
      workMode: ["remote"],
      employmentType: ["contract"],
      salaryCurrency: "CHF",
      experienceMax: 8,
    });
  });

  it("returns safe empty structures for non-object JSONB values", () => {
    expect(normalizeWatchlistFiltersForRead(["not", "an", "object"])).toEqual({});
    expect(normalizeWatchlistCompanyIdsForRead("not-an-array")).toEqual([]);
    expect(normalizeWatchlistCompaniesForRead({ id: "not-an-array" })).toEqual([]);
  });

  it("migrates an internship-only legacy filter to Intern seniority on read", () => {
    expect(normalizeWatchlistFiltersForRead({
      employmentType: ["internship"],
    })).toEqual({ senioritySlugs: ["intern"] });
    expect(normalizeWatchlistFiltersForRead({
      employmentType: ["full_time", "internship"],
    })).toEqual({
      employmentType: ["full_time"],
    });
    expect(normalizeWatchlistFiltersForSharedRead({
      employmentType: ["internship"],
      senioritySlugs: ["senior"],
    })).toEqual({ senioritySlugs: ["senior"] });
  });

  it("fails a shared read closed when persisted filters exceed the runtime contract", () => {
    expect(normalizeWatchlistFiltersForSharedRead({
      workMode: ["remote", "future-mode"],
    })).toBeNull();
    expect(normalizeWatchlistFiltersForSharedRead({
      locationSlugs: Array.from({ length: 21 }, (_, index) => `location-${index}`),
    })).toBeNull();
  });

  it("fails shared metadata closed instead of truncating oversized legacy text", () => {
    expect(normalizeSharedWatchlistMetadata({
      title: "x".repeat(WATCHLIST_TITLE_MAX_LENGTH + 1),
      description: null,
    })).toBeNull();
    expect(normalizeSharedWatchlistMetadata({
      title: "Valid",
      description: "x".repeat(WATCHLIST_DESCRIPTION_MAX_LENGTH + 1),
    })).toBeNull();
  });

  it("validates shared company previews and caps rendering work", () => {
    const valid = Array.from({ length: WATCHLIST_COMPANY_MAX + 5 }, (_, index) => ({
      id: `company-${index}`,
      name: `Company ${index}`,
      slug: `company-${index}`,
      icon: index % 2 === 0 ? null : `https://cdn.example/${index}.svg`,
    }));
    const rawCompanies = [
      { id: "bad", name: 42, slug: "bad", icon: null },
      ...valid,
      valid[0],
    ];
    expect(normalizeWatchlistCompaniesForRead(
      rawCompanies,
      WATCHLIST_COMPANY_MAX,
    )).toBeNull();
    const companies = normalizeWatchlistCompaniesForRead(rawCompanies);
    expect(companies).not.toBeNull();
    expect(companies).toHaveLength(WATCHLIST_COMPANY_MAX + 5);
    expect(companies?.[0]).toEqual(valid[0]);
    expect(new Set(companies?.map((company) => company.id)).size).toBe(WATCHLIST_COMPANY_MAX + 5);
  });
});
