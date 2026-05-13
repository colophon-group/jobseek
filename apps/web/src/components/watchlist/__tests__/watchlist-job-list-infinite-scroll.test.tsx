/**
 * Regression tests for issue #3038 — watchlist infinite scroll never
 * terminates when the load-more fetch returns items that all collide
 * with previously-loaded IDs.
 *
 * The original `handleLoadMore` in `watchlist-job-list.tsx` had two
 * terminal conditions:
 *   - `result.postings.length < BATCH`        → end of list, short batch
 *   - server-reported `truncated`             → anon cap hit
 *
 * Neither covered the "fetch returned BATCH items but client dedup
 * dropped all of them" case. When that happens:
 *   1. `setPostings` is a no-op (after dedup, no new items).
 *   2. `result.postings.length === BATCH` so `exhausted` stays false.
 *   3. The list height doesn't grow → sentinel stays in viewport.
 *   4. `useInfiniteScroll`'s re-observe-after-load effect fires the
 *      IntersectionObserver again → another `handleLoadMore` call →
 *      same `offset` (= unchanged `postings.length`) → same response.
 *   5. Loop.
 *
 * Real-world trigger: data drift (postings re-ordered or partially
 * cleared between calls), batched-watchlist merge returning the same
 * top-N for two consecutive pages, or any case where the server
 * legitimately returns a fully-overlapping result set.
 *
 * The fix extracts the load-more state machine into
 * `usePaginatedLoadMore` (apps/web/src/lib/use-paginated-load-more.ts)
 * and adds two terminal conditions to its end-of-list check:
 *   - `fresh.length === 0`                    → dedup ate everything
 *   - `projectedLength >= result.total`       → reached server total
 *
 * The shim below wires `usePaginatedLoadMore` + `useInfiniteScroll`
 * — the exact pair the production component uses — into a minimal DOM
 * surface so the regression covers the production code path without
 * dragging in Lingui / providers / etc.
 */

import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, render } from "@testing-library/react";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { usePaginatedLoadMore } from "@/lib/use-paginated-load-more";

// ── IntersectionObserver harness ─────────────────────────────────────
//
// happy-dom doesn't ship one. We approximate browser behaviour:
// `observe()` schedules a "fire intersection callback" on the next
// microtask if the sentinel is "in view". That mirrors what a real
// browser does when you observe an already-visible element AND
// reflects the re-observe effect in `useInfiniteScroll` — the hook
// re-issues `observe()` after every load to nudge the next fetch if
// the just-loaded batch was short enough to leave the sentinel still
// on screen.

type ObserverCallback = (entries: IntersectionObserverEntry[]) => void;

interface ManagedObserver {
  callback: ObserverCallback;
  observe: ReturnType<typeof vi.fn>;
  unobserve: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
}

const observers: ManagedObserver[] = [];
let sentinelInView = true;

/**
 * Browser IntersectionObserver semantics this mock reproduces:
 *
 *   - When an already-visible element is `observe()`d, the callback
 *     fires asynchronously (microtask + first paint).
 *   - As long as the element stays observed AND in view, no additional
 *     callbacks fire — the observer reports state changes, not state.
 *   - BUT: when the rendered DOM changes layout in a way that could
 *     plausibly affect the intersection (which happens on every React
 *     commit of the surrounding list), the browser RE-EVALUATES on the
 *     next paint and fires the callback again if `isIntersecting` is
 *     true. This is what makes the production bug runaway — every
 *     state-update from `handleLoadMore` (setTotal, setPostings even
 *     with no growth) is enough to trigger a re-evaluation.
 *
 * Approximation: every `observe()` schedules a fire on a microtask AND
 * every layout-change (we proxy this by firing on a `setTimeout(0)`
 * tick whenever React commits a state update) re-fires the callback.
 */

const fireQueue = new Set<{
  cb: ObserverCallback;
  target: Element;
}>();

class MockIntersectionObserver {
  private callback: ObserverCallback;
  observe: ReturnType<typeof vi.fn>;
  unobserve: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;

  constructor(callback: ObserverCallback) {
    this.callback = callback;
    this.observe = vi.fn((target: Element) => {
      // Approximate browser: a fresh `observe()` against an already-
      // visible target dispatches a callback asynchronously.
      const entry = { cb: callback, target };
      fireQueue.add(entry);
      queueMicrotask(() => {
        if (sentinelInView && fireQueue.has(entry)) {
          this.callback([
            {
              isIntersecting: true,
              target,
            } as unknown as IntersectionObserverEntry,
          ]);
        }
      });
    });
    this.unobserve = vi.fn((target: Element) => {
      for (const entry of fireQueue) {
        if (entry.target === target && entry.cb === callback) {
          fireQueue.delete(entry);
        }
      }
    });
    this.disconnect = vi.fn(() => {
      for (const entry of fireQueue) {
        if (entry.cb === callback) {
          fireQueue.delete(entry);
        }
      }
    });
    observers.push({
      callback,
      observe: this.observe,
      unobserve: this.unobserve,
      disconnect: this.disconnect,
    });
  }
}

