import { describe, expect, it, vi } from "vitest";
import {
  canResolveCompanyBySlugFromEnv,
  isSafeCompanySlug,
  mapPostgresCompanyRowToDetail,
  mapTypesenseCompanyHitToDetail,
  resolveCompanyBySlug,
  type CompanyDetail,
  type PostgresCompanyRow,
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

const pgRow = (overrides: Partial<PostgresCompanyRow> = {}): PostgresCompanyRow => ({
  id: "co-1",
  name: "Acme Corp",
  slug: "acme",
  icon: null,
  logo: null,
  website: null,
  description: "From Postgres",
  industry_id: 7,
  industry_name: "Software",
  employee_count_range: 3,
  founded_year: 2015,
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

  it("maps a Postgres fallback row and leaves active count at zero", () => {
    expect(mapPostgresCompanyRowToDetail(pgRow())).toEqual(
      detail({
        icon: null,
        website: null,
        description: "From Postgres",
        activeJobCount: 0,
      }),
    );
  });
});

describe("company slug lookup resolver", () => {
  it("detects safe slugs and configured lookup environments", () => {
    expect(isSafeCompanySlug("acme")).toBe(true);
    expect(isSafeCompanySlug("1-800-flowers")).toBe(true);
    expect(isSafeCompanySlug("acme corp")).toBe(false);
    expect(isSafeCompanySlug("acme&&filter:=evil")).toBe(false);
    expect(isSafeCompanySlug("ACME")).toBe(false);
    expect(canResolveCompanyBySlugFromEnv({ DATABASE_URL: "postgres://test" })).toBe(true);
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

  it("returns the Typesense result without querying Postgres", async () => {
    const fetchFromTypesense = vi.fn().mockResolvedValue(detail());
    const fetchFromPostgres = vi.fn().mockResolvedValue(detail({ description: "pg" }));

    const out = await resolveCompanyBySlug("acme", "en", {
      fetchFromTypesense,
      fetchFromPostgres,
      hasPostgresConfig: () => true,
      isTypesenseUnavailableError: unavailable,
    });

    expect(out).toEqual(detail());
    expect(fetchFromTypesense).toHaveBeenCalledWith("acme", "en");
    expect(fetchFromPostgres).not.toHaveBeenCalled();
  });

  it("does not send malformed slugs to Typesense and lets Postgres miss naturally", async () => {
    const fetchFromTypesense = vi.fn().mockRejectedValue(new Error("should not run"));
    const fetchFromPostgres = vi.fn().mockResolvedValue(null);

    const out = await resolveCompanyBySlug("acme&&filter:=evil", "en", {
      fetchFromTypesense,
      fetchFromPostgres,
      hasPostgresConfig: () => true,
      isTypesenseUnavailableError: unavailable,
    });

    expect(out).toBeNull();
    expect(fetchFromTypesense).not.toHaveBeenCalled();
    expect(fetchFromPostgres).toHaveBeenCalledWith("acme&&filter:=evil", "en");
  });

  it("falls back to Postgres and logs when Typesense is unavailable", async () => {
    const error = new Error("TYPESENSE_SEARCH_KEY is not set");
    const logger = { error: vi.fn(), warn: vi.fn() };
    const fetchFromTypesense = vi.fn().mockRejectedValue(error);
    const fetchFromPostgres = vi.fn().mockResolvedValue(detail({ description: "pg" }));

    const out = await resolveCompanyBySlug("acme", "en", {
      fetchFromTypesense,
      fetchFromPostgres,
      hasPostgresConfig: () => true,
      isTypesenseUnavailableError: unavailable,
      logger,
    });

    expect(out?.description).toBe("pg");
    expect(fetchFromPostgres).toHaveBeenCalledWith("acme", "en");
    expect(logger.error).toHaveBeenCalledWith(
      "[company] Typesense failed, falling back to Postgres",
      error,
    );
    expect(logger.warn).not.toHaveBeenCalled();
  });

  it("rethrows non-unavailable Typesense errors without querying Postgres", async () => {
    const rateLimitError = Object.assign(new Error("Request failed with HTTP code 429"), {
      httpStatus: 429,
    });
    const fetchFromPostgres = vi.fn();

    await expect(
      resolveCompanyBySlug("acme", "en", {
        fetchFromTypesense: vi.fn().mockRejectedValue(rateLimitError),
        fetchFromPostgres,
        hasPostgresConfig: () => true,
        isTypesenseUnavailableError: unavailable,
      }),
    ).rejects.toBe(rateLimitError);
    expect(fetchFromPostgres).not.toHaveBeenCalled();
  });

  it("returns null and warns when neither backend can return a company", async () => {
    const logger = { error: vi.fn(), warn: vi.fn() };
    const out = await resolveCompanyBySlug("ghost-slug", "en", {
      fetchFromTypesense: vi.fn().mockResolvedValue(null),
      fetchFromPostgres: vi.fn().mockResolvedValue(detail()),
      hasPostgresConfig: () => false,
      isTypesenseUnavailableError: unavailable,
      logger,
    });

    expect(out).toBeNull();
    expect(logger.warn).toHaveBeenCalledWith(
      "[company] Postgres fallback skipped because DATABASE_URL is not configured",
    );
    expect(logger.error).not.toHaveBeenCalled();
  });
});
