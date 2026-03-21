import { useRef, useEffect, useState, useCallback } from "react";

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
   *  Useful when the sentinel is conditionally unmounted/remounted
   *  (e.g. behind a search transition). */
  observerKey?: unknown;
}

export function useInfiniteScroll({
  hasMore,
  load,
  root,
  rootMargin = "200px",
  observerKey,
}: UseInfiniteScrollOptions) {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const loadRef = useRef(load);
  const loadingRef = useRef(false);
  const [isLoading, setIsLoading] = useState(false);

  loadRef.current = load;

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
    const sentinel = sentinelRef.current;
    if (!sentinel || !hasMore) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !loadingRef.current) {
          doLoad();
        }
      },
      { root: root?.current ?? null, rootMargin },
    );

    observerRef.current = observer;
    observer.observe(sentinel);
    return () => {
      observer.disconnect();
      observerRef.current = null;
    };
  }, [hasMore, root, rootMargin, doLoad, observerKey]);

  // Re-observe after load finishes so the next page loads if sentinel is still visible
  useEffect(() => {
    if (isLoading) return;
    const sentinel = sentinelRef.current;
    const observer = observerRef.current;
    if (!sentinel || !observer) return;
    observer.unobserve(sentinel);
    observer.observe(sentinel);
  }, [isLoading]);

  return { sentinelRef, isLoading };
}