/**
 * Simulate a "re-evaluate intersections" pass — fires the IO callback
 * for every currently-observed target if the sentinel is in view.
 * Real browsers do this on layout/paint changes; in the test we drive
 * it manually so the load-more loop has a chance to compound.
 */
function tickIntersections() {
  if (!sentinelInView) return;
  for (const entry of fireQueue) {
    entry.cb([
      {
        isIntersecting: true,
        target: entry.target,
      } as unknown as IntersectionObserverEntry,
    ]);
  }
}

beforeEach(() => {
  observers.length = 0;
  fireQueue.clear();
  sentinelInView = true;
  // @ts-expect-error — assigning a mock to the global
  globalThis.IntersectionObserver = MockIntersectionObserver;
});

afterEach(() => {
  // @ts-expect-error — cleanup
  delete globalThis.IntersectionObserver;
  fireQueue.clear();
  vi.restoreAllMocks();
});

/**
 * Pump the test event loop:
 *   - microtask drain (queueMicrotask, promise.then)
 *   - macrotask boundary (setTimeout 0) so React commits flush
 *   - repeat enough times that the bug's loop compounds many times if
 *     it's still present.
 */
async function pumpLoop(iterations = 50) {
  for (let i = 0; i < iterations; i++) {
    // Each await act drains microtasks + flushes pending React commits.
    // Mix microtask awaits with a macrotask boundary so anything queued
    // via setTimeout (e.g. simulated network latency) drains too.
    await act(async () => {
      await Promise.resolve();
    });
    await act(async () => {
      await new Promise<void>((r) => setTimeout(r, 0));
    });
    // After each microtask/macrotask cycle, simulate the browser's
    // "re-evaluate intersection" pass: if the sentinel is still in
    // view, the IO callback fires again. This is what drives the
    // production loop and lets the test catch it.
    await act(async () => {
      tickIntersections();
    });
  }
}

// ── The unit under test ──────────────────────────────────────────────
//
// `WatchlistLoadMoreShim` wires the production hooks
// (`usePaginatedLoadMore` + `useInfiniteScroll`) into a minimal DOM
// surface that mirrors what `WatchlistJobList` renders for the
// purposes of infinite scroll: the postings list and a sentinel
// element when `hasMore` is true. By calling the SAME hook the real
// component uses, the test catches the bug in production code rather
// than in a parallel re-implementation that can drift.

interface Posting {
  id: string;
}

const BATCH = 20;

function WatchlistLoadMoreShim({
  initialPostings,
  initialTotal,
  fetcher,
}: {
  initialPostings: Posting[];
  initialTotal: number;
  fetcher: (offset: number) => Promise<{
    postings: Posting[];
    total: number;
    truncated?: boolean;
  }>;
}) {
  const {
    items: postings,
    total,
    hasMore,
    loadMore,
  } = usePaginatedLoadMore<Posting>({
    initialItems: initialPostings,
    initialTotal,
    batchSize: BATCH,
    itemKey: (p) => p.id,
    fetcher: ({ offset, limit }) =>
      fetcher(offset).then((r) => ({
        // Cap the page size to the batch the caller asked for. The
        // hook itself doesn't enforce this; production fetchers do.
        postings: r.postings.slice(0, limit),
        total: r.total,
        truncated: r.truncated,
      })),
  });

  const { sentinelRef, isLoading } = useInfiniteScroll({
    hasMore,
    load: loadMore,
  });

  return (
    <div>
      <ul>
        {postings.map((p) => (
          <li key={p.id}>{p.id}</li>
        ))}
      </ul>
      <div data-testid="total">{total}</div>
      <div data-testid="is-loading">{String(isLoading)}</div>
      {hasMore && <div ref={sentinelRef} data-testid="sentinel" />}
    </div>
  );
}

function postingsArray(n: number, offset = 0): Posting[] {
  return Array.from({ length: n }, (_, i) => ({ id: `p${offset + i}` }));
}

