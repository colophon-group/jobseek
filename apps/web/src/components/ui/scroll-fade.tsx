"use client";

import { useState, useRef, useCallback, useEffect, type ReactNode, type RefObject } from "react";

type Direction = "vertical" | "horizontal";

interface ScrollFadeProps {
  /** Extra classes for the scroll container (e.g. "max-h-48 w-72" or "gap-2 py-2"). */
  className?: string;
  /** Extra classes for the outer wrapper (e.g. "flex-1 min-h-0"). */
  wrapperClassName?: string;
  children: ReactNode;
  /**
   * Scroll axis. Defaults to "vertical" for backward compatibility with the
   * existing callers (company card, settings modals, job detail panel).
   */
  direction?: Direction;
  /**
   * Fade overlay size along the scroll axis.
   *   vertical   → height (e.g. "h-4")
   *   horizontal → width  (e.g. "w-8")
   * Default: "h-4" / "w-8" depending on direction.
   */
  fadeSize?: string;
  /** Re-check overflow when this value changes (e.g. list length). */
  deps?: unknown[];
  /** Expose the scroll container ref (e.g. for IntersectionObserver root). */
  scrollRef?: RefObject<HTMLDivElement | null>;
}

export function ScrollFade({
  className = "",
  wrapperClassName = "",
  children,
  direction = "vertical",
  fadeSize,
  deps = [],
  scrollRef: externalRef,
}: ScrollFadeProps) {
  const internalRef = useRef<HTMLDivElement>(null);
  const scrollRef = externalRef ?? internalRef;
  const [canScrollStart, setCanScrollStart] = useState(false);
  const [canScrollEnd, setCanScrollEnd] = useState(false);

  const isHorizontal = direction === "horizontal";

  const update = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (isHorizontal) {
      setCanScrollStart(el.scrollLeft > 2);
      setCanScrollEnd(el.scrollWidth - el.scrollLeft - el.clientWidth > 2);
    } else {
      setCanScrollStart(el.scrollTop > 2);
      setCanScrollEnd(el.scrollHeight - el.scrollTop - el.clientHeight > 2);
    }
  }, [scrollRef, isHorizontal]);

  useEffect(() => {
    update();
  }, [...deps, update]);

  const resolvedFadeSize = fadeSize ?? (isHorizontal ? "w-8" : "h-4");

  if (isHorizontal) {
    return (
      <div className={`relative flex overflow-hidden rounded-[inherit] ${wrapperClassName}`}>
        {canScrollStart && (
          <div
            className={`pointer-events-none absolute inset-y-0 left-0 z-10 ${resolvedFadeSize} bg-gradient-to-r from-surface via-surface/40 to-transparent`}
          />
        )}
        {canScrollEnd && (
          <div
            className={`pointer-events-none absolute inset-y-0 right-0 z-10 ${resolvedFadeSize} bg-gradient-to-l from-surface via-surface/40 to-transparent`}
          />
        )}
        <div
          ref={scrollRef}
          className={`min-w-0 flex-1 overflow-x-auto scrollbar-hide ${className}`}
          onScroll={update}
        >
          {children}
        </div>
      </div>
    );
  }

  return (
    <div className={`relative flex flex-col overflow-hidden rounded-[inherit] ${wrapperClassName}`}>
      {canScrollStart && (
        <div
          className={`pointer-events-none absolute inset-x-0 top-0 z-10 ${resolvedFadeSize} bg-gradient-to-b from-surface via-surface/40 to-transparent`}
        />
      )}
      {canScrollEnd && (
        <div
          className={`pointer-events-none absolute inset-x-0 bottom-0 z-10 ${resolvedFadeSize} bg-gradient-to-t from-surface via-surface/40 to-transparent`}
        />
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
