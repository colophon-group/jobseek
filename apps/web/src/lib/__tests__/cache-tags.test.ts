import { describe, expect, it } from "vitest";
import {
  blogIndexCacheTag,
  blogPostCacheTag,
  companyByIdCacheTag,
  companyCacheTag,
  companyCsvDataCacheTag,
  watchlistCacheTag,
} from "../cache-tags";

/**
 * The contract these tests guard: the strings emitted by the namers ARE
 * the tags `updateTag()` is called with from server actions, and the
 * same strings are passed to `cacheTag()` inside `'use cache'` boundaries.
 * If the format drifts, mutations stop busting their cached pages — a
 * silent staleness bug. So these are exact-string assertions on purpose.
 *
 * See PR #2888 for the original feature context and round-3/round-4
 * critic asks behind this test file.
 */
describe("cache-tags", () => {
  describe("watchlistCacheTag", () => {
    it("returns watchlist:<userSlug>:<watchlistSlug>", () => {
      expect(watchlistCacheTag("alice", "saved-jobs")).toBe(
        "watchlist:alice:saved-jobs",
      );
    });

    it("produces different tags for different inputs", () => {
      expect(watchlistCacheTag("alice", "a")).not.toBe(
        watchlistCacheTag("alice", "b"),
      );
      expect(watchlistCacheTag("alice", "x")).not.toBe(
        watchlistCacheTag("bob", "x"),
      );
    });

    it("does not normalize special characters (slugs are validated upstream)", () => {
      // The namer is a pure string concat — it doesn't sanitize. Slug
      // shape is the caller's responsibility. Verify pass-through so we
      // catch any future change that adds normalization.
      expect(watchlistCacheTag("user:1", "wl/2")).toBe(
        "watchlist:user:1:wl/2",
      );
    });
  });

  describe("companyCacheTag", () => {
    it("returns company:<slug>", () => {
      expect(companyCacheTag("stripe")).toBe("company:stripe");
    });

    it("produces different tags for different slugs", () => {
      expect(companyCacheTag("stripe")).not.toBe(companyCacheTag("airbnb"));
    });

    it("does not normalize special characters in the slug", () => {
      expect(companyCacheTag("foo-bar")).toBe("company:foo-bar");
      expect(companyCacheTag("foo:bar")).toBe("company:foo:bar");
    });
  });

  describe("companyByIdCacheTag", () => {
    it("returns company-id:<companyId>", () => {
      expect(companyByIdCacheTag("co-uuid-123")).toBe("company-id:co-uuid-123");
    });

    it("produces a tag distinct from companyCacheTag(slug)", () => {
      // The page route and the data layer use different keys (slug vs.
      // companyId UUID). Distinct prefixes make this explicit and avoid
      // cross-namespace `updateTag` collisions.
      expect(companyByIdCacheTag("foo")).not.toBe(companyCacheTag("foo"));
      expect(companyByIdCacheTag("foo").startsWith("company-id:")).toBe(true);
      expect(companyCacheTag("foo").startsWith("company:")).toBe(true);
    });
  });

  describe("companyCsvDataCacheTag", () => {
    it("returns the literal company-csv-data", () => {
      expect(companyCsvDataCacheTag()).toBe("company-csv-data");
    });

    it("returns the same tag on every call (no input — shared sweep tag)", () => {
      // CSV-driven blanket tag fired by `crawler sync` to drop every
      // `getCompanyBySlug` and `getSimilarCompanies` slot in one call.
      expect(companyCsvDataCacheTag()).toBe(companyCsvDataCacheTag());
    });
  });

  describe("blogPostCacheTag", () => {
    it("returns blog-post:<slug>", () => {
      expect(blogPostCacheTag("hello-world")).toBe("blog-post:hello-world");
    });

    it("produces different tags for different slugs", () => {
      expect(blogPostCacheTag("hello-world")).not.toBe(
        blogPostCacheTag("goodbye-world"),
      );
    });

    it("does not normalize special characters in the slug", () => {
      expect(blogPostCacheTag("with spaces")).toBe("blog-post:with spaces");
      expect(blogPostCacheTag("a:b")).toBe("blog-post:a:b");
    });
  });

  describe("blogIndexCacheTag", () => {
    it("returns the literal blog-index", () => {
      expect(blogIndexCacheTag()).toBe("blog-index");
    });

    it("returns the same tag on every call (no input)", () => {
      expect(blogIndexCacheTag()).toBe(blogIndexCacheTag());
    });
  });

  describe("namer namespace separation", () => {
    // Cross-namer regression guard. If anyone collapses two namers to
    // share a prefix, an `updateTag('blog-post:slug')` call could
    // accidentally bust unrelated cached pages.
    it("each namer uses a distinct resource prefix", () => {
      expect(watchlistCacheTag("u", "w").startsWith("watchlist:")).toBe(true);
      expect(companyCacheTag("s").startsWith("company:")).toBe(true);
      expect(blogPostCacheTag("s").startsWith("blog-post:")).toBe(true);
      expect(blogIndexCacheTag()).toBe("blog-index");
    });
  });
});
