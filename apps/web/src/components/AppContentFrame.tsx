"use client";

import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";

/** Keeps fixed detail panes below whichever alerts are currently visible. */
export function AppContentFrame({ alerts, children }: { alerts: ReactNode; children: ReactNode }) {
  const alertsRef = useRef<HTMLDivElement>(null);
  const [alertHeight, setAlertHeight] = useState(0);

  useLayoutEffect(() => {
    const element = alertsRef.current;
    if (!element) return;

    const measure = () => setAlertHeight(element.getBoundingClientRect().height);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      className="flex min-h-0 flex-1 flex-col md:pt-12"
      style={{ "--app-alert-height": `${alertHeight}px` } as CSSProperties}
    >
      <div ref={alertsRef} className="md:sticky md:top-12 md:z-40">
        {alerts}
      </div>
      {children}
    </div>
  );
}
