import { describe, it, expect } from "vitest";
import {
  readingTimeMinutes,
  selectRelatedPosts,
  type BlogPostSummary,
} from "../blog";

describe("readingTimeMinutes", () => {
  it("returns 1 for short bodies", () => {
    expect(readingTimeMinutes("hello world")).toBe(1);
    expect(readingTimeMinutes("")).toBe(1);
  });

  it("scales at ~200 words per minute", () => {
    const body200 = Array.from({ length: 200 }, () => "word").join(" ");
    expect(readingTimeMinutes(body200)).toBe(1);
    const body800 = Array.from({ length: 800 }, () => "word").join(" ");
    expect(readingTimeMinutes(body800)).toBe(4);
  });

  it("rounds to nearest minute", () => {
    const body1100 = Array.from({ length: 1100 }, () => "word").join(" ");
    // 1100 / 200 = 5.5, rounds to 6.
    expect(readingTimeMinutes(body1100)).toBe(6);
  });

  it("treats whitespace-only as empty", () => {
    expect(readingTimeMinutes("   \n\t  ")).toBe(1);
  });
});

// listBlogPosts / getBlogPost / listBlogSlugs are filesystem-bound;
// they're exercised by the build-time `generateStaticParams` and the
// integration smoke run on the index page. No mock-fs in this suite.

describe("selectRelatedPosts", () => {
  function post(
    slug: string,
    overrides: Partial<BlogPostSummary> = {},
  ): BlogPostSummary {
    return {
      slug,
      title: `Title ${slug}`,
      description: `Description ${slug}`,
      datePublished: "2026-01-01",
      dateModified: "2026-01-01",
      author: "Author",
      tags: [],
      relatedCompanies: [],
      relatedWatchlists: [],
      relatedPosts: [],
      ...overrides,
    };
  }

  it("returns an empty array when there are no other posts", () => {
    const current = post("only-one");
    expect(selectRelatedPosts(current, [current])).toEqual([]);
  });

  it("excludes the current post from candidates", () => {
    const current = post("a");
    const result = selectRelatedPosts(current, [current, post("b"), post("c")]);
    expect(result.map((p) => p.slug)).not.toContain("a");
  });

  it("honors author-curated relatedPosts in authored order", () => {
    const current = post("a", { relatedPosts: ["c", "b"] });
    const all = [current, post("b"), post("c"), post("d")];
    const result = selectRelatedPosts(current, all);
    // c before b — preserve authored order, not alphabetical or chronological.
    expect(result.map((p) => p.slug)).toEqual(["c", "b", "d"]);
  });

  it("silently drops curated slugs that don't resolve to a published post", () => {
    const current = post("a", { relatedPosts: ["nonexistent", "b"] });
    const all = [current, post("b"), post("c")];
    const result = selectRelatedPosts(current, all);
    expect(result.map((p) => p.slug)).toEqual(["b", "c"]);
  });

  it("falls back to tag-overlap scoring when curated list is empty", () => {
    const current = post("a", { tags: ["x", "y"] });
    const all = [
      current,
      post("hi-overlap", { tags: ["x", "y", "z"] }), // overlap=2
      post("lo-overlap", { tags: ["x"] }), // overlap=1
      post("no-overlap", { tags: ["q"] }), // overlap=0 → tag step skips
      post("no-tags"), // overlap=0 → tag step skips
    ];
    // Tag step picks the two overlap winners in score order; recency
    // fallback fills the remaining slot from the no-overlap pool.
    const result = selectRelatedPosts(current, all);
    expect(result.slice(0, 2).map((p) => p.slug)).toEqual([
      "hi-overlap",
      "lo-overlap",
    ]);
    expect(result).toHaveLength(3);
  });

  it("picks fewer than max when no candidates are available", () => {
    const current = post("a");
    const all = [current, post("b")];
    const result = selectRelatedPosts(current, all);
    expect(result).toHaveLength(1);
    expect(result[0]?.slug).toBe("b");
  });

  it("breaks tag-overlap ties by recency (newest first)", () => {
    const current = post("a", { tags: ["x"] });
    const all = [
      current,
      post("old", { tags: ["x"], datePublished: "2024-01-01" }),
      post("new", { tags: ["x"], datePublished: "2026-06-01" }),
      post("mid", { tags: ["x"], datePublished: "2025-06-01" }),
    ];
    const result = selectRelatedPosts(current, all);
    expect(result.map((p) => p.slug)).toEqual(["new", "mid", "old"]);
  });

  it("fills the remainder with most-recent posts when overlap is insufficient", () => {
    const current = post("a", { tags: ["x"] });
    const all = [
      current,
      post("overlap", { tags: ["x"], datePublished: "2025-01-01" }),
      post("recent-no-overlap", { tags: ["q"], datePublished: "2026-06-01" }),
      post("old-no-overlap", { tags: ["q"], datePublished: "2024-06-01" }),
    ];
    const result = selectRelatedPosts(current, all);
    // overlap wins position 1; remaining 2 slots filled by recency, newest first.
    expect(result.map((p) => p.slug)).toEqual([
      "overlap",
      "recent-no-overlap",
      "old-no-overlap",
    ]);
  });

  it("respects the max parameter", () => {
    const current = post("a", { tags: ["x"] });
    const all = Array.from({ length: 5 }, (_, i) =>
      post(`p-${i}`, { tags: ["x"] }),
    );
    const result = selectRelatedPosts(current, [current, ...all], 2);
    expect(result).toHaveLength(2);
  });
});
