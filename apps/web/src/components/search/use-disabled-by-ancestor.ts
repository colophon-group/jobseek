/**
 * Shared "disabled-by-ancestor" hook for hierarchical filter modals
 * (locations + occupations). Given the currently-selected filter IDs and
 * a parent map, computes whether each ID would be redundant — i.e. some
 * ancestor in its chain is already selected.
 *
 * Returns:
 * - `isDisabled(id)` — true when any transitive ancestor of `id` is
 *   present in `selectedIds`. Self-membership is NOT a disable: a
 *   selected pill renders as active, not greyed.
 * - `disabledByAncestor(id)` — the ID of the nearest ancestor that
 *   triggered the disable (so the caller can look up its name). `null`
 *   when the row is not disabled.
 *
 * Two ancestor sources are walked, in this order:
 * 1. The plain `parents` map (id -> parentId | null) — covers
 *    country -> region -> city and occupation domain -> family-parent
 *    -> child.
 * 2. The optional `macroMembers` map (macroId -> [memberCountryIds]) —
 *    covers macro -> country, which is NOT a tree edge in the location
 *    parent_id schema (countries don't have a single macro parent;
 *    macros are membership rather than containment).
 *
 * Macros walked first by virtue of `macroMembers.entries()` running once
 * per render, then for each leaf row the hook walks the parent chain
 * upward and consults the macro->member set as a side-channel. Both
 * checks compose: selecting EU + selecting Germany also disables Berlin
 * via Germany even after EU's contribution is irrelevant — the result
 * is the same.
 *
 * Performance: `O(N * D)` where N = number of distinct IDs in the modal
 * and D = chain depth (≤4 for locations, ≤3 for occupations). Memoized
 * by `selectedIds` identity AND a stringified key derived from the
 * parents/macroMembers maps. The caller is expected to pass stable map
 * references; the hook does NOT memo when references change.
 *
 * Related: #2977 indexes ancestor IDs at write time so a single
 * `location_ids:=<parent>` filter matches every descendant. This hook
 * is the UI counterpart — descendants cannot add filtering signal so
 * the modal disables them.
 */

import { useMemo } from "react";

export interface UseDisabledByAncestorArgs {
  /** Currently-selected filter IDs. */
  selectedIds: ReadonlySet<number>;
  /**
   * id -> parentId mapping. Use `null` (NOT undefined) for top-level
   * rows. Including `null` parents in the map is fine — they just
   * terminate the walk.
   */
  parents: ReadonlyMap<number, number | null>;
  /**
   * Optional macro -> member-country IDs map. Used by location modals
   * where macros are not in the parent chain but disable their member
   * countries (and transitively regions + cities) when selected.
   */
  macroMembers?: ReadonlyMap<number, readonly number[]>;
}

export interface UseDisabledByAncestorResult {
  /** True if `id` has a selected ancestor (excluding `id` itself). */
  isDisabled(id: number): boolean;
  /** The ID of the nearest selected ancestor, or `null`. */
  disabledByAncestor(id: number): number | null;
}

export function useDisabledByAncestor({
  selectedIds,
  parents,
  macroMembers,
}: UseDisabledByAncestorArgs): UseDisabledByAncestorResult {
  return useMemo(() => {
    // Build a reverse map: countryId -> [macroIds that include it as a
    // member]. Walked first so a city's macro lineage is found even when
    // its country isn't in `parents` for some reason. Doing this once
    // per render keeps the per-row check O(D + macroChainLen).
    const countryToMacros = new Map<number, number[]>();
    if (macroMembers) {
      for (const [macroId, members] of macroMembers.entries()) {
        for (const cid of members) {
          let arr = countryToMacros.get(cid);
          if (!arr) { arr = []; countryToMacros.set(cid, arr); }
          arr.push(macroId);
        }
      }
    }

    function findAncestor(id: number): number | null {
      // Walk parent chain. Any selected ancestor (excluding self) wins.
      let cur = parents.get(id);
      const seen = new Set<number>([id]); // cycle guard
      while (cur != null && !seen.has(cur)) {
        seen.add(cur);
        if (selectedIds.has(cur)) return cur;
        // Ancestors-of-ancestors: also check macros that include this
        // node (the country in particular). This means selecting EU
        // disables Berlin via the Germany -> EU side-channel.
        const macros = countryToMacros.get(cur);
        if (macros) {
          for (const m of macros) {
            if (selectedIds.has(m)) return m;
          }
        }
        cur = parents.get(cur);
      }
      // Final check on `id` itself — covers "this row is a country and
      // a macro that includes it is selected".
      const directMacros = countryToMacros.get(id);
      if (directMacros) {
        for (const m of directMacros) {
          if (selectedIds.has(m)) return m;
        }
      }
      return null;
    }

    return {
      isDisabled(id: number) {
        return findAncestor(id) !== null;
      },
      disabledByAncestor(id: number) {
        return findAncestor(id);
      },
    };
  }, [selectedIds, parents, macroMembers]);
}

/**
 * Auto-deselect descendants when a parent is committed. Returns a fresh
 * array filtered to drop any entry whose `id` is now redundant under
 * the {@link useDisabledByAncestor} contract.
 *
 * Use after `onToggle` adds the parent — the hook computes
 * `disabledByAncestor` against the new selection and the caller filters
 * the chip list. This keeps the modal in a clean state where every
 * rendered chip is active (not selected-but-disabled).
 */
export function pruneRedundantDescendants<T extends { id: number }>(
  items: readonly T[],
  parents: ReadonlyMap<number, number | null>,
  macroMembers?: ReadonlyMap<number, readonly number[]>,
): T[] {
  const selectedIds = new Set(items.map((i) => i.id));
  const countryToMacros = new Map<number, number[]>();
  if (macroMembers) {
    for (const [macroId, members] of macroMembers.entries()) {
      for (const cid of members) {
        let arr = countryToMacros.get(cid);
        if (!arr) { arr = []; countryToMacros.set(cid, arr); }
        arr.push(macroId);
      }
    }
  }
  return items.filter((item) => {
    let cur = parents.get(item.id);
    const seen = new Set<number>([item.id]);
    while (cur != null && !seen.has(cur)) {
      seen.add(cur);
      if (selectedIds.has(cur)) return false;
      const macros = countryToMacros.get(cur);
      if (macros) {
        for (const m of macros) {
          if (selectedIds.has(m)) return false;
        }
      }
      cur = parents.get(cur);
    }
    const directMacros = countryToMacros.get(item.id);
    if (directMacros) {
      for (const m of directMacros) {
        if (selectedIds.has(m)) return false;
      }
    }
    return true;
  });
}
