/**
 * Tests for the OG prebake's `generateStaticParams` fail-loud condition
 * (issue #2891 / PR #2888 round-3 critic ask).
 *
 * The module under test exports `generateStaticParams`, which dynamically
 * imports `@/lib/search/typesense-client` so the dependency can be mocked
 * here without touching the surrounding `ImageResponse` handler.
 *
 * The fail-loud gate: `isProductionBuild && hasTypesenseConfig`. When both
 * are true and Typesense throws, we re-throw to fail the Vercel deploy
 * (silently shipping zero prerender hides a real outage). All three other
 * quadrants soft-fail with a console.warn and an empty array.
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
  });

  it(
    "production + Typesense configured + Typesense throws -> re-throws (fail loud)",
    async () => {
      process.env.VERCEL_ENV = "production";
      process.env.TYPESENSE_HOST = "typesense.example.com";
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
      searchMock.mockRejectedValue(new Error("ECONNREFUSED"));

      const result = await generateStaticParams();

      expect(result).toEqual([]);
      expect(warnSpy).toHaveBeenCalledTimes(1);
    },
  );

  it(
    "happy path: returns slug × locale matrix (en/de/fr/it)",
    async () => {
      process.env.VERCEL_ENV = "production";
      process.env.TYPESENSE_HOST = "typesense.example.com";
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
      expect(warnSpy).not.toHaveBeenCalled();
    },
  );
});
