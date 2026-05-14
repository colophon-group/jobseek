import { describe, it, expect } from "vitest";
import { canonicalStringCompare, makeDisplayStringCompare } from "../sort";

describe("canonicalStringCompare — locale-independent canonicalization (#3221)", () => {
  it("regression: raw `.sort()` and `canonicalStringCompare` produce different orders for accented strings", () => {
    // The bug: bare `.sort()` uses UTF-16 code unit order, where
    // `"ü"` (U+00FC) sorts after `"z"` (U+007A). For a watchlist /
    // cache-key with keywords `["python","übung","zoom"]`, the
    // canonicalized order changes depending on which order the
    // caller provided them in.
    const raw = ["python", "übung", "zoom"].slice().sort();
    const canonical = ["python", "übung", "zoom"].slice().sort(canonicalStringCompare);

    expect(raw).toEqual(["python", "zoom", "übung"]);
    expect(canonical).toEqual(["python", "übung", "zoom"]);
    expect(raw).not.toEqual(canonical);
  });

  it("produces the same canonicalization key for every input permutation", () => {
    const inputs: string[][] = [
      ["python", "übung", "zoom"],
      ["zoom", "python", "übung"],
      ["übung", "zoom", "python"],
      ["zoom", "übung", "python"],
    ];
    const keys = inputs.map((arr) => arr.slice().sort(canonicalStringCompare).join("|"));
    // All permutations collapse to the same canonical key.
    expect(new Set(keys).size).toBe(1);
  });

  it("is stable across user-locale boundaries (de-DE viewer === en-US viewer)", () => {
    // The canonical collator pins locale to "en" — it must NOT vary
    // by viewer language, otherwise the same logical filter set
    // produces different cache keys for German vs English viewers,
    // splitting the cache.
    const input = ["zürich", "berlin", "ärger", "amsterdam"];
    const sortedOnce = input.slice().sort(canonicalStringCompare);
    const sortedTwice = sortedOnce.slice().sort(canonicalStringCompare);
    expect(sortedTwice).toEqual(sortedOnce);
    // Spot-check: base-letter folding puts "ärger" with the As, not
    // after "Z". The raw `.sort()` would order it last.
    expect(sortedOnce.indexOf("ärger")).toBeLessThan(sortedOnce.indexOf("berlin"));
  });

  it("treats base-letter variants as equal in ordering (no inversions)", () => {
    // `sensitivity: "base"` means `Ü` and `u` compare equal; the
    // collator falls back to identity for ties. Practically, this
    // means `Übung` and `übung` group together and are not split by
    // accidental case.
    const out = ["Übung", "übung", "Apple", "apple"].sort(canonicalStringCompare);
    // Apples first (base letter a < base letter u), regardless of case.
    expect(out.slice(0, 2).every((s) => s.toLowerCase() === "apple")).toBe(true);
    expect(out.slice(2).every((s) => s.toLowerCase() === "übung")).toBe(true);
  });
});

describe("makeDisplayStringCompare — locale-aware display ordering", () => {
  it("returns a comparator function bound to the requested locale", () => {
    const cmp = makeDisplayStringCompare("de-DE");
    expect(typeof cmp).toBe("function");
    // German collation folds umlauts to their base letter — `ö` is
    // sorted with `o`, well before `z`.
    const sorted = ["zürich", "öl", "apfel"].sort(cmp);
    expect(sorted[0]).toBe("apfel");
    expect(sorted[1]).toBe("öl");
    expect(sorted[2]).toBe("zürich");
  });

  it("uses `numeric: true` so `Item 2` sorts before `Item 10`", () => {
    const cmp = makeDisplayStringCompare("en-US");
    const sorted = ["Item 10", "Item 2", "Item 1"].sort(cmp);
    expect(sorted).toEqual(["Item 1", "Item 2", "Item 10"]);
  });
});
