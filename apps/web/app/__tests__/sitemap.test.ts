import { describe, it, expect, vi } from "vitest";

// Mock the database — sitemap queries the DB for companies and watchlists
vi.mock("@/db", () => ({
  db: {
    execute: vi.fn().mockResolvedValue([]),
  },
}));

// Mock the cache — passes through to the fetcher (no Redis in tests)
vi.mock("@/lib/cache", () => ({
  cached: (_key: string, fetcher: () => Promise<unknown>) => fetcher(),
}));

import sitemap from "../sitemap";

describe("sitemap", () => {
  it("returns an array of entries", async () => {
    const result = await sitemap();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
  });

  it("generates entries for all 4 locales", async () => {
    const result = await sitemap();
    const locales = ["en", "de", "fr", "it"];
    for (const locale of locales) {
      const hasLocale = result.some((entry) =>
        entry.url.includes(`/${locale}`)
      );
      expect(hasLocale, `should have entries for locale ${locale}`).toBe(true);
    }
  });

  it("each entry has required fields", async () => {
    const result = await sitemap();
    for (const entry of result) {
      expect(entry.url).toBeDefined();
      expect(typeof entry.url).toBe("string");
      expect(entry.url).toMatch(/^https?:\/\//);
      expect(entry.priority).toBeDefined();
      expect(entry.changeFrequency).toBeDefined();
    }
  });

  it("homepage entries have highest priority", async () => {
    const result = await sitemap();
    const homeEntries = result.filter((e) => e.url.match(/\/[a-z]{2}$/));
    for (const entry of homeEntries) {
      expect(entry.priority).toBe(1);
    }
  });
});
