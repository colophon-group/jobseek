/**
 * Tests for the OG prebake's `generateStaticParams` opt-in and fail-loud
 * conditions (issues #2891 and #3422).
 *
 * The module under test exports `generateStaticParams`, which dynamically
 * imports `@/lib/search/typesense-client` so the dependency can be mocked
 * here without touching the surrounding `ImageResponse` handler.
 *
 * The prebake is disabled unless `COMPANY_OG_PRERENDER_TOP_N` is explicitly
 * positive. Once enabled, the fail-loud gate remains
 * `isProductionBuild && hasTypesenseConfig`. When both are true and Typesense
 * throws, we re-throw to fail the Vercel deploy (silently shipping a partial
 * prebake hides a real outage). All three other quadrants soft-fail with a
 * console.warn and an empty array.
 */
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type MockInstance,
} from "vitest";

const { searchMock } = vi.hoisted(() => ({
  searchMock: vi.fn(),
}));

vi.mock("server-only", () => ({}));

vi.mock("@/lib/search/typesense-client", () => ({
  getSearchClient: () => ({
    collections: () => ({
      documents: () => ({
        search: searchMock,
      }),
    }),
  }),
}));

// `getCompanyBySlug` is only called by the default OG handler, which we
// don't exercise here — mock it to a no-op so importing the module
// doesn't pull in db / Typesense / cache transitive deps unnecessarily.
vi.mock("@/lib/actions/company", () => ({
  getCompanyBySlug: vi.fn(),
}));

import { generateStaticParams } from "../opengraph-image";

describe("opengraph-image generateStaticParams", () => {
  let warnSpy: MockInstance<typeof console.warn>;
  const ORIGINAL_VERCEL_ENV = process.env.VERCEL_ENV;
  const ORIGINAL_TYPESENSE_HOST = process.env.TYPESENSE_HOST;
  const ORIGINAL_OG_PRERENDER_TOP_N = process.env.COMPANY_OG_PRERENDER_TOP_N;

  beforeEach(() => {
    searchMock.mockReset();
    warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    warnSpy.mockRestore();
    // Restore env to whatever the test runner set it to. Using delete
    // when the original was undefined avoids leaking the literal string
    // "undefined" into subsequent tests.
    if (ORIGINAL_VERCEL_ENV === undefined) {
      delete process.env.VERCEL_ENV;
    } else {
      process.env.VERCEL_ENV = ORIGINAL_VERCEL_ENV;
    }
    if (ORIGINAL_TYPESENSE_HOST === undefined) {
      delete process.env.TYPESENSE_HOST;
    } else {
      process.env.TYPESENSE_HOST = ORIGINAL_TYPESENSE_HOST;
    }
    if (ORIGINAL_OG_PRERENDER_TOP_N === undefined) {
      delete process.env.COMPANY_OG_PRERENDER_TOP_N;
    } else {
      process.env.COMPANY_OG_PRERENDER_TOP_N = ORIGINAL_OG_PRERENDER_TOP_N;
    }
  });

  it(
    "production + Typesense configured + Typesense throws -> re-throws (fail loud)",
    async () => {
      process.env.VERCEL_ENV = "production";
      process.env.TYPESENSE_HOST = "typesense.example.com";
      process.env.COMPANY_OG_PRERENDER_TOP_N = "200";
      const err = new Error("connection refused");
      searchMock.mockRejectedValue(err);

      await expect(generateStaticParams()).rejects.toThrow("connection refused");
      // Fail-loud path doesn't warn — it throws, surfacing in the
      // Vercel build log directly.
      expect(warnSpy).not.toHaveBeenCalled();
    },
  );

  it(
    "production + Typesense configured + zero hits -> warns and returns []",
    async () => {
      process.env.VERCEL_ENV = "production";
      process.env.TYPESENSE_HOST = "typesense.example.com";
      process.env.COMPANY_OG_PRERENDER_TOP_N = "200";
      searchMock.mockResolvedValue({ hits: [] });

      const result = await generateStaticParams();

      expect(result).toEqual([]);
      expect(warnSpy).toHaveBeenCalledTimes(1);
      const message = warnSpy.mock.calls[0]?.[0] as string;
      expect(message).toContain("0 companies returned from");
      expect(message).toContain("Typesense");
    },
  );

  it(
    "preview env + Typesense throws -> warns and returns []",
    async () => {
      process.env.VERCEL_ENV = "preview";
      process.env.COMPANY_OG_PRERENDER_TOP_N = "200";
      // TYPESENSE_HOST may or may not be set on previews; clear it
      // to exercise the "not configured" branch alongside non-prod
      // env. Either way this quadrant must NOT throw.
      delete process.env.TYPESENSE_HOST;
      searchMock.mockRejectedValue(new Error("dns failure"));

      const result = await generateStaticParams();

      expect(result).toEqual([]);
      expect(warnSpy).toHaveBeenCalledTimes(1);
      expect(warnSpy.mock.calls[0]?.[0]).toContain(
        "skipping prerender",
      );
    },
  );

  it(
    "local build (no VERCEL_ENV, no TYPESENSE_HOST) + Typesense throws -> warns and returns []",
    async () => {
      delete process.env.VERCEL_ENV;
      delete process.env.TYPESENSE_HOST;
      process.env.COMPANY_OG_PRERENDER_TOP_N = "200";
      searchMock.mockRejectedValue(new Error("invalid request"));

      const result = await generateStaticParams();

      expect(result).toEqual([]);
      expect(warnSpy).toHaveBeenCalledTimes(1);
    },
  );

  it(
    "unset COMPANY_OG_PRERENDER_TOP_N skips Typesense prebake by default",
    async () => {
      process.env.VERCEL_ENV = "production";
      process.env.TYPESENSE_HOST = "typesense.example.com";
      delete process.env.COMPANY_OG_PRERENDER_TOP_N;

      const result = await generateStaticParams();

      expect(result).toEqual([]);
      expect(searchMock).not.toHaveBeenCalled();
      expect(warnSpy).not.toHaveBeenCalled();
    },
  );

  it(
    "COMPANY_OG_PRERENDER_TOP_N=0 skips Typesense prebake",
    async () => {
      process.env.VERCEL_ENV = "production";
      process.env.TYPESENSE_HOST = "typesense.example.com";
      process.env.COMPANY_OG_PRERENDER_TOP_N = "0";

      const result = await generateStaticParams();

      expect(result).toEqual([]);
      expect(searchMock).not.toHaveBeenCalled();
      expect(warnSpy).not.toHaveBeenCalled();
    },
  );

  it(
    "happy path: returns slug × locale matrix (en/de/fr/it)",
    async () => {
      process.env.VERCEL_ENV = "production";
      process.env.TYPESENSE_HOST = "typesense.example.com";
      process.env.COMPANY_OG_PRERENDER_TOP_N = "2";
      searchMock.mockResolvedValue({
        hits: [{ document: { slug: "stripe" } }, { document: { slug: "airbnb" } }],
      });

      const result = await generateStaticParams();

      // 2 slugs × 4 locales = 8 entries.
      expect(result).toHaveLength(8);
      const stripeLocales = result
        .filter((entry) => entry.slug === "stripe")
        .map((entry) => entry.lang)
        .sort();
      expect(stripeLocales).toEqual(["de", "en", "fr", "it"]);
      expect(searchMock).toHaveBeenCalledWith(
        expect.objectContaining({ per_page: 2 }),
      );
      expect(warnSpy).not.toHaveBeenCalled();
    },
  );
});
