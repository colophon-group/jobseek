import { describe, expect, it, vi } from "vitest";
import {
  canResolveCompanyBySlugFromEnv,
  isSafeCompanySlug,
  mapTypesenseCompanyHitToDetail,
  resolveCompanyBySlug,
  type CompanyDetail,
} from "../company-detail-lookup";

const detail = (overrides: Partial<CompanyDetail> = {}): CompanyDetail => ({
  id: "co-1",
  name: "Acme Corp",
  slug: "acme",
  icon: "https://cdn.example/icon.png",
  logo: null,
  website: "https://acme.example",
  description: "We build things.",
  industryId: 7,
  industryName: "Software",
  employeeCountRange: 3,
  foundedYear: 2015,
  activeJobCount: 42,
  ...overrides,
});

const hit = (overrides: Record<string, unknown> = {}) => ({
  id: "co-1",
  name: "Acme Corp",
  slug: "acme",
  icon: "https://cdn.example/icon.png",
  logo: null,
  website: "https://acme.example",
  description: "We build things.",
  description_de: "Wir bauen Dinge.",
  industry_id: 7,
  industry_name: "Software",
  industry_name_de: "Software DE",
  employee_count_range: 3,
  founded_year: 2015,
  active_posting_count: 42,
  ...overrides,
});

const unavailable = (err: unknown): boolean =>
  err instanceof Error && err.message.includes("TYPESENSE_SEARCH_KEY");

describe("company detail mapping", () => {
  it("maps a Typesense hit into CompanyDetail values", () => {
    expect(mapTypesenseCompanyHitToDetail(hit(), "fallback-slug", "en")).toEqual(
      detail(),
    );
  });

  it("prefers non-empty localized Typesense fields and falls back to English", () => {
    const localized = mapTypesenseCompanyHitToDetail(hit(), "acme", "de");
    expect(localized.description).toBe("Wir bauen Dinge.");
    expect(localized.industryName).toBe("Software DE");

    const fallback = mapTypesenseCompanyHitToDetail(
      hit({ description_fr: "", industry_name_fr: undefined }),
      "acme",
      "fr",
    );
    expect(fallback.description).toBe("We build things.");
    expect(fallback.industryName).toBe("Software");

    const missing = mapTypesenseCompanyHitToDetail(
      hit({ description: undefined, description_it: undefined }),
      "acme",
      "it",
    );
    expect(missing.description).toBeNull();
  });
});

describe("company slug lookup resolver", () => {
  it("detects safe slugs and configured lookup environments", () => {
    expect(isSafeCompanySlug("acme")).toBe(true);
    expect(isSafeCompanySlug("1-800-flowers")).toBe(true);
    expect(isSafeCompanySlug("acme corp")).toBe(false);
    expect(isSafeCompanySlug("acme&&filter:=evil")).toBe(false);
    expect(isSafeCompanySlug("ACME")).toBe(false);
    expect(canResolveCompanyBySlugFromEnv({ DATABASE_URL: "postgres://test" })).toBe(false);
    expect(
      canResolveCompanyBySlugFromEnv({
        TYPESENSE_HOST: "localhost",
        TYPESENSE_PORT: "8108",
        TYPESENSE_PROTOCOL: "http",
        TYPESENSE_SEARCH_KEY: "xyz",
      }),
    ).toBe(true);
    expect(canResolveCompanyBySlugFromEnv({ TYPESENSE_HOST: "localhost" })).toBe(false);
  });

  it("returns the Typesense result", async () => {
    const fetchFromTypesense = vi.fn().mockResolvedValue(detail());

    const out = await resolveCompanyBySlug("acme", "en", {
      fetchFromTypesense,
      isTypesenseUnavailableError: unavailable,
    });

    expect(out).toEqual(detail());
    expect(fetchFromTypesense).toHaveBeenCalledWith("acme", "en");
  });

  it("does not send malformed slugs to Typesense", async () => {
    const fetchFromTypesense = vi.fn().mockRejectedValue(new Error("should not run"));

    const out = await resolveCompanyBySlug("acme&&filter:=evil", "en", {
      fetchFromTypesense,
      isTypesenseUnavailableError: unavailable,
    });

    expect(out).toBeNull();
    expect(fetchFromTypesense).not.toHaveBeenCalled();
  });

  it("degrades to not found and logs when Typesense is unavailable", async () => {
    const error = new Error("TYPESENSE_SEARCH_KEY is not set");
    const logger = { error: vi.fn() };
    const fetchFromTypesense = vi.fn().mockRejectedValue(error);

    const out = await resolveCompanyBySlug("acme", "en", {
      fetchFromTypesense,
      isTypesenseUnavailableError: unavailable,
      logger,
    });

    expect(out).toBeNull();
    expect(logger.error).toHaveBeenCalledWith(
      "[company] Typesense unavailable; company detail degraded to not found",
      expect.objectContaining({
        event: "external_client_error",
        service: "typesense",
        operation: "company_detail_lookup",
      }),
    );
  });

  it("rethrows non-unavailable Typesense errors", async () => {
    const rateLimitError = Object.assign(new Error("Request failed with HTTP code 429"), {
      httpStatus: 429,
    });

    await expect(
      resolveCompanyBySlug("acme", "en", {
        fetchFromTypesense: vi.fn().mockRejectedValue(rateLimitError),
        isTypesenseUnavailableError: unavailable,
      }),
    ).rejects.toBe(rateLimitError);
  });

  it("returns null without logging for a normal Typesense miss", async () => {
    const logger = { error: vi.fn() };
    const out = await resolveCompanyBySlug("ghost-slug", "en", {
      fetchFromTypesense: vi.fn().mockResolvedValue(null),
      isTypesenseUnavailableError: unavailable,
      logger,
    });

    expect(out).toBeNull();
    expect(logger.error).not.toHaveBeenCalled();
  });
});
