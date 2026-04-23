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
import type { SearchResultCompany } from "@/lib/search";

export interface SearchStateSnapshot {
  keywords: string[];
  excludeTitles: string[];
  locations: SelectedLocation[];
  occupations: { id: number; slug: string; name: string }[];
  seniorities: { id: number; slug: string; name: string }[];
  technologies: { id: number; slug: string; name: string }[];
  salaryMinEur: number | undefined;
  salaryMaxEur: number | undefined;
  salaryCurrency: string;
  experienceMin: number | undefined;
  experienceMax: number | undefined;
  companies: SearchResultCompany[];
  totalCompanies: number;
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
  excludeTitles?: string[],
): string {
  const parts = [
    [...keywords].sort().join(","),
    [...locationIds].sort().join(","),
    [...(occupationIds ?? [])].sort().join(","),
    [...(seniorityIds ?? [])].sort().join(","),
    [...(technologyIds ?? [])].sort().join(","),
    [...(excludeTitles ?? [])].map((s) => s.toLowerCase()).sort().join(","),
  ];
  return parts.join("|");
}

/** Live callbacks the search page registers so the header SearchBar can interact directly. */
export interface SearchPageActions {
  addLocation: (location: SelectedLocation) => void;
  addOccupation: (occupation: { id: number; slug: string; name: string }) => void;
  addSeniority: (seniority: { id: number; slug: string; name: string }) => void;
  addTechnology?: (technology: { id: number; slug: string; name: string }) => void;
  addEmploymentType?: (type: string) => void;
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
