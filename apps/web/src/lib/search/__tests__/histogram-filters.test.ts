import { describe, it, expect } from "vitest";
import { normalizeHistogramFilters } from "../histogram-filters";

describe("normalizeHistogramFilters — stable `'use cache'` keys (#3276)", () => {
  // The salary / experience histogram cache slot is keyed off the
  // normalized filter shape. Same logical filter, different input
  // ordering → must collapse to one slot, or the `'use cache'` boundary
  // splits into n! permutations and the cache miss-rate collapses.

  it("permutes keywords to the same cache shape", () => {
    const ab = normalizeHistogramFilters({ keywords: ["python", "go"] });
    const ba = normalizeHistogramFilters({ keywords: ["go", "python"] });
    expect(JSON.stringify(ab)).toBe(JSON.stringify(ba));
  });

  it("regression: accented keywords no longer split the cache slot", () => {
    // Bare `.sort()` puts `"übung"` after `"z"` in UTF-16 order. With
    // `canonicalStringCompare`, the u-group collates next to itself, so
    // every permutation collapses.
    const a = normalizeHistogramFilters({ keywords: ["python", "übung", "zoom"] });
    const b = normalizeHistogramFilters({ keywords: ["übung", "zoom", "python"] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("base-sensitivity preserves relative ordering across case variants", () => {
    // `sensitivity: "base"` collates `"Apple"` and `"apple"` as equal; the
    // comparator just reorders, so the surface strings keep their case.
    // Within a single case, every permutation collapses to one shape.
    const upperA = normalizeHistogramFilters({ keywords: ["Apple", "banana"] });
    const upperB = normalizeHistogramFilters({ keywords: ["banana", "Apple"] });
    expect(JSON.stringify(upperA)).toBe(JSON.stringify(upperB));
    const lowerA = normalizeHistogramFilters({ keywords: ["apple", "banana"] });
    const lowerB = normalizeHistogramFilters({ keywords: ["banana", "apple"] });
    expect(JSON.stringify(lowerA)).toBe(JSON.stringify(lowerB));
    // The a-group sorts before the b-group regardless of case.
    expect(upperA.keywords[0]).toBe("Apple");
    expect(lowerA.keywords[0]).toBe("apple");
  });

  it("permutes languages to the same shape", () => {
    const a = normalizeHistogramFilters({ languages: ["de", "en", "fr"] });
    const b = normalizeHistogramFilters({ languages: ["fr", "de", "en"] });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("sorts numeric ID arrays numerically — no string coercion", () => {
    // Bare `.sort()` coerces to string and would produce `[10, 2]`. The
    // canonicalizer sorts as numbers: `[2, 10]`.
    const out = normalizeHistogramFilters({ locationIds: [10, 2, 100, 1] });
    expect(out.locationIds).toEqual([1, 2, 10, 100]);
  });

  it.each([
    ["locationIds"],
    ["occupationIds"],
    ["seniorityIds"],
    ["technologyIds"],
  ] as const)("permutes %s to the same shape", (field) => {
    const a = normalizeHistogramFilters({ [field]: [42, 7, 13] } as Record<string, number[]>);
    const b = normalizeHistogramFilters({ [field]: [7, 13, 42] } as Record<string, number[]>);
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });

  it("preserves the full normalized shape for empty input", () => {
    const out = normalizeHistogramFilters();
    expect(out).toEqual({
      companyId: "",
      keywords: [],
      locationIds: [],
      occupationIds: [],
      seniorityIds: [],
      technologyIds: [],
      languages: [],
    });
  });

  it("collapses combined permutations across every dimension", () => {
    const a = normalizeHistogramFilters({
      companyId: "acme",
      keywords: ["zoom", "apple"],
      locationIds: [42, 7],
      occupationIds: [3, 1],
      seniorityIds: [5, 2],
      technologyIds: [10, 4],
      languages: ["fr", "de"],
    });
    const b = normalizeHistogramFilters({
      languages: ["de", "fr"],
      technologyIds: [4, 10],
      seniorityIds: [2, 5],
      occupationIds: [1, 3],
      locationIds: [7, 42],
      keywords: ["apple", "zoom"],
      companyId: "acme",
    });
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });
});
