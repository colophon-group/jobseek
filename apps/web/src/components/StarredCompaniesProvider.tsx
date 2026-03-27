"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  useMemo,
  type ReactNode,
} from "react";
import { toggleStarredCompany } from "@/lib/actions/starred-companies";

type StarredCompaniesContextValue = {
  isStarred: (id: string) => boolean;
  toggle: (id: string) => void;
  isToggling: (id: string) => boolean;
  starredIds: string[];
};

const StarredCompaniesContext = createContext<StarredCompaniesContextValue>({
  isStarred: () => false,
  toggle: () => {},
  isToggling: () => false,
  starredIds: [],
});

export function StarredCompaniesProvider({
  initialIds = [],
  children,
}: {
  initialIds?: string[];
  children: ReactNode;
}) {
  const [starredIdSet, setStarredIdSet] = useState(() => new Set(initialIds));
  // Sync when bootstrap data arrives (initialIds starts empty, then fills)
  useEffect(() => {
    if (initialIds.length === 0) return;
    setStarredIdSet(new Set(initialIds));
  }, [initialIds]);
  const [togglingIds, setTogglingIds] = useState(() => new Set<string>());
  const lockRef = useRef(new Set<string>());
  const starredIdSetRef = useRef(starredIdSet);
  starredIdSetRef.current = starredIdSet;

  const isStarred = useCallback((id: string) => starredIdSet.has(id), [starredIdSet]);
  const isToggling = useCallback(
    (id: string) => togglingIds.has(id),
    [togglingIds],
  );

  const starredIds = useMemo(() => [...starredIdSet], [starredIdSet]);

  const toggle = useCallback((id: string) => {
    if (lockRef.current.has(id)) return;
    lockRef.current.add(id);

    const wasStarred = starredIdSetRef.current.has(id);

    // Optimistic update
    setStarredIdSet((prev) => {
      const next = new Set(prev);
      if (wasStarred) next.delete(id);
      else next.add(id);
      return next;
    });
    setTogglingIds((prev) => new Set(prev).add(id));

    toggleStarredCompany(id)
      .then((result) => {
        setStarredIdSet((prev) => {
          const next = new Set(prev);
          if (result.starred) next.add(id);
          else next.delete(id);
          return next;
        });
      })
      .catch(() => {
        setStarredIdSet((prev) => {
          const next = new Set(prev);
          if (wasStarred) next.add(id);
          else next.delete(id);
          return next;
        });
      })
      .finally(() => {
        lockRef.current.delete(id);
        setTogglingIds((prev) => {
          const next = new Set(prev);
          next.delete(id);
          return next;
        });
      });
  }, []);

  return (
    <StarredCompaniesContext.Provider
      value={{
        isStarred,
        toggle,
        isToggling,
        starredIds,
      }}
    >
      {children}
    </StarredCompaniesContext.Provider>
  );
}

export function useStarredCompanies() {
  return useContext(StarredCompaniesContext);
}
