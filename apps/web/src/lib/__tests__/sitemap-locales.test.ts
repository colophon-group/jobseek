import { describe, expect, it, vi } from "vitest";

vi.mock("../blog", () => ({
  listBlogPosts: async () => [{ slug: "translated", dateModified: "2026-05-07" }],
  getBlogPostLocales: async () => ["en", "de"],
  getBlogPost: async (_slug: string, locale: string) => ({
    dateModified: locale === "de" ? "2026-10-01" : "2026-05-07",
  }),
}));

import { buildSitemap } from "../sitemap";

describe("translated sitemap freshness", () => {
  it("uses each translation's date and advances only its blog index", async () => {
    const entries = await buildSitemap();
    const date = (path: string) => new Date(
      entries.find((entry) => entry.url === `https://jseek.co${path}`)!.lastModified!,
    ).toISOString().slice(0, 10);
    expect(date("/en/blog/translated")).toBe("2026-05-07");
    expect(date("/de/blog/translated")).toBe("2026-10-01");
    expect(date("/de/blog")).toBe("2026-10-01");
    expect(date("/en/blog")).toBe("2026-09-28");
    expect(entries.some((entry) => entry.url === "https://jseek.co/fr/blog/translated")).toBe(false);
  });
});
