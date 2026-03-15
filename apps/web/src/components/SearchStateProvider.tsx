"use client";

import {
  createContext,
  useContext,
  useRef,
  useCallback,
  type ReactNode,
} from "react";
import type { SelectedLocation } from "@/components/search/location-pills";
import type { SearchResultCompany } from "@/lib/search";

export interface SearchStateSnapshot {
  keywords: string[];
  locations: SelectedLocation[];
  companies: SearchResultCompany[];
  totalCompanies: number;
  showPostingId: string | null;
  scrollY: number;
  cacheKey: string;
}

export function buildCacheKey(
  keywords: string[],
  locationIds: number[],
): string {
  return `${[...keywords].sort().join(",")}|${[...locationIds].sort().join(",")}`;
}

/** Live callbacks the search page registers so the header SearchBar can interact directly. */
export interface SearchPageActions {
  addLocation: (location: SelectedLocation) => void;
  submitSearch: (keywords: string[], locations: SelectedLocation[]) => void;
  getLocations: () => SelectedLocation[];
  getKeywords: () => string[];
}

type SearchStateStore = {
  get: () => SearchStateSnapshot | null;
  set: (snapshot: SearchStateSnapshot) => void;
  setPageActions: (actions: SearchPageActions | null) => void;
  getPageActions: () => SearchPageActions | null;
};

const SearchStateContext = createContext<SearchStateStore>({
  get: () => null,
  set: () => {},
  setPageActions: () => {},
  getPageActions: () => null,
});

export function SearchStateProvider({ children }: { children: ReactNode }) {
  const storeRef = useRef<SearchStateSnapshot | null>(null);
  const pageActionsRef = useRef<SearchPageActions | null>(null);

  const get = useCallback(() => storeRef.current, []);
  const set = useCallback((snapshot: SearchStateSnapshot) => {
    storeRef.current = snapshot;
  }, []);
  const setPageActions = useCallback((actions: SearchPageActions | null) => {
    pageActionsRef.current = actions;
  }, []);
  const getPageActions = useCallback(() => pageActionsRef.current, []);

  const value = useRef<SearchStateStore>({ get, set, setPageActions, getPageActions }).current;

  return (
    <SearchStateContext.Provider value={value}>
      {children}
    </SearchStateContext.Provider>
  );
}

export function useSearchStateStore() {
  return useContext(SearchStateContext);
}
