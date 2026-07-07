"use client";

import { createContext, useContext, useState, useCallback, type ReactNode } from "react";

type BannerContextValue = {
  dismissedBanners: Set<string>;
  activeBanner: string | null;
  claim: (id: string) => boolean;
  dismiss: (id: string) => void;
};

const BannerContext = createContext<BannerContextValue>({
  dismissedBanners: new Set(),
  activeBanner: null,
  claim: () => false,
  dismiss: () => {},
});

export function BannerProvider({
  serverDismissed = [],
  children,
}: {
  serverDismissed?: string[];
  children: ReactNode;
}) {
  const [dismissedBanners] = useState(() => {
    const set = new Set(serverDismissed);
    if (typeof window !== "undefined") {
      // Merge localStorage keys for logged-out users
      for (const key of ["cookie-consent", "upgrade-banner-dismissed", "watchlist-tip-dismissed"]) {
        if (localStorage.getItem(key)) set.add(key);
      }
    }
    return set;
  });

  const [activeBanner, setActiveBanner] = useState<string | null>(null);

  const claim = useCallback((id: string) => {
    if (dismissedBanners.has(id)) return false;
    setActiveBanner((current) => {
      if (current === null || current === id) return id;
      return current; // another banner is already showing
    });
    return true;
  }, [dismissedBanners]);

  const dismiss = useCallback((id: string) => {
    dismissedBanners.add(id);
    setActiveBanner((current) => (current === id ? null : current));
  }, [dismissedBanners]);

  return (
    <BannerContext.Provider value={{ dismissedBanners, activeBanner, claim, dismiss }}>
      {children}
    </BannerContext.Provider>
  );
}

export function useBanner() {
  return useContext(BannerContext);
}
