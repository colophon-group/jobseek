import { describe, it, expect } from "vitest";
import { readingTimeMinutes } from "../blog";

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
