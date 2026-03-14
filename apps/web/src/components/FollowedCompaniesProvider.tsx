"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import { toggleFollowedCompany } from "@/lib/actions/followed-companies";

type FollowedCompaniesContextValue = {
  isFollowed: (id: string) => boolean;
  toggle: (id: string) => void;
  isToggling: (id: string) => boolean;
};

const FollowedCompaniesContext = createContext<FollowedCompaniesContextValue>({
  isFollowed: () => false,
  toggle: () => {},
  isToggling: () => false,
});

export function FollowedCompaniesProvider({
  initialIds,
  children,
}: {
  initialIds: string[];
  children: ReactNode;
}) {
  const [followedIds, setFollowedIds] = useState(() => new Set(initialIds));
  const [togglingIds, setTogglingIds] = useState(() => new Set<string>());
  const lockRef = useRef(new Set<string>());
  const followedIdsRef = useRef(followedIds);
  followedIdsRef.current = followedIds;

  const isFollowed = useCallback((id: string) => followedIds.has(id), [followedIds]);
  const isToggling = useCallback(
    (id: string) => togglingIds.has(id),
    [togglingIds],
  );

  const toggle = useCallback((id: string) => {
    if (lockRef.current.has(id)) return;
    lockRef.current.add(id);

    const wasFollowed = followedIdsRef.current.has(id);

    // Optimistic update
    setFollowedIds((prev) => {
      const next = new Set(prev);
      if (wasFollowed) next.delete(id);
      else next.add(id);
      return next;
    });
    setTogglingIds((prev) => new Set(prev).add(id));

    toggleFollowedCompany(id)
      .then((result) => {
        setFollowedIds((prev) => {
          const next = new Set(prev);
          if (result.followed) next.add(id);
          else next.delete(id);
          return next;
        });
      })
      .catch(() => {
        setFollowedIds((prev) => {
          const next = new Set(prev);
          if (wasFollowed) next.add(id);
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
    <FollowedCompaniesContext.Provider value={{ isFollowed, toggle, isToggling }}>
      {children}
    </FollowedCompaniesContext.Provider>
  );
}

export function useFollowedCompanies() {
  return useContext(FollowedCompaniesContext);
}
