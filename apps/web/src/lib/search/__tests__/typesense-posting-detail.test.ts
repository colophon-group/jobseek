import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

const mocks = vi.hoisted(() => ({
  documents: new Map<string, unknown>(),
  searchHits: [] as unknown[],
  searchRequests: [] as Record<string, unknown>[],
  getSearchClient: vi.fn(),
}));

vi.mock("../typesense-client", () => ({
  getSearchClient: mocks.getSearchClient,
}));

vi.mock("../typesense-retry", () => ({
  withTypesenseRetry: (fn: () => Promise<unknown>) => fn(),
}));

import {
  fetchIndexedPostingDetail,
  fetchIndexedPostingSnapshot,
  fetchIndexedPostingStates,
} from "../typesense-posting-detail";

const posting = {
  id: "posting-1",
  company_id: "company-1",
  company_name: "Embedded Company",
  company_slug: "embedded-company",
  company_icon: "embedded-icon.svg",
  title: "  Senior Engineer  ",
  is_active: false,
  location_ids: [10, 20, 30],
  location_names: ["Zurich"],
  location_types: ["onsite"],
  location_geo_types: ["city"],
  seniority_id: 2,
  seniority_name: "Senior",
  technology_ids: [7, 8],
  technology_names: ["TypeScript", "PostgreSQL"],
  employment_type: "full-time",
  salary_min: 120_000,
  salary_max: 150_000,
  salary_currency: "CHF",
  salary_period: "year",
  experience_min_years: 3.5,
  experience_max_years: 99,
  experience_min: 4,
  experience_max: 99,
  locales: ["de", "en"],
  source_url: "https://example.com/jobs/1",
  first_seen_at: 1_767_225_600,
};

beforeEach(() => {
  mocks.documents.clear();
  mocks.searchHits = [posting];
  mocks.searchRequests = [];
  mocks.documents.set("job_posting/posting-1", posting);
  mocks.documents.set("company/company-1", {
    id: "company-1",
    name: "Canonical Company",
    slug: "canonical-company",
    logo: "logo.svg",
    icon: "icon.svg",
  });
  mocks.documents.set("location/10", {
    id: "10",
    location_id: 10,
    name_en: "Zurich",
    name_de: "Zürich",
    type: "city",
    parent_name: "Switzerland",
  });
  mocks.documents.set("seniority/2-de", {
    id: "2-de",
    seniority_id: 2,
    slug: "senior",
    name: "Senior",
    locale: "de",
  });

  mocks.getSearchClient.mockReturnValue({
    collections: (collection: string) => ({
      documents: (id?: string) =>
        id
          ? {
              retrieve: async () => {
                const key = `${collection}/${id}`;
                if (!mocks.documents.has(key)) throw new Error(`missing ${key}`);
                return mocks.documents.get(key);
              },
            }
          : {
              search: async (request: Record<string, unknown>) => {
                mocks.searchRequests.push(request);
                return {
                  hits: mocks.searchHits.map((document) => ({ document })),
                };
              },
            },
    }),
  });
  vi.spyOn(console, "warn").mockImplementation(() => undefined);
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Typesense posting projection", () => {
  it("maps an immutable saved-job snapshot", async () => {
    const snapshot = await fetchIndexedPostingSnapshot("posting-1");

    expect(snapshot).toMatchObject({
      id: "posting-1",
      title: "  Senior Engineer  ",
      sourceUrl: "https://example.com/jobs/1",
      isActive: false,
      salaryMin: 120_000,
      salaryMax: 150_000,
      salaryCurrency: "CHF",
      salaryPeriod: "year",
      company: {
        id: "company-1",
        name: "Embedded Company",
        slug: "embedded-company",
        icon: "embedded-icon.svg",
      },
    });
  });

  it("hydrates localized detail and ignores ancestor-only location ids", async () => {
    const detail = await fetchIndexedPostingDetail("posting-1", "de");

    expect(detail).toMatchObject({
      company: {
        id: "company-1",
        name: "Canonical Company",
        slug: "canonical-company",
        logo: "logo.svg",
        icon: "icon.svg",
      },
      locations: [
        {
          id: 10,
          name: "Zürich",
          type: "onsite",
          geoType: "city",
          parentName: "Switzerland",
        },
      ],
      experienceMin: 3.5,
      experienceMax: null,
      technologies: [
        { id: 7, name: "TypeScript" },
        { id: 8, name: "PostgreSQL" },
      ],
      seniority: { id: 2, slug: "senior", name: "Senior" },
      descriptionLocale: "de",
    });
    expect(mocks.documents.has("location/20")).toBe(false);
  });

  it("returns null when the posting document is unavailable", async () => {
    await expect(fetchIndexedPostingDetail("missing", "en")).resolves.toBeNull();
    await expect(fetchIndexedPostingSnapshot("missing")).rejects.toThrow(
      "missing job_posting/missing",
    );
  });

  it.each([
    ["source_url", { source_url: "" }],
    ["company_name", { company_name: "" }],
    ["company_slug", { company_slug: "" }],
    ["first_seen_at", { first_seen_at: 0 }],
    ["is_active", { is_active: "yes" }],
    ["salary_currency", { salary_currency: 12 }],
    ["company_icon", { company_icon: "" }],
  ])("rejects an invalid required %s snapshot field", async (field, change) => {
    mocks.documents.set("job_posting/posting-1", { ...posting, ...change });

    await expect(fetchIndexedPostingSnapshot("posting-1")).rejects.toThrow(
      field,
    );
  });

  it("keeps absent salary and icon fields nullable", async () => {
    const {
      salary_min: _salaryMin,
      salary_max: _salaryMax,
      salary_currency: _salaryCurrency,
      salary_period: _salaryPeriod,
      company_icon: _companyIcon,
      ...withoutOptionalFields
    } = posting;
    mocks.documents.set("job_posting/posting-1", withoutOptionalFields);

    await expect(fetchIndexedPostingSnapshot("posting-1")).resolves.toMatchObject({
      salaryMin: null,
      salaryMax: null,
      salaryCurrency: null,
      salaryPeriod: null,
      company: { icon: null },
    });
  });

  it("resolves current saved-job state in one bounded search", async () => {
    mocks.searchHits = [
      posting,
      { ...posting, id: "posting-2", is_active: true },
    ];
    const states = await fetchIndexedPostingStates([
      "posting-1",
      "posting-2",
      "posting-1",
      "bad:id",
    ]);

    expect(states.get("posting-1")).toEqual({
      isActive: false,
      sourceUrl: "https://example.com/jobs/1",
    });
    expect(states.get("posting-2")).toEqual({
      isActive: true,
      sourceUrl: "https://example.com/jobs/1",
    });
    expect(states.size).toBe(2);
    expect(mocks.searchRequests[0]).toMatchObject({
      include_fields: "id,is_active,source_url",
      per_page: 2,
    });
  });

  it("returns an empty state map when Typesense is unavailable", async () => {
    mocks.getSearchClient.mockImplementationOnce(() => {
      throw new Error("Typesense unavailable");
    });

    await expect(fetchIndexedPostingStates(["posting-1"])).resolves.toEqual(
      new Map(),
    );
  });
});