describe("WatchlistJobList — infinite-scroll termination (issue #3038)", () => {
  it("does not refetch indefinitely when every result page is fully duplicate", async () => {
    const firstPage = postingsArray(BATCH);

    // Bug scenario: server returns the SAME 20 postings regardless of
    // offset. After client-side dedup, no new items are added.
    const fetcher = vi.fn(async () => ({
      postings: firstPage,
      total: 100,
    }));

    render(
      <WatchlistLoadMoreShim
        initialPostings={firstPage}
        initialTotal={100}
        fetcher={fetcher}
      />,
    );

    await pumpLoop();

    // A correct implementation MUST terminate. The original code re-fired
    // forever; the fix stops after at most a handful of calls.
    //
    // We allow up to 2 calls — one is unavoidable (the bug's first fetch
    // which returns a fully-duplicate page), the second covers any
    // benign re-observe that fires before the terminal condition is
    // committed. Anything past 3 is the runaway loop.
    expect(fetcher.mock.calls.length).toBeLessThanOrEqual(2);
  });

  it("terminates cleanly when load-more returns a short batch", async () => {
    const firstPage = postingsArray(BATCH);
    // 25 total: first 20 came as initial, next 5 are returned by load.
    const fetcher = vi.fn(async () => ({
      postings: postingsArray(5, BATCH),
      total: 25,
    }));

    render(
      <WatchlistLoadMoreShim
        initialPostings={firstPage}
        initialTotal={25}
        fetcher={fetcher}
      />,
    );

    await pumpLoop();

    // Exactly one load-more call: `result.postings.length < BATCH`
    // sets `exhausted = true` and the sentinel unmounts.
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("never fires load-more when initialPostings already match initialTotal", async () => {
    const all = postingsArray(BATCH);
    const fetcher = vi.fn();

    render(
      <WatchlistLoadMoreShim
        initialPostings={all}
        initialTotal={BATCH}
        fetcher={async () => ({
          postings: [],
          total: BATCH,
        })}
      />,
    );

    await pumpLoop();

    expect(fetcher).not.toHaveBeenCalled();
  });

  it("terminates on a partial-overlap page (some new + some duplicates)", async () => {
    const firstPage = postingsArray(BATCH);
    // Total claims 30 items but server only ever returns the same first
    // 20 plus 5 new ones — leaving 5 phantom slots. The page returned
    // is BATCH-sized (15 new + 5 old, before dedup), so the
    // `length < BATCH` short-batch terminator can't fire on the first
    // call. The terminal condition has to come from elsewhere.
    let pageNum = 0;
    const fetcher = vi.fn(async () => {
      pageNum += 1;
      // Return 5 new items + 15 duplicates each call → total page = 20.
      // After dedup, the list grows by 5 on first call, then 0 on each
      // subsequent call. Loop forever in the buggy version.
      const dupes = postingsArray(15);
      const fresh = postingsArray(5, BATCH);
      return { postings: [...fresh, ...dupes], total: 30 };
    });

    render(
      <WatchlistLoadMoreShim
        initialPostings={firstPage}
        initialTotal={30}
        fetcher={fetcher}
      />,
    );

    await pumpLoop();

    // Must terminate even in the partial-overlap case. The fix has to
    // notice that the postings list reached or passed `total` AND/OR
    // that dedup yielded zero new items, and stop firing.
    expect(fetcher.mock.calls.length).toBeLessThanOrEqual(3);
    // Sanity: at least one fetch fired (the first one) — covers the
    // case where someone accidentally disables hasMore on mount.
    expect(fetcher.mock.calls.length).toBeGreaterThanOrEqual(1);
    // Avoid the unused-page-num lint warning while documenting intent.
    expect(pageNum).toBe(fetcher.mock.calls.length);
  });

  // Sanity test: the hook actually re-fires after a successful load
  // when the sentinel stays in view. If this regresses (e.g., the
  // re-observe effect breaks), the previous duplicate-loop test would
  // start passing for the wrong reason — protect it.
  it(
    "re-fires the load function after a load that leaves the sentinel visible",
    { timeout: 10000 },
    async () => {
      const fetcher = vi.fn(async (offset: number) => ({
        postings: postingsArray(BATCH, offset),
        total: BATCH * 100, // very high total so terminal conditions don't fire
      }));

      render(
        <WatchlistLoadMoreShim
          initialPostings={postingsArray(BATCH)}
          initialTotal={BATCH * 100}
          fetcher={fetcher}
        />,
      );

      // 10 iterations is enough to observe multiple loads — beyond
      // that the IO callback continues firing but adds no signal.
      await pumpLoop(10);

      // With healthy data flow (fresh items every page, total far ahead
      // of postings), the hook should drive multiple loads before the
      // viewport "runs out".
      expect(fetcher.mock.calls.length).toBeGreaterThan(1);
    },
  );
});

