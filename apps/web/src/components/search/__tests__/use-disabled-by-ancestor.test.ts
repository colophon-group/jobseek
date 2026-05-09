import { describe, it, expect } from "vitest";
import { renderHook } from "@testing-library/react";
import {
  useDisabledByAncestor,
  pruneRedundantDescendants,
} from "../use-disabled-by-ancestor";

describe("useDisabledByAncestor", () => {
  /**
   * Tree:
   *   countryA (1) -> regionA (10) -> cityA (100)
   *   countryB (2) -> cityB (200)              <- region-less branch
   *   macroEU (50) covers [countryA, countryB]
   */
  const parents = new Map<number, number | null>([
    [1, null],
    [10, 1],
    [100, 10],
    [2, null],
    [200, 2],
    [50, null], // macro is parent-less
  ]);
  const macroMembers = new Map<number, readonly number[]>([
    [50, [1, 2]],
  ]);

  it("returns isDisabled false when no ancestor is selected", () => {
    const { result } = renderHook(() =>
      useDisabledByAncestor({
        selectedIds: new Set<number>(),
        parents,
        macroMembers,
      }),
    );
    expect(result.current.isDisabled(100)).toBe(false);
    expect(result.current.disabledByAncestor(100)).toBe(null);
  });

  it("disables city when its country is selected", () => {
    const { result } = renderHook(() =>
      useDisabledByAncestor({
        selectedIds: new Set([1]),
        parents,
        macroMembers,
      }),
    );
    expect(result.current.isDisabled(100)).toBe(true);
    expect(result.current.disabledByAncestor(100)).toBe(1);
    // Region also disabled (its parent country is selected)
    expect(result.current.isDisabled(10)).toBe(true);
    expect(result.current.disabledByAncestor(10)).toBe(1);
  });

  it("disables city when its region is selected (and region's parent country isn't)", () => {
    const { result } = renderHook(() =>
      useDisabledByAncestor({
        selectedIds: new Set([10]),
        parents,
        macroMembers,
      }),
    );
    expect(result.current.isDisabled(100)).toBe(true);
    expect(result.current.disabledByAncestor(100)).toBe(10);
    // Country itself is NOT disabled — selection is at the region level
    expect(result.current.isDisabled(1)).toBe(false);
  });

  it("does not flag the selected row itself as disabled", () => {
    const { result } = renderHook(() =>
      useDisabledByAncestor({
        selectedIds: new Set([1]),
        parents,
        macroMembers,
      }),
    );
    expect(result.current.isDisabled(1)).toBe(false);
  });

  it("disables member countries (and transitively their descendants) when a macro is selected", () => {
    const { result } = renderHook(() =>
      useDisabledByAncestor({
        selectedIds: new Set([50]),
        parents,
        macroMembers,
      }),
    );
    // Member country
    expect(result.current.isDisabled(1)).toBe(true);
    expect(result.current.disabledByAncestor(1)).toBe(50);
    // Region inside member country (transitive)
    expect(result.current.isDisabled(10)).toBe(true);
    expect(result.current.disabledByAncestor(10)).toBe(50);
    // City three levels down
    expect(result.current.isDisabled(100)).toBe(true);
    // Other member country
    expect(result.current.isDisabled(2)).toBe(true);
    // City under the second member country
    expect(result.current.isDisabled(200)).toBe(true);
    // Macro itself isn't disabled (it's the selected row)
    expect(result.current.isDisabled(50)).toBe(false);
  });

  it("handles cycle in the parent chain without infinite-looping", () => {
    const cyclic = new Map<number, number | null>([
      [1, 2],
      [2, 3],
      [3, 1], // cycle 1 -> 2 -> 3 -> 1
    ]);
    const { result } = renderHook(() =>
      useDisabledByAncestor({
        selectedIds: new Set<number>(),
        parents: cyclic,
      }),
    );
    expect(result.current.isDisabled(1)).toBe(false);
  });
});

describe("pruneRedundantDescendants", () => {
  const parents = new Map<number, number | null>([
    [1, null],
    [10, 1],
    [100, 10],
    [2, null],
    [200, 2],
  ]);
  const macroMembers = new Map<number, readonly number[]>([
    [50, [1, 2]],
  ]);

  it("drops descendants when a parent is committed in the same array", () => {
    const items = [
      { id: 1, name: "Country A" },
      { id: 100, name: "City under A" },
      { id: 2, name: "Country B" },
    ];
    const kept = pruneRedundantDescendants(items, parents, macroMembers);
    expect(kept.map((i) => i.id)).toEqual([1, 2]);
  });

  it("drops cities under a macro's member country", () => {
    const items = [
      { id: 50, name: "EU" },
      { id: 100, name: "City under member country A" },
      { id: 200, name: "City under member country B" },
    ];
    const kept = pruneRedundantDescendants(items, parents, macroMembers);
    expect(kept.map((i) => i.id)).toEqual([50]);
  });

  it("preserves siblings that are not ancestors of each other", () => {
    const items = [
      { id: 100, name: "City under A" },
      { id: 200, name: "City under B" },
    ];
    const kept = pruneRedundantDescendants(items, parents, macroMembers);
    expect(kept.map((i) => i.id)).toEqual([100, 200]);
  });
});
