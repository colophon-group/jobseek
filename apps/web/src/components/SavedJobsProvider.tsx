"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import { toggleSavedJob } from "@/lib/actions/saved-jobs";

type SavedJobsContextValue = {
  isSaved: (id: string) => boolean;
  toggle: (id: string) => void;
  isToggling: (id: string) => boolean;
};

const SavedJobsContext = createContext<SavedJobsContextValue>({
  isSaved: () => false,
  toggle: () => {},
  isToggling: () => false,
});

export function SavedJobsProvider({
  initialIds,
  children,
}: {
  initialIds: string[];
  children: ReactNode;
}) {
  const [savedIds, setSavedIds] = useState(() => new Set(initialIds));
  const [togglingIds, setTogglingIds] = useState(() => new Set<string>());
  const lockRef = useRef(new Set<string>());
  const savedIdsRef = useRef(savedIds);
  savedIdsRef.current = savedIds;

  const isSaved = useCallback((id: string) => savedIds.has(id), [savedIds]);
  const isToggling = useCallback(
    (id: string) => togglingIds.has(id),
    [togglingIds],
  );

  const toggle = useCallback((id: string) => {
    if (lockRef.current.has(id)) return;
    lockRef.current.add(id);

    const wasSaved = savedIdsRef.current.has(id);

    // Optimistic update
    setSavedIds((prev) => {
      const next = new Set(prev);
      if (wasSaved) next.delete(id);
      else next.add(id);
      return next;
    });
    setTogglingIds((prev) => new Set(prev).add(id));

    toggleSavedJob(id)
      .then((result) => {
        setSavedIds((prev) => {
          const next = new Set(prev);
          if (result.saved) next.add(id);
          else next.delete(id);
          return next;
        });
      })
      .catch(() => {
        setSavedIds((prev) => {
          const next = new Set(prev);
          if (wasSaved) next.add(id);
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
    <SavedJobsContext.Provider value={{ isSaved, toggle, isToggling }}>
      {children}
    </SavedJobsContext.Provider>
  );
}

export function useSavedJobs() {
  return useContext(SavedJobsContext);
}
