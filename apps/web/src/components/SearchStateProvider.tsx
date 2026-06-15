"use client";

import {
  createContext,
  useContext,
  useRef,
  useCallback,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import type { SelectedLocation } from "@/components/search/location-pills";
import type { SearchResultCompany, WorkMode } from "@/lib/search";
import { canonicalStringCompare } from "@/lib/sort";

export interface SearchStateSnapshot {
  keywords: string[];
  locations: SelectedLocation[];
  occupations: { id: number; slug: string; name: string }[];
  seniorities: { id: number; slug: string; name: string }[];
  technologies: { id: number; slug: string; name: string }[];
  workMode: WorkMode[];
  salaryMinEur: number | undefined;
  salaryMaxEur: number | undefined;
  salaryCurrency: string;
  experienceMin: number | undefined;
  experienceMax: number | undefined;
  companies: SearchResultCompany[];
  totalCompanies: number;
  degraded?: boolean;
  showPostingId: string | null;
  scrollY: number;
  cacheKey: string;
}

export function buildCacheKey(
  keywords: string[],
  locationIds: number[],
  occupationIds?: number[],
  seniorityIds?: number[],
  technologyIds?: number[],
): string {
  // String dimensions (keywords) sort with `canonicalStringCompare`
  // (locale-independent `Intl.Collator("en", { sensitivity: "base" })`)
  // so that `["python","übung"]` and `["übung","python"]` hash to the
  // same in-memory snapshot key. Numeric dimensions sort numerically so
  // `[10, 2]` doesn't coerce to `["10","2"]` and split the slot.
  // See #3276 (follow-up to #3221/#3187).
  const numCmp = (a: number, b: number) => a - b;
  const parts = [
    [...keywords].sort(canonicalStringCompare).join(","),
    [...locationIds].sort(numCmp).join(","),
    [...(occupationIds ?? [])].sort(numCmp).join(","),
    [...(seniorityIds ?? [])].sort(numCmp).join(","),
    [...(technologyIds ?? [])].sort(numCmp).join(","),
  ];
  return parts.join("|");
}

/**
 * Decide whether the cached SearchStateProvider snapshot should hydrate
 * the SearchPage that's about to mount.
 *
 * Returns ``true`` only when the snapshot's cache key matches the URL-
 * derived cache key. The previous predicate also restored whenever the
 * URL had no filters, which let a snapshot from a previous filtered
 * search — including ``companies: []`` from an empty-result search —
 * leak into a fresh ``/explore`` visit. The user then saw
 * ``ZeroResults`` even though the URL had no filters and the
 * prerendered ``initialData`` had ~10 top companies. See #2989.
 *
 * Additional guard (#3354): never restore a snapshot whose
 * ``companies`` is empty AND whose ``cacheKey`` represents the no-
 * filter view (``||||``). The snapshot only serves the
 * "return to the same view after a posting-detail dive" use case — but
 * an empty unfiltered result is never a legitimate destination (the
 * homepage always has top companies). The empty state arises only from
 * transient Typesense / cache degradation poisoning the snapshot, and
 * restoring it traps the user on a permanently empty page even after
 * the back end recovers, because the fresh prerendered top-10 in
 * ``initialCompanies`` is overridden by the empty snapshot.
 *
 * Restoration semantics now:
 *   - Same URL filters (or both empty), snapshot has results → restore.
 *   - Same URL filters (or both empty), snapshot has empty companies
 *     AND the no-filter cacheKey → ignore (#3354 poison guard).
 *   - Different URL filters → ignore the snapshot, render the fresh
 *     ``initialData`` from the server prerender / re-fetch.
 *
 * The strict match preserves the original intent of the cache (return
 * to the same view after a posting-detail dive) without the poisoning
 * footgun.
 */
const NO_FILTER_CACHE_KEY = "||||";

export function shouldRestoreSnapshot(
  cached: SearchStateSnapshot | null,
  currentCacheKey: string,
): boolean {
  if (cached === null) return false;
  if (cached.cacheKey !== currentCacheKey) return false;
  // #3354 poison guard: an unfiltered snapshot with 0 companies is
  // always a degraded prior state (Typesense glitch / silent failure)
  // and never a legitimate "saved view" worth restoring. Reject it so
  // the fresh ``initialCompanies`` from the server prerender / refetch
  // can render. Filtered empty snapshots stay restorable because a
  // 0-result keyword search IS a legitimate destination the user may
  // want to return to.
  if (
    cached.cacheKey === NO_FILTER_CACHE_KEY &&
    cached.companies.length === 0
  ) {
    return false;
  }
  return true;
}

/** Live callbacks the search page registers so the header SearchBar can interact directly. */
export interface SearchPageActions {
  addLocation: (location: SelectedLocation) => void;
  addOccupation: (occupation: { id: number; slug: string; name: string }) => void;
  addSeniority: (seniority: { id: number; slug: string; name: string }) => void;
  addTechnology?: (technology: { id: number; slug: string; name: string }) => void;
  addEmploymentType?: (type: string) => void;
  /**
   * Add a work-mode (onsite/hybrid/remote) to the active filter set.
   * Idempotent — implementations should no-op when the mode is already
   * selected. Used by the global search-bar autocomplete (#2983).
   */
  addWorkMode?: (mode: WorkMode) => void;
  setSalaryFilter?: (currency: string, min: number | undefined, max: number | undefined) => void;
  setExperienceFilter?: (min: number | undefined, max: number | undefined) => void;
  submitSearch: (
    keywords: string[],
    locations: SelectedLocation[],
    occupations?: { id: number; slug: string; name: string }[],
    seniorities?: { id: number; slug: string; name: string }[],
    technologies?: { id: number; slug: string; name: string }[],
  ) => void;
  getLocations: () => SelectedLocation[];
  getKeywords: () => string[];
  getOccupations: () => { id: number; slug: string; name: string }[];
  getSeniorities: () => { id: number; slug: string; name: string }[];
  getTechnologies?: () => { id: number; slug: string; name: string }[];
  /** Custom placeholder for the header SearchBar (e.g. "Search at Google...") */
  placeholder?: string;
}

type SearchStateStore = {
  get: () => SearchStateSnapshot | null;
  set: (snapshot: SearchStateSnapshot) => void;
  setPageActions: (actions: SearchPageActions | null) => void;
  getPageActions: () => SearchPageActions | null;
  subscribePageActions: (cb: () => void) => () => void;
};

const SearchStateContext = createContext<SearchStateStore>({
  get: () => null,
  set: () => {},
  setPageActions: () => {},
  getPageActions: () => null,
  subscribePageActions: () => () => {},
});

export function SearchStateProvider({ children }: { children: ReactNode }) {
  const storeRef = useRef<SearchStateSnapshot | null>(null);
  const pageActionsRef = useRef<SearchPageActions | null>(null);
  const pageActionsListenersRef = useRef(new Set<() => void>());

  const get = useCallback(() => storeRef.current, []);
  const set = useCallback((snapshot: SearchStateSnapshot) => {
    storeRef.current = snapshot;
  }, []);
  const setPageActions = useCallback((actions: SearchPageActions | null) => {
    pageActionsRef.current = actions;
    pageActionsListenersRef.current.forEach((fn) => fn());
  }, []);
  const getPageActions = useCallback(() => pageActionsRef.current, []);
  const subscribePageActions = useCallback((cb: () => void) => {
    pageActionsListenersRef.current.add(cb);
    return () => { pageActionsListenersRef.current.delete(cb); };
  }, []);

  const value = useRef<SearchStateStore>({ get, set, setPageActions, getPageActions, subscribePageActions }).current;

  return (
    <SearchStateContext.Provider value={value}>
      {children}
    </SearchStateContext.Provider>
  );
}

export function useSearchStateStore() {
  return useContext(SearchStateContext);
}

const nullPageActions = () => null;

/** Reactively subscribe to pageActions changes (triggers re-render when actions change). */
export function usePageActions(): SearchPageActions | null {
  const { subscribePageActions, getPageActions } = useSearchStateStore();
  return useSyncExternalStore(subscribePageActions, getPageActions, nullPageActions);
}
