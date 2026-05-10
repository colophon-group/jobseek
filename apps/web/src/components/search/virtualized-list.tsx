"use client";

import {
  useEffect,
  useState,
  type RefObject,
  type ReactNode,
} from "react";
import { useVirtualizer } from "@tanstack/react-virtual";

interface VirtualizedListProps<T> {
  items: readonly T[];
  getKey: (item: T, index: number) => string | number;
  render: (item: T, index: number) => ReactNode;
  estimateSize: number;
  overscan?: number;
  scrollRef: RefObject<HTMLDivElement | null>;
  className?: string;
  prelude?: ReactNode;
}

/**
 * Thin wrapper around `@tanstack/react-virtual`'s `useVirtualizer`
 * tailored for the filter-modal use case (#2982).
 *
 * - The scroll container is provided externally so we can keep the
 *   `<ScrollFade>` overlay (one source of truth for the modal body).
 * - Items have variable height — the virtualizer uses ResizeObserver via
 *   `measureElement` to read actual heights post-render.
 * - A single optional `prelude` slot lets callers render an always-
 *   visible cluster (the Regions macro chips) above the virtualized
 *   list without polluting the virtual stream's index space.
 * - When the scroll container is unmeasurable (zero clientHeight — the
 *   first render before the modal animation settles, or in JSDOM/happy-
 *   dom-based tests), the helper falls back to flat rendering.
 */
export function VirtualizedList<T>({
  items,
  getKey,
  render,
  estimateSize,
  overscan = 4,
  scrollRef,
  className = "",
  prelude,
}: VirtualizedListProps<T>) {
  const [scrollReady, setScrollReady] = useState(false);
  useEffect(() => {
    if (scrollRef.current) {
      setScrollReady(scrollRef.current.clientHeight > 0);
    }
  }, [scrollRef, items]);

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => estimateSize,
    overscan,
    enabled: scrollReady,
    getItemKey: (index) => getKey(items[index], index),
  });

  if (!scrollReady) {
    return (
      <>
        {prelude}
        <div className={className}>
          {items.map((item, i) => (
            <div key={getKey(item, i)} data-index={i}>
              {render(item, i)}
            </div>
          ))}
        </div>
      </>
    );
  }

  const total = virtualizer.getTotalSize();
  const virtualItems = virtualizer.getVirtualItems();

  return (
    <>
      {prelude}
      <div
        className={className}
        style={{
          height: `${total}px`,
          width: "100%",
          position: "relative",
        }}
      >
        {virtualItems.map((vi) => {
          const item = items[vi.index];
          return (
            <div
              key={vi.key}
              data-index={vi.index}
              ref={(el) => {
                if (el) virtualizer.measureElement(el);
              }}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${vi.start}px)`,
              }}
            >
              {render(item, vi.index)}
            </div>
          );
        })}
      </div>
    </>
  );
}
