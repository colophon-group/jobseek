import { describe, it, expect } from "vitest";
import { canonicalizeFilters } from "../canonicalize-filters";

describe("canonicalizeFilters — stable cache keys (#3187)", () => {
  it("regression: same filter content in different array order JSON-encodes to the same string", () => {
    // Before the fix, `getGlobalLocationsGrouped`,
    // `getAllOccupationsGrouped`, `getAllSeniorities`, and
    // `getAllTechnologiesGrouped` used `JSON.stringify(filters)` for
    // their cache keys. Same content, different array order → different
    // cache slots → cache pollution and miss-rate collapse.
    const a = canonicalizeFilters({ locationIds: [42, 7] });
    const b = canonicalizeFilters({ locationIds: [7, 42] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
    expect(JSON.stringify(a)).toBe('{"locationIds":[7,42]}');
  });

  it("collapses occupationIds permutations", () => {
    const a = canonicalizeFilters({ occupationIds: [3, 1, 2] });
    const b = canonicalizeFilters({ occupationIds: [2, 3, 1] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("collapses seniorityIds permutations", () => {
    const a = canonicalizeFilters({ seniorityIds: [5, 1] });
    const b = canonicalizeFilters({ seniorityIds: [1, 5] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("collapses technologyIds permutations", () => {
    const a = canonicalizeFilters({ technologyIds: [10, 2, 7] });
    const b = canonicalizeFilters({ technologyIds: [2, 7, 10] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("sorts numeric IDs numerically (not lexicographically)", () => {
    // Bare `.sort()` would produce `[10, 2]` not `[2, 10]` because
    // it coerces to string. That alone is a cache-key bug.
    const out = canonicalizeFilters({ locationIds: [10, 2, 100, 1] });
    expect(out.locationIds).toEqual([1, 2, 10, 100]);
  });

  it("sorts keywords case-insensitively via canonicalStringCompare", () => {
    // `canonicalStringCompare` folds accents and case (sensitivity:
    // "base"), so `Apple` and `apple` collate to the same base position
    // and `übung` sorts with the u-group, not after `z`.
    const a = canonicalizeFilters({ keywords: ["zoom", "Apple", "übung"] });
    const b = canonicalizeFilters({ keywords: ["übung", "zoom", "Apple"] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
    // Apple sorts first (base letter a); übung in the u-group, before zoom.
    expect(a.keywords?.[0]?.toLowerCase()).toBe("apple");
    expect(a.keywords?.[2]).toBe("zoom");
  });

  it("sorts languages with canonicalStringCompare too", () => {
    const a = canonicalizeFilters({ languages: ["de", "en", "fr"] });
    const b = canonicalizeFilters({ languages: ["fr", "de", "en"] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("collapses workMode permutations for employment-type count cache keys (#3303)", () => {
    const a = canonicalizeFilters({ workMode: ["onsite", "remote"] });
    const b = canonicalizeFilters({ workMode: ["remote", "onsite"] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
    expect(JSON.stringify(a)).toBe('{"workMode":["onsite","remote"]}');
  });

  it("collapses employmentTypes permutations for work-mode count cache keys (#3303)", () => {
    const a = canonicalizeFilters({ employmentTypes: ["part_time", "full_time"] });
    const b = canonicalizeFilters({ employmentTypes: ["full_time", "part_time"] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
    expect(JSON.stringify(a)).toBe('{"employmentTypes":["full_time","part_time"]}');
  });

  it("does not mutate the input arrays", () => {
    const input = { locationIds: [42, 7], keywords: ["zoom", "apple"] };
    const before = JSON.stringify(input);
    canonicalizeFilters(input);
    expect(JSON.stringify(input)).toBe(before);
    expect(input.locationIds).toEqual([42, 7]);
    expect(input.keywords).toEqual(["zoom", "apple"]);
  });

  it("preserves empty arrays (does not collapse to undefined)", () => {
    // The original `JSON.stringify(filters)` path serialized `[]`
    // distinctly from missing keys. Preserve that so we don't
    // accidentally collide `{keywords: []}` with `{}` and change the
    // downstream `buildFilterString` behaviour.
    const out = canonicalizeFilters({ keywords: [], locationIds: [] });
    expect(out.keywords).toEqual([]);
    expect(out.locationIds).toEqual([]);
    expect(JSON.stringify(out)).toBe('{"keywords":[],"locationIds":[]}');
  });

  it("preserves scalar fields like companyId unchanged", () => {
    const out = canonicalizeFilters({ companyId: "acme-corp", locationIds: [9, 1] });
    expect(out.companyId).toBe("acme-corp");
    expect(out.locationIds).toEqual([1, 9]);
  });

  it("produces a stable JSON key irrespective of input property insertion order", () => {
    // V8 preserves object insertion order for `JSON.stringify`. The
    // helper assembles fields in a fixed order so callers that build
    // `{technologyIds, keywords}` and callers that build
    // `{keywords, technologyIds}` collapse to the same cache key.
    const a = canonicalizeFilters({ technologyIds: [3, 1], keywords: ["a", "b"] });
    const b = canonicalizeFilters({ keywords: ["b", "a"], technologyIds: [1, 3] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("omits fields that are undefined on the input", () => {
    // No need to spam the cache key with `"keywords":null` if the
    // caller never set keywords. JSON.stringify already drops `undefined`
    // entries; the helper must not re-introduce them as `null`.
    const out = canonicalizeFilters({ locationIds: [1] });
    expect(JSON.stringify(out)).toBe('{"locationIds":[1]}');
  });

  it("collapses combined permutations across multiple array dimensions", () => {
    // The worst case: every dimension's permutation multiplies the
    // keyspace. Verify that the helper folds them all into one key.
    const a = canonicalizeFilters({
      keywords: ["zoom", "apple"],
      locationIds: [42, 7],
      occupationIds: [3, 1],
      seniorityIds: [5, 2],
      technologyIds: [10, 4],
      languages: ["fr", "de"],
      workMode: ["remote", "onsite"],
      employmentTypes: ["part_time", "full_time"],
    });
    const b = canonicalizeFilters({
      employmentTypes: ["full_time", "part_time"],
      workMode: ["onsite", "remote"],
      languages: ["de", "fr"],
      technologyIds: [4, 10],
      seniorityIds: [2, 5],
      occupationIds: [1, 3],
      locationIds: [7, 42],
      keywords: ["apple", "zoom"],
    });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });
});
