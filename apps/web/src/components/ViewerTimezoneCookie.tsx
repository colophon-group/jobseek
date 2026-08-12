"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { persistViewerTimeZoneCookie } from "@/lib/viewer-tz";

/**
 * Keeps the browser's IANA timezone available to request-time Server
 * Components without a read Server Action. On the stats route, a missing or
 * stale cookie triggers one RSC refresh after the direct browser cookie write
 * so the heatmap is never intentionally left bucketed in a different zone.
 */
export function ViewerTimezoneCookie({
  serverTimeZone,
  refreshWhenChanged = false,
}: {
  serverTimeZone?: string | null;
  refreshWhenChanged?: boolean;
}) {
  const router = useRouter();
  const refreshRequested = useRef(false);

  useEffect(() => {
    const browserTimeZone = persistViewerTimeZoneCookie();
    if (
      refreshWhenChanged &&
      browserTimeZone !== serverTimeZone &&
      !refreshRequested.current
    ) {
      refreshRequested.current = true;
      router.refresh();
    }
  }, [refreshWhenChanged, router, serverTimeZone]);

  return null;
}
