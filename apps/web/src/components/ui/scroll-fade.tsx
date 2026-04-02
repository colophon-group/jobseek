"use client";

import { useState, useRef, useCallback, useEffect, type ReactNode, type RefObject } from "react";

interface ScrollFadeProps {
  /** Extra classes for the scroll container (e.g. "max-h-48 w-72"). */
  className?: string;
  /** Extra classes for the outer wrapper (e.g. "flex-1 min-h-0"). */
  wrapperClassName?: string;
  children: ReactNode;
  /** Height of the fade overlay. Default: "h-4" */
  fadeHeight?: string;
  /** Re-check overflow when this value changes (e.g. list length). */
  deps?: unknown[];
  /** Expose the scroll container ref (e.g. for IntersectionObserver root). */
  scrollRef?: RefObject<HTMLDivElement | null>;
}

export function ScrollFade({ className = "", wrapperClassName = "", children, fadeHeight = "h-4", deps = [], scrollRef: externalRef }: ScrollFadeProps) {
  const internalRef = useRef<HTMLDivElement>(null);
  const scrollRef = externalRef ?? internalRef;
  const [canScrollUp, setCanScrollUp] = useState(false);
  const [canScrollDown, setCanScrollDown] = useState(false);

  const update = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollUp(el.scrollTop > 2);
    setCanScrollDown(el.scrollHeight - el.scrollTop - el.clientHeight > 2);
  }, [scrollRef]);

  useEffect(() => {
    update();
  }, [...deps, update]);

  return (
    <div className={`relative flex flex-col overflow-clip rounded-[inherit] ${wrapperClassName}`}>
      {canScrollUp && (
        <div className={`pointer-events-none absolute inset-x-0 top-0 z-10 ${fadeHeight} bg-gradient-to-b from-surface via-surface/40 to-transparent`} />
      )}
      {canScrollDown && (
        <div className={`pointer-events-none absolute inset-x-0 bottom-0 z-10 ${fadeHeight} bg-gradient-to-t from-surface via-surface/40 to-transparent`} />
      )}
      <div
        ref={scrollRef}
        className={`min-h-0 flex-1 overflow-y-auto scrollbar-hide ${className}`}
        onScroll={update}
      >
        {children}
      </div>
    </div>
  );
}
