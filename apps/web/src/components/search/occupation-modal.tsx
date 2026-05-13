"use client";

import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Search, Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { getAllOccupationsGrouped } from "@/lib/actions/taxonomy";
import type { OccupationGroup, OccupationItem } from "@/lib/actions/taxonomy";
import { findBestGuess } from "./best-guess";
import { ScrollFade } from "@/components/ui/scroll-fade";
import { useDisabledByAncestor } from "./use-disabled-by-ancestor";
import { DisabledFilterPill } from "./disabled-filter-pill";

interface OccupationModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  locale: string;
  selected: { id: number; slug: string; name: string }[];
  onToggle: (occ: { id: number; slug: string; name: string }) => void;
  filters?: { companyId?: string; keywords?: string[]; locationIds?: number[]; seniorityIds?: number[]; technologyIds?: number[]; languages?: string[] };
}

export function OccupationModal({
  open,
  onOpenChange,
  locale,
  selected,
  onToggle,
  filters,
}: OccupationModalProps) {
  const { t } = useLingui();
  const [groups, setGroups] = useState<OccupationGroup[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [warning, setWarning] = useState("");
  const warningTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const selectedIds = useMemo(() => new Set(selected.map((s) => s.id)), [selected]);

  // Build occupation -> parent map from the loaded groups. Family
  // parents are top-level (parentId: null) within their domain; children
  // point to the family parent's id. Standalones are top-level with no
  // parent. Hook walks this chain to compute disable state.
  const parentMap = useMemo(() => {
    const map = new Map<number, number | null>();
    if (!groups) return map;
    for (const group of groups) {
      for (const sg of group.subGroups) {
        map.set(sg.parent.id, null);
        for (const child of sg.children) {
          map.set(child.id, sg.parent.id);
        }
      }
      for (const item of group.standalone) {
        map.set(item.id, null);
      }
    }
    return map;
  }, [groups]);

  const { isDisabled, disabledByAncestor } = useDisabledByAncestor({
    selectedIds,
    parents: parentMap,
  });

  const nameById = useMemo(() => {
    const map = new Map<number, string>();
    if (!groups) return map;
    for (const group of groups) {
      for (const sg of group.subGroups) {
        map.set(sg.parent.id, sg.parent.name);
        for (const child of sg.children) map.set(child.id, child.name);
      }
      for (const item of group.standalone) map.set(item.id, item.name);
    }
    return map;
  }, [groups]);

  const ancestorNameOf = useCallback((id: number): string => {
    const ancId = disabledByAncestor(id);
    if (ancId == null) return "";
    return nameById.get(ancId) ?? "";
  }, [disabledByAncestor, nameById]);

  /**
   * Wrap onToggle so that selecting a family parent auto-deselects any
   * children currently in `selected`. Parity with the location modals.
   */
  const handleToggle = useCallback((item: { id: number; slug: string; name: string }) => {
    const wasSelected = selectedIds.has(item.id);
    onToggle(item);
    if (wasSelected) return;
    // Find children of `item` currently in selection and toggle them off.
    for (const s of selected) {
      let cur = parentMap.get(s.id);
      const seen = new Set<number>([s.id]);
      while (cur != null && !seen.has(cur)) {
        seen.add(cur);
        if (cur === item.id) {
          onToggle(s);
          break;
        }
        cur = parentMap.get(cur);
      }
    }
  }, [onToggle, selected, selectedIds, parentMap]);

  const filtersKey = filters ? JSON.stringify(filters) : "";
  const prevFiltersKeyRef = useRef(filtersKey);

  useEffect(() => {
    if (open && (!groups || filtersKey !== prevFiltersKeyRef.current)) {
      prevFiltersKeyRef.current = filtersKey;
      setLoading(true);
      getAllOccupationsGrouped(locale, filters)
        .then(setGroups)
        .finally(() => setLoading(false));
    }
  }, [open, groups, locale, filtersKey]);

  useEffect(() => {
    if (!open) setSearch("");
  }, [open]);

  const filtered = useMemo(() => {
    if (!groups) return [];
    if (!search.trim()) return groups;
    const q = search.trim().toLowerCase();

    return groups
      .map((group) => {
        // If domain name matches, show entire group
        if (group.domain.name.toLowerCase().includes(q)) {
          return group;
        }

        // Filter sub-groups: keep if parent or any child matches
        const matchingSubGroups = group.subGroups
          .map((sg) => {
            const parentMatches = sg.parent.name.toLowerCase().includes(q);
            const matchingChildren = sg.children.filter((c) => c.name.toLowerCase().includes(q));
            if (parentMatches) return sg; // show full sub-group
            if (matchingChildren.length > 0) return { ...sg, children: matchingChildren };
            return null;
          })
          .filter((sg): sg is NonNullable<typeof sg> => sg !== null);

        // Filter standalone
        const matchingStandalone = group.standalone.filter((s) =>
          s.name.toLowerCase().includes(q),
        );

        if (matchingSubGroups.length === 0 && matchingStandalone.length === 0) return null;

        return { ...group, subGroups: matchingSubGroups, standalone: matchingStandalone };
      })
      .filter((g): g is OccupationGroup => g !== null);
  }, [groups, search]);

  const showWarning = useCallback((msg: string) => {
    clearTimeout(warningTimer.current);
    setWarning(msg);
    warningTimer.current = setTimeout(() => setWarning(""), 3000);
  }, []);

  const handleSearchKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key !== "Enter") return;
      const leafItems = filtered.flatMap((g) => [
        ...g.standalone,
        ...g.subGroups.flatMap((sg) => sg.children),
      ]);
      const result = findBestGuess(search, leafItems);
      if (!result) return;
      if ("match" in result) {
        onToggle(result.match);
        setSearch("");
        setWarning("");
      } else {
        showWarning(t({
          id: "search.bestGuess.ambiguous",
          comment: "Warning when Enter is pressed but multiple items match",
          message: "Multiple matches — select one below",
        }));
      }
    },
    [filtered, search, onToggle, showWarning, t],
  );

  /**
   * First-level descendants of a domain — the rows that render at depth-1
   * in the modal tree. This is the set of items the domain header toggles
   * (and the set used to compute the header's "all selected" indicator).
   *
   * Composition:
   * - Family parents: `group.subGroups[].parent` — top-level occupations
   *   in the domain that themselves have children (e.g. Software Engineer,
   *   DevOps Engineer).
   * - Standalones: `group.standalone[]` — top-level occupations in the
   *   domain with no children (e.g. QA Engineer).
   *
   * Grandchildren (`sg.children`) are NOT first-level — selecting them via
   * the domain header would be redundant once their family parent is
   * selected. The disable hook handles their UI state via the
   * parent-chain walk in `useDisabledByAncestor`.
   */
  function firstLevelItems(group: OccupationGroup): OccupationItem[] {
    const items: OccupationItem[] = [...group.standalone];
    for (const sg of group.subGroups) {
      items.push(sg.parent);
    }
    return items;
  }

  /**
   * Domain header click selects every first-level descendant of the
   * domain. Grandchildren (`sg.children`) are not added — they become
   * disabled (greyed) via `useDisabledByAncestor` because their family
   * parent is now selected. Mirrors the location modal's
   * country-header / region-header semantics.
   *
   * On deselect: drops the first-level ids, and also drops any of their
   * descendants currently in `selected` (which would otherwise be
   * orphan-selected with no visible ancestor). Same rule as
   * `handleToggle` for an individual family parent.
   *
   * #2978 follow-up — was previously a "select-all-children" loop that
   * recursively activated grandchildren too.
   */
  function handleDomainToggle(group: OccupationGroup) {
    const firstLevel = firstLevelItems(group);
    if (firstLevel.length === 0) return;
    const allSelected = firstLevel.every((c) => selectedIds.has(c.id));
    if (allSelected) {
      // Deselect every first-level row plus any of their descendants
      // currently in the selection (grandchildren that were selected
      // independently before the parent was committed).
      const firstLevelIds = new Set(firstLevel.map((c) => c.id));
      for (const c of firstLevel) onToggle(c);
      for (const s of selected) {
        if (firstLevelIds.has(s.id)) continue;
        // Walk parent chain — drop if any ancestor is a first-level id.
        let cur = parentMap.get(s.id);
        const seen = new Set<number>([s.id]);
        while (cur != null && !seen.has(cur)) {
          seen.add(cur);
          if (firstLevelIds.has(cur)) {
            onToggle(s);
            break;
          }
          cur = parentMap.get(cur);
        }
      }
    } else {
      firstLevel.forEach((c) => { if (!selectedIds.has(c.id)) onToggle(c); });
    }
  }

  function renderPill(item: OccupationItem) {
    const active = selectedIds.has(item.id);
    if (!active && isDisabled(item.id)) {
      return (
        <DisabledFilterPill
          key={item.id}
          name={item.name}
          count={item.count}
          ancestorName={ancestorNameOf(item.id)}
        />
      );
    }
    return (
      <button
        key={item.id}
        onClick={() => handleToggle(item)}
        className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-3 py-1 text-sm transition-colors ${
          active
            ? "bg-primary/10 text-primary"
            : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
        }`}
      >
        {item.name}
        <span className={`text-xs ${active ? "text-primary/70" : "text-muted"}`}>
          ({item.count})
        </span>
      </button>
    );
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          className="fixed left-1/2 top-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col rounded-xl border border-border-soft bg-surface shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
          aria-describedby={undefined}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-divider px-5 py-4">
            <Dialog.Title className="text-base font-semibold">
              <Trans id="search.occupationModal.title" comment="Title for the occupation selection modal">
                Select role
              </Trans>
            </Dialog.Title>
            <Dialog.Close asChild>
              <button className="rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground cursor-pointer">
                <X size={16} />
              </button>
            </Dialog.Close>
          </div>

          {/* Search */}
          <div className="border-b border-divider px-5 py-3">
            <div className="flex items-center gap-2 rounded-md border border-border-soft px-3 py-2">
              <Search size={14} className="shrink-0 text-muted" />
              <input
                type="text"
                value={search}
                onChange={(e) => { setSearch(e.target.value); setWarning(""); }}
                onKeyDown={handleSearchKeyDown}
                placeholder={t({
                  id: "search.occupationModal.searchPlaceholder",
                  comment: "Placeholder for search input in occupation modal",
                  message: "Search roles...",
                })}
                className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
              />
            </div>
            {warning && (
              <p className="mt-2 text-xs text-amber-600 dark:text-amber-400">{warning}</p>
            )}
          </div>

          {/* Body */}
          <ScrollFade wrapperClassName="flex-1 min-h-0" className="px-5 py-4">
            {loading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 size={20} className="animate-spin text-muted" />
              </div>
            ) : filtered.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted">
                <Trans id="search.occupationModal.noResults" comment="No occupations match search in occupation modal">
                  No roles match your search.
                </Trans>
              </p>
            ) : (
              <div className="space-y-5">
                {filtered.map((group) => {
                  // Domain header is "all selected" iff every first-level
                  // descendant is selected — grandchildren don't count
                  // because they're auto-disabled by the parent. Mirrors
                  // the toggle semantics in `handleDomainToggle`.
                  const firstLevel = firstLevelItems(group);
                  const allSelected = firstLevel.length > 0 && firstLevel.every((c) => selectedIds.has(c.id));

                  return (
                    <div key={group.domain.slug}>
                      {/* Domain divider header */}
                      <div className="mb-3 flex items-center gap-3">
                        <div className="h-px flex-1 bg-divider" />
                        <button
                          onClick={() => handleDomainToggle(group)}
                          className={`cursor-pointer text-xs font-semibold uppercase tracking-wider transition-colors ${
                            allSelected ? "text-primary" : "text-muted hover:text-foreground"
                          }`}
                        >
                          {group.domain.name}
                          <span className={`ml-1 text-[10px] font-normal normal-case ${allSelected ? "text-primary/70" : "text-muted"}`}>
                            ({group.domain.count})
                          </span>
                        </button>
                        <div className="h-px flex-1 bg-divider" />
                      </div>

                      {/* Sub-groups (parent + children) */}
                      {group.subGroups.map((sg) => {
                        const parentActive = selectedIds.has(sg.parent.id);
                        const parentDisabled = !parentActive && isDisabled(sg.parent.id);
                        // `sg.parent.count` already encodes the parent's
                        // subtree count (under the ancestor-expanded
                        // `occupation_ids` facet), so display it directly
                        // rather than re-summing children — summing
                        // double-counts every posting tagged at the child
                        // tier AND drops postings tagged only at the parent
                        // tier (issue #3033).
                        const totalCount = sg.parent.count;
                        return (
                          <div key={sg.parent.id} className="mb-3 rounded-lg border border-border-soft p-3">
                            {/* Parent header */}
                            {parentDisabled ? (
                              <DisabledFilterPill
                                name={sg.parent.name}
                                ancestorName={ancestorNameOf(sg.parent.id)}
                                variant="parent"
                                auxText={`(${totalCount})`}
                              />
                            ) : (
                              <button
                                onClick={() => handleToggle(sg.parent)}
                                className={`group/parent mb-1.5 cursor-pointer text-sm font-medium transition-colors ${
                                  parentActive ? "text-primary" : "text-foreground hover:text-primary"
                                }`}
                              >
                                <span className={parentActive ? "underline" : "group-hover/parent:underline"}>{sg.parent.name}</span>
                                <span className={`ml-1 text-xs font-normal ${parentActive ? "text-primary/70" : "text-muted"}`}>
                                  ({totalCount})
                                </span>
                              </button>
                            )}
                            {/* Child pills */}
                            <div className="flex flex-wrap gap-2">
                              {sg.children.map(renderPill)}
                            </div>
                          </div>
                        );
                      })}

                      {/* Standalone pills */}
                      {group.standalone.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {group.standalone.map(renderPill)}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </ScrollFade>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
