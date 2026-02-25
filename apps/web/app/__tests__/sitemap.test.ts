import { describe, it, expect } from "vitest";
import sitemap from "../sitemap";

describe("sitemap", () => {
  it("returns an array of entries", () => {
    const result = sitemap();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
  });

  it("generates entries for all 4 locales", () => {
    const result = sitemap();
    const locales = ["en", "de", "fr", "it"];
    for (const locale of locales) {
      const hasLocale = result.some((entry) => entry.url.includes(`/${locale}`));
      expect(hasLocale, `should have entries for locale ${locale}`).toBe(true);
    }
  });

  it("each entry has required fields", () => {
    const result = sitemap();
    for (const entry of result) {
      expect(entry.url).toBeDefined();
      expect(typeof entry.url).toBe("string");
      expect(entry.url).toMatch(/^https?:\/\//);
      expect(entry.priority).toBeDefined();
      expect(entry.changeFrequency).toBeDefined();
    }
  });

  it("homepage entries have highest priority", () => {
    const result = sitemap();
    const homeEntries = result.filter((e) => e.url.match(/\/[a-z]{2}$/));
    for (const entry of homeEntries) {
      expect(entry.priority).toBe(1);
    }
  });
});
