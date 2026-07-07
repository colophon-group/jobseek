import { beforeEach, describe, expect, it, vi } from "vitest";
import { setTestEnv, withTestEnv } from "@/test-utils/env";

const mocks = vi.hoisted(() => ({
  cached: vi.fn(
    (_key: string, fetcher: () => Promise<unknown>, _options: unknown) => fetcher(),
  ),
  search: vi.fn(),
  dbExecute: vi.fn(),
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
vi.mock("@/db", () => ({ db: { execute: mocks.dbExecute } }));
vi.mock("drizzle-orm", () => ({
  sql: (strings: TemplateStringsArray, ..._values: unknown[]) =>
    strings.join("?"),
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

import { getCompanyBySlug } from "../company-detail";

const searchMock = mocks.search;
const dbExecuteMock = mocks.dbExecute;
const cachedMock = mocks.cached;
const TEST_ENV = {
  DATABASE_URL:
    process.env.DATABASE_URL ?? "postgresql://test:test@localhost:5432/test",
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

const pgRow = {
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
};

withTestEnv(TEST_ENV);

beforeEach(() => {
  vi.clearAllMocks();
  cachedMock.mockImplementation(
    (_key: string, fetcher: () => Promise<unknown>, _options: unknown) => fetcher(),
  );
  searchMock.mockReset();
  dbExecuteMock.mockReset();
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
    dbExecuteMock.mockResolvedValue([]);

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
    expect(dbExecuteMock).not.toHaveBeenCalled();
    expect(warnSpy).toHaveBeenCalledWith(
      "[company] lookup skipped because Typesense and DATABASE_URL are not configured",
    );
    warnSpy.mockRestore();
  });

  it("retries the Postgres query on transient ECONNRESET", async () => {
    searchMock.mockRejectedValue(new Error("TYPESENSE_SEARCH_KEY is not set"));
    const econn = new Error("read ECONNRESET") as Error & { code: string };
    econn.code = "ECONNRESET";
    dbExecuteMock
      .mockRejectedValueOnce(econn)
      .mockResolvedValueOnce([pgRow]);
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const out = await getCompanyBySlug("acme", "en");

    expect(out?.description).toBe("From Postgres");
    expect(dbExecuteMock).toHaveBeenCalledTimes(2);
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });
});
