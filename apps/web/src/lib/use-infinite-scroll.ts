import { useCallback, useEffect, useRef, useState } from "react";

interface UseInfiniteScrollOptions {
  /** Whether there are more items to load */
  hasMore: boolean;
  /** Async function that fetches the next page. The hook manages the loading
   *  state and guards against concurrent calls — callers should not add their own. */
  load: () => Promise<void>;
  /** Scroll container ref. Defaults to the viewport when omitted. */
  root?: React.RefObject<Element | null>;
  /** IntersectionObserver rootMargin. @default "200px" */
  rootMargin?: string;
  /** Extra key that forces the observer to re-attach when changed.
   *  Reserved for callers that need to bust the observer for reasons
   *  unrelated to the sentinel mounting (e.g. swapping the scroll root
   *  imperatively). The hook already re-attaches when the sentinel
   *  element itself mounts/unmounts — see `sentinelCallbackRef`. */
  observerKey?: unknown;
}

/**
 * IntersectionObserver-based infinite scroll, with one footgun-fix
 * built in: the returned `sentinelRef` is a **callback ref**, not a
 * `useRef` object, so the observer re-attaches automatically when the
 * sentinel element appears in or disappears from the DOM. This matters
 * for callers that conditionally render the sentinel (e.g. a list with
 * an early `if (items.length === 0) return null` while data loads).
 *
 * The returned ref is still spelled `sentinelRef` and assignable to a
 * `ref={...}` prop just like a `useRef` object — `<div ref={sentinelRef}>`
 * works in both shapes.
 */
export function useInfiniteScroll({
  hasMore,
  load,
  root,
  rootMargin = "200px",
  observerKey,
}: UseInfiniteScrollOptions) {
  const [sentinelEl, setSentinelEl] = useState<HTMLDivElement | null>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const loadRef = useRef(load);
  const loadingRef = useRef(false);
  const [isLoading, setIsLoading] = useState(false);

  loadRef.current = load;

  // Stable callback ref — React invokes it with the DOM element on
  // mount and with `null` on unmount. Storing the element in state
  // makes the observer-attach effect re-run whenever it changes.
  const sentinelRef = useCallback((el: HTMLDivElement | null) => {
    setSentinelEl(el);
  }, []);

  const doLoad = useCallback(() => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    setIsLoading(true);

    loadRef.current()
      .then(() => {
        loadingRef.current = false;
        setIsLoading(false);
      })
      .catch(() => {
        loadingRef.current = false;
        setIsLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!sentinelEl || !hasMore) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !loadingRef.current) {
          doLoad();
        }
      },
      { root: root?.current ?? null, rootMargin },
    );

    observerRef.current = observer;
    observer.observe(sentinelEl);
    return () => {
      observer.disconnect();
      observerRef.current = null;
    };
  }, [sentinelEl, hasMore, root, rootMargin, doLoad, observerKey]);

  // After a load finishes, check whether the sentinel is still in the
  // viewport and, if so, trigger the next load. This covers a case the
  // bare IntersectionObserver misses on cold-start with the `?show=`
  // detail-panel param (#3353): the panel mounts alongside the list,
  // its async getPostingDetail fetch shifts layout once the data
  // streams in, and the IO's "re-evaluate on next paint" was not
  // re-firing the callback. Reading the sentinel's actual rect after
  // each load is deterministic regardless of layout-shift timing.
  //
  // Guarded by `prevLoadingRef`: only runs on the trailing edge of a
  // load (true → false), not on mount or on every render. Without this
  // guard, a fetcher that resolves to "no more items" (and so leaves
  // the sentinel visible until React commits hasMore=false) could be
  // re-invoked tightly.
  const prevLoadingRef = useRef(false);
  useEffect(() => {
    const justFinished = prevLoadingRef.current && !isLoading;
    prevLoadingRef.current = isLoading;
    if (!justFinished) return;
    if (!hasMore || !sentinelEl) return;
    if (loadingRef.current) return;
    const rect = sentinelEl.getBoundingClientRect();
    const rootEl = root?.current ?? null;
    const rootRect = rootEl
      ? rootEl.getBoundingClientRect()
      : { top: 0, bottom: window.innerHeight, left: 0, right: window.innerWidth };
    // Apply rootMargin as a numeric pixel value (supports "200px" form;
    // falls back to 0 if it can't be parsed).
    const margin = parseInt(rootMargin, 10) || 0;
    const inView =
      rect.top < rootRect.bottom + margin && rect.bottom > rootRect.top - margin;
    if (inView) doLoad();
  }, [isLoading, hasMore, sentinelEl, root, rootMargin, doLoad]);

  return { sentinelRef, isLoading };
}
