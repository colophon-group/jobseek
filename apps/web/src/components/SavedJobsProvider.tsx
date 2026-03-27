"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from "react";
import { toggleSavedJob } from "@/lib/actions/saved-jobs";
import type { SavedJobStatus } from "@/lib/actions/saved-jobs";

type SavedJobInfo = { savedJobId: string; status: string };

type StatusChangeListener = (postingId: string, status: string) => void;

type SavedJobsContextValue = {
  isSaved: (id: string) => boolean;
  getStatus: (id: string) => string | null;
  getSavedJobId: (postingId: string) => string | null;
  setStatus: (postingId: string, status: string) => void;
  toggle: (id: string) => void;
  isToggling: (id: string) => boolean;
  onStatusChange: (listener: StatusChangeListener) => () => void;
};

const SavedJobsContext = createContext<SavedJobsContextValue>({
  isSaved: () => false,
  getStatus: () => null,
  getSavedJobId: () => null,
  setStatus: () => {},
  toggle: () => {},
  isToggling: () => false,
  onStatusChange: () => () => {},
});

export function SavedJobsProvider({
  initialStatuses = [],
  children,
}: {
  initialStatuses?: SavedJobStatus[];
  children: ReactNode;
}) {
  const [infoMap, setInfoMap] = useState(
    () => new Map(initialStatuses.map((s) => [s.postingId, { savedJobId: s.savedJobId, status: s.status } as SavedJobInfo])),
  );
  // Sync when bootstrap data arrives (initialStatuses starts empty, then fills)
  useEffect(() => {
    if (initialStatuses.length === 0) return;
    setInfoMap(
      new Map(initialStatuses.map((s) => [s.postingId, { savedJobId: s.savedJobId, status: s.status }])),
    );
  }, [initialStatuses]);
  const [togglingIds, setTogglingIds] = useState(() => new Set<string>());
  const lockRef = useRef(new Set<string>());
  const infoMapRef = useRef(infoMap);
  infoMapRef.current = infoMap;

  const isSaved = useCallback((id: string) => infoMap.has(id), [infoMap]);
  const getStatus = useCallback(
    (id: string) => infoMap.get(id)?.status ?? null,
    [infoMap],
  );
  const getSavedJobId = useCallback(
    (postingId: string) => infoMap.get(postingId)?.savedJobId ?? null,
    [infoMap],
  );
  const listenersRef = useRef(new Set<StatusChangeListener>());
  const onStatusChange = useCallback((listener: StatusChangeListener) => {
    listenersRef.current.add(listener);
    return () => { listenersRef.current.delete(listener); };
  }, []);

  const setStatus = useCallback(
    (postingId: string, status: string) => {
      setInfoMap((prev) => {
        const info = prev.get(postingId);
        if (!info) return prev;
        const next = new Map(prev);
        next.set(postingId, { ...info, status });
        return next;
      });
      listenersRef.current.forEach((fn) => fn(postingId, status));
    },
    [],
  );
  const isToggling = useCallback(
    (id: string) => togglingIds.has(id),
    [togglingIds],
  );

  const toggle = useCallback((id: string) => {
    if (lockRef.current.has(id)) return;
    lockRef.current.add(id);

    const prevInfo = infoMapRef.current.get(id);
    const wasSaved = !!prevInfo;

    // Optimistic update
    setInfoMap((prev) => {
      const next = new Map(prev);
      if (wasSaved) next.delete(id);
      else next.set(id, { savedJobId: "", status: "saved" });
      return next;
    });
    setTogglingIds((prev) => new Set(prev).add(id));

    toggleSavedJob(id)
      .then((result) => {
        setInfoMap((prev) => {
          const next = new Map(prev);
          if (result.saved) {
            next.set(id, { savedJobId: result.savedJobId ?? "", status: "saved" });
          } else {
            next.delete(id);
          }
          return next;
        });
      })
      .catch(() => {
        setInfoMap((prev) => {
          const next = new Map(prev);
          if (wasSaved && prevInfo) next.set(id, prevInfo);
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
    <SavedJobsContext.Provider value={{ isSaved, getStatus, getSavedJobId, setStatus, toggle, isToggling, onStatusChange }}>
      {children}
    </SavedJobsContext.Provider>
  );
}

export function useSavedJobs() {
  return useContext(SavedJobsContext);
}
