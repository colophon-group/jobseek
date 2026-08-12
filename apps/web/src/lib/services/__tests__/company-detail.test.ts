import { beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

const mocks = vi.hoisted(() => ({
  cached: vi.fn(
    (_key: string, fetcher: () => Promise<unknown>, _options: unknown) => fetcher(),
  ),
  search: vi.fn(),
}));

vi.mock("server-only", () => ({}));
vi.mock("@/lib/cache", () => ({
  cached: mocks.cached,
}));
vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({ documents: () => ({ search: mocks.search }) }),
  }),
}));
vi.mock("@/lib/search/typesense-retry", () => ({
  isRetryableError: (err: unknown) =>
    typeof err === "object" &&
    err !== null &&
    "code" in err &&
    (err as { code?: unknown }).code === "ECONNRESET",
  isTypesenseRateLimitError: (err: unknown) =>
    typeof err === "object" &&
    err !== null &&
    (
      ("httpStatus" in err && (err as { httpStatus?: unknown }).httpStatus === 429) ||
      ("message" in err &&
        typeof (err as { message?: unknown }).message === "string" &&
        (err as { message: string }).message.includes("HTTP code 429"))
    ),
  isTypesenseUnavailableError: (err: unknown) =>
    typeof err === "object" &&
    err !== null &&
    (
      ("code" in err && (err as { code?: unknown }).code === "ECONNRESET") ||
      ("message" in err &&
        typeof (err as { message?: unknown }).message === "string" &&
        (err as { message: string }).message.includes("TYPESENSE_SEARCH_KEY"))
    ),
  withTypesenseRetry: (fn: () => Promise<unknown>) => fn(),
}));

import { getCompanyBySlug, getCompanyIdsBySlugs } from "../company-detail";

const searchMock = mocks.search;
const cachedMock = mocks.cached;
const TEST_ENV = {
  TYPESENSE_HOST: process.env.TYPESENSE_HOST ?? "localhost",
  TYPESENSE_PORT: process.env.TYPESENSE_PORT ?? "8108",
  TYPESENSE_PROTOCOL: process.env.TYPESENSE_PROTOCOL ?? "http",
  TYPESENSE_SEARCH_KEY: process.env.TYPESENSE_SEARCH_KEY ?? "test-key",
};

const hit = (overrides: Record<string, unknown> = {}) => ({
  id: "co-1",
  name: "Acme Corp",
  slug: "acme",
  icon: "https://cdn.x/icon.png",
  logo: null,
  website: "https://acme.example",
  description: "We build things in English.",
  industry_id: 7,
  industry_name: "Software",
  employee_count_range: 3,
  founded_year: 2015,
  active_posting_count: 42,
  ...overrides,
});

withTestEnv(TEST_ENV);

beforeEach(() => {
  vi.clearAllMocks();
  cachedMock.mockImplementation(
    (_key: string, fetcher: () => Promise<unknown>, _options: unknown) => fetcher(),
  );
  searchMock.mockReset();
});

describe("getCompanyBySlug", () => {
  it("uses the shared company-slug cache with skip-null semantics on a hit", async () => {
    searchMock.mockResolvedValue({ hits: [{ document: hit() }] });

    const out = await getCompanyBySlug("acme", "en");

    expect(out?.id).toBe("co-1");
    expect(cachedMock).toHaveBeenCalledTimes(1);
    const [key, _fetcher, options] = cachedMock.mock.calls[0] as [
      string,
      () => Promise<unknown>,
      { ttl: number; skipIf: (data: unknown) => boolean },
    ];
    expect(key).toBe("company-slug:acme:en");
    expect(options.ttl).toBe(600);
    expect(options.skipIf(null)).toBe(true);
    expect(options.skipIf(out)).toBe(false);
  });

  it("returns null on miss without throwing a cache-boundary sentinel", async () => {
    searchMock.mockResolvedValue({ hits: [] });

    const out = await getCompanyBySlug("ghost-slug", "en");

    expect(out).toBeNull();
    const options = cachedMock.mock.calls[0][2] as {
      skipIf: (data: unknown) => boolean;
    };
    expect(options.skipIf(out)).toBe(true);
  });

  it("returns null outside the cache boundary when lookup env is not configured", async () => {
    setTestEnv({
      DATABASE_URL: undefined,
      TYPESENSE_HOST: undefined,
      TYPESENSE_PORT: undefined,
      TYPESENSE_PROTOCOL: undefined,
      TYPESENSE_SEARCH_KEY: undefined,
    });
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const out = await getCompanyBySlug("acme", "en");

    expect(out).toBeNull();
    expect(cachedMock).not.toHaveBeenCalled();
    expect(searchMock).not.toHaveBeenCalled();
    expect(warnSpy).toHaveBeenCalledWith(
      "[company] lookup skipped because Typesense is not configured",
    );
    warnSpy.mockRestore();
  });

  it("returns null and reports explicit degradation when Typesense is unavailable", async () => {
    const error = new Error("TYPESENSE_SEARCH_KEY is not set");
    searchMock.mockRejectedValue(error);
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

    const out = await getCompanyBySlug("acme", "en");

    expect(out).toBeNull();
    expect(errorSpy).toHaveBeenCalledWith(
      "[company] Typesense unavailable; company detail degraded to not found",
      expect.objectContaining({
        event: "external_client_error",
        service: "typesense",
        operation: "company_detail_lookup",
      }),
    );
    errorSpy.mockRestore();
  });
});

describe("getCompanyIdsBySlugs", () => {
  it("resolves the handoff company set in one exact batched search", async () => {
    searchMock.mockResolvedValue({
      hits: [
        { document: { id: "uuid-stripe", slug: "stripe" } },
        { document: { id: "uuid-gitlab", slug: "gitlab" } },
      ],
    });

    await expect(getCompanyIdsBySlugs(["stripe", "gitlab"])).resolves.toEqual(
      new Map([
        ["stripe", "uuid-stripe"],
        ["gitlab", "uuid-gitlab"],
      ]),
    );
    expect(searchMock).toHaveBeenCalledTimes(1);
    expect(searchMock).toHaveBeenCalledWith({
      q: "*",
      filter_by: "slug:[stripe,gitlab]",
      per_page: 2,
    });
  });
});
