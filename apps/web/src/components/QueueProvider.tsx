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
import { addToQueue, removeFromQueue } from "@/lib/actions/queue";

type QueueItemInfo = { queueId: string; analyzed: boolean };

type QueueCheckStatus = { postingId: string; queued: boolean; queueId?: string; analyzed: boolean };

type QueueContextValue = {
  isQueued: (postingId: string) => boolean;
  getQueueId: (postingId: string) => string | null;
  getAnalyzed: (postingId: string) => boolean;
  toggle: (postingId: string) => void;
  isToggling: (postingId: string) => boolean;
};

const QueueContext = createContext<QueueContextValue>({
  isQueued: () => false,
  getQueueId: () => null,
  getAnalyzed: () => false,
  toggle: () => {},
  isToggling: () => false,
});

export function QueueProvider({
  initialStatuses = [],
  children,
}: {
  initialStatuses?: QueueCheckStatus[];
  children: ReactNode;
}) {
  const [infoMap, setInfoMap] = useState(
    () => new Map(
      initialStatuses
        .filter((s) => s.queued && s.queueId)
        .map((s) => [s.postingId, { queueId: s.queueId!, analyzed: s.analyzed } as QueueItemInfo])
    ),
  );

  useEffect(() => {
    if (initialStatuses.length === 0) return;
    setInfoMap(
      new Map(
        initialStatuses
          .filter((s) => s.queued && s.queueId)
          .map((s) => [s.postingId, { queueId: s.queueId!, analyzed: s.analyzed }])
      ),
    );
  }, [initialStatuses]);

  const [togglingIds, setTogglingIds] = useState(() => new Set<string>());
  const lockRef = useRef(new Set<string>());
  const infoMapRef = useRef(infoMap);
  infoMapRef.current = infoMap;

  const isQueued = useCallback(
    (postingId: string) => infoMap.has(postingId),
    [infoMap],
  );

  const getQueueId = useCallback(
    (postingId: string) => infoMap.get(postingId)?.queueId ?? null,
    [infoMap],
  );

  const getAnalyzed = useCallback(
    (postingId: string) => infoMap.get(postingId)?.analyzed ?? false,
    [infoMap],
  );

  const isToggling = useCallback(
    (postingId: string) => togglingIds.has(postingId),
    [togglingIds],
  );

  const toggle = useCallback((postingId: string) => {
    if (lockRef.current.has(postingId)) return;
    lockRef.current.add(postingId);

    const prevInfo = infoMapRef.current.get(postingId);
    const wasQueued = !!prevInfo;

    // Optimistic update
    setInfoMap((prev) => {
      const next = new Map(prev);
      if (wasQueued) next.delete(postingId);
      else next.set(postingId, { queueId: "", analyzed: false });
      return next;
    });
    setTogglingIds((prev) => new Set(prev).add(postingId));

    (wasQueued && prevInfo
      ? removeFromQueue(prevInfo.queueId)
      : addToQueue(postingId)
    )
      .then((result) => {
        setInfoMap((prev) => {
          const next = new Map(prev);
          if (!wasQueued && "queueId" in result && result.queueId) {
            next.set(postingId, { queueId: result.queueId, analyzed: false });
          } else if (wasQueued) {
            next.delete(postingId);
          }
          return next;
        });
      })
      .catch(() => {
        setInfoMap((prev) => {
          const next = new Map(prev);
          if (wasQueued && prevInfo) next.set(postingId, prevInfo);
          else next.delete(postingId);
          return next;
        });
      })
      .finally(() => {
        lockRef.current.delete(postingId);
        setTogglingIds((prev) => {
          const next = new Set(prev);
          next.delete(postingId);
          return next;
        });
      });
  }, []);

  return (
    <QueueContext.Provider value={{ isQueued, getQueueId, getAnalyzed, toggle, isToggling }}>
      {children}
    </QueueContext.Provider>
  );
}

export function useQueue() {
  return useContext(QueueContext);
}
