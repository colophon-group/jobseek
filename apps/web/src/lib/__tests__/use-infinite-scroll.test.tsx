import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { useRef, useState } from "react";
import { useInfiniteScroll } from "../use-infinite-scroll";

// Stub IntersectionObserver — happy-dom doesn't ship one. We capture the
// element passed to `observe()` so tests can assert what got attached.
// `callback` is exposed so the horizontal-carousel tests below can fire
// the IO callback manually (the bare mock doesn't auto-fire).
type ObserverCallback = (entries: IntersectionObserverEntry[]) => void;
type ObserverInstance = {
  callback: ObserverCallback;
  observe: ReturnType<typeof vi.fn>;
  unobserve: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  rootMargin?: string;
  root?: Element | null;
};
const observerInstances: ObserverInstance[] = [];

class MockIntersectionObserver {
  callback: ObserverCallback;
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  rootMargin?: string;
  root?: Element | null;
  constructor(
    callback: ObserverCallback,
    options?: { rootMargin?: string; root?: Element | null },
  ) {
    this.callback = callback;
    this.rootMargin = options?.rootMargin;
    this.root = options?.root ?? null;
    observerInstances.push(this);
  }
}

beforeEach(() => {
  observerInstances.length = 0;
  // @ts-expect-error — assigning a mock to the global
  globalThis.IntersectionObserver = MockIntersectionObserver;
});

afterEach(() => {
  // @ts-expect-error — cleanup
  delete globalThis.IntersectionObserver;
});

/**
 * The exact shape that broke after #2248: a list with an early
 * `if (items.length === 0) return null` that defers the sentinel's
 * mount until a fetch completes. The hook MUST attach the observer
 * once the sentinel finally appears in the DOM, even though refs
 * aren't reactive React deps.
 */
function ConditionalListWithLoad({
  initiallyEmpty,
}: {
  initiallyEmpty: boolean;
}) {
  const [items, setItems] = useState<string[]>(initiallyEmpty ? [] : ["a"]);
  const { sentinelRef } = useInfiniteScroll({
    hasMore: true,
    load: async () => {},
  });

  if (items.length === 0) {
    // The bug we're guarding against: returning null prevents the
    // sentinel JSX from mounting on first render.
    return (
      <button
        data-testid="trigger"
        onClick={() => setItems(["loaded"])}
      >
        load
      </button>
    );
  }

  return (
    <div>
      <ul>{items.map((i) => <li key={i}>{i}</li>)}</ul>
      <div ref={sentinelRef} data-testid="sentinel" />
    </div>
  );
}

function lastObservedElement(): Element | undefined {
  // The hook has a separate "re-observe after load finishes" effect
  // that calls observe() a second time on mount, so we don't pin the
  // call count — only the most recent observed element.
  for (let i = observerInstances.length - 1; i >= 0; i--) {
    const calls = observerInstances[i].observe.mock.calls;
    if (calls.length > 0) return calls[calls.length - 1][0];
  }
  return undefined;
}

describe("useInfiniteScroll — sentinel re-attach on conditional mount", () => {
  it("attaches the observer to the sentinel when it is present from first render", () => {
    render(<ConditionalListWithLoad initiallyEmpty={false} />);

    expect(observerInstances).toHaveLength(1);
    expect(lastObservedElement()).toBe(screen.getByTestId("sentinel"));
  });

  it("attaches the observer after the sentinel mounts later (regression for issue introduced by #2248)", async () => {
    const { findByTestId } = render(
      <ConditionalListWithLoad initiallyEmpty={true} />,
    );

    // No sentinel in the DOM yet → no observer created. Pre-fix this
    // was the same, but it stayed that way forever because the hook's
    // effect deps didn't include the sentinel element. The bug:
    // when the sentinel mounted later (after the post-mount fetch),
    // the effect didn't re-run and the observer was never attached.
    expect(observerInstances).toHaveLength(0);

    await act(async () => {
      (await findByTestId("trigger")).click();
    });

    // Post-fix: the callback-ref + state pattern in the hook detects
    // the sentinel becoming available and attaches the observer.
    const sentinel = await findByTestId("sentinel");
    await waitFor(() => expect(observerInstances).toHaveLength(1));
    expect(lastObservedElement()).toBe(sentinel);
  });

  it("disconnects the observer when the sentinel unmounts", async () => {
    function Toggle() {
      const [show, setShow] = useState(true);
      const { sentinelRef } = useInfiniteScroll({
        hasMore: true,
        load: async () => {},
      });
      return (
        <>
          <button data-testid="toggle" onClick={() => setShow((s) => !s)}>
            toggle
          </button>
          {show && <div ref={sentinelRef} data-testid="sentinel" />}
        </>
      );
    }

    const { findByTestId } = render(<Toggle />);
    expect(observerInstances).toHaveLength(1);
    const created = observerInstances[0];

    expect(created.disconnect).not.toHaveBeenCalled();
    await act(async () => {
      (await findByTestId("toggle")).click();
    });

    // After the sentinel unmounts, the cleanup function in the
    // hook's effect must disconnect the observer.
    await waitFor(() => expect(created.disconnect).toHaveBeenCalled());
  });
});

// ─────────────────────────────────────────────────────────────────────
// Horizontal-carousel rect-check regression
// ─────────────────────────────────────────────────────────────────────
//
// After #3353 the post-load fallback was a vertical-only rect check —
// it used `parseInt(rootMargin)` to expand only the top/bottom edges of
// the root rect. That broke the `similar-companies-strip` carousel
// (rootMargin: "0px 200px 0px 0px") because a sentinel scrolled far
// off-screen horizontally still satisfied the vertical predicate
// trivially → the rect-check fired `doLoad` on every isLoading flip and
// chain-loaded every page of similar companies on cold start.
//
// The fix parses rootMargin as the 4-value CSS shorthand and applies
// each component on the matching axis. These tests pin both directions
// of the regression: out-of-margin sentinels must NOT trigger doLoad,
// in-margin sentinels must.

/**
 * Shim that renders a horizontal-scrolling carousel using `useInfiniteScroll`
 * with the same `rootMargin: "0px 200px 0px 0px"` as similar-companies-strip.
 *
 *   - `sentinelLeft` / `sentinelRight` pin the sentinel's bounding rect
 *     so the rect-check can be exercised deterministically.
 *   - `load` is a controlled async function. Each call returns a
 *     promise the test resolves manually, giving the test precise
 *     control over the `isLoading: true → false` falling-edge that
 *     triggers the post-load rect-check.
 */
function HorizontalCarouselShim({
  sentinelLeft,
  sentinelRight,
  load,
}: {
  sentinelLeft: number;
  sentinelRight: number;
  load: () => Promise<void>;
}) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const { sentinelRef, isLoading } = useInfiniteScroll({
    hasMore: true,
    load,
    root: scrollRef,
    rootMargin: "0px 200px 0px 0px",
  });

  return (
    <div
      ref={(el) => {
        scrollRef.current = el;
        if (el) {
          // Root: 800px-wide scroll viewport at origin.
          el.getBoundingClientRect = () =>
            ({
              top: 0,
              bottom: 200,
              left: 0,
              right: 800,
              x: 0,
              y: 0,
              width: 800,
              height: 200,
              toJSON: () => ({}),
            } as DOMRect);
        }
      }}
      data-testid="scroll-root"
    >
      <div data-testid="is-loading">{String(isLoading)}</div>
      <div
        data-testid="sentinel"
        ref={(el) => {
          sentinelRef(el as HTMLDivElement | null);
          if (el) {
            el.getBoundingClientRect = () =>
              ({
                top: 0,
                bottom: 200,
                left: sentinelLeft,
                right: sentinelRight,
                x: sentinelLeft,
                y: 0,
                width: sentinelRight - sentinelLeft,
                height: 200,
                toJSON: () => ({}),
              } as DOMRect);
          }
        }}
      />
    </div>
  );
}

/**
 * Manually-resolvable promise so the test can drive the `isLoading`
 * trailing edge exactly when it wants. Without this we can't deterministically
 * exercise the rect-check fallback — it only fires on a true → false
 * transition.
 */
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("useInfiniteScroll — horizontal-carousel rect-check (regression for #3362 critic finding)", () => {
  it(
    "does NOT trigger a follow-up load when the sentinel is past the horizontal rootMargin",
    async () => {
      // Root width 800; rootMargin right = 200 → effective right edge is
      // 1000. Sentinel at x=2000 is well past that — the rect-check must
      // see "out of view" and skip doLoad.
      const firstLoad = deferred();
      const followupLoad = deferred();
      let callCount = 0;
      const load = vi.fn(() => {
        callCount += 1;
        return callCount === 1 ? firstLoad.promise : followupLoad.promise;
      });

      render(
        <HorizontalCarouselShim
          sentinelLeft={2000}
          sentinelRight={2200}
          load={load}
        />,
      );

      // Drive the first load by simulating an IO intersection — the
      // mocked observer captures the entry but doesn't fire callbacks,
      // so we trigger it manually through the captured constructor arg.
      // (Simpler: directly invoke the observer's stored callback.)
      const observer = observerInstances[observerInstances.length - 1];
      expect(observer).toBeDefined();
      // First load: pretend IO fires "in view".
      await act(async () => {
        observer.callback([
          {
            isIntersecting: true,
            target: screen.getByTestId("sentinel"),
          } as unknown as IntersectionObserverEntry,
        ]);
      });
      expect(load).toHaveBeenCalledTimes(1);

      // Finish the first load → trailing edge → rect-check fires.
      await act(async () => {
        firstLoad.resolve();
        await Promise.resolve();
      });

      // Out-of-margin sentinel: rect-check must reject. Load count stays 1.
      expect(load).toHaveBeenCalledTimes(1);
    },
  );

  it(
    "DOES trigger a follow-up load when the sentinel is within the horizontal rootMargin",
    async () => {
      // Root width 800; rootMargin right = 200 → effective right edge is
      // 1000. Sentinel at x=850 (right=950) is inside that band — the
      // rect-check must see "in view" and call doLoad a second time.
      const firstLoad = deferred();
      const followupLoad = deferred();
      let callCount = 0;
      const load = vi.fn(() => {
        callCount += 1;
        return callCount === 1 ? firstLoad.promise : followupLoad.promise;
      });

      const { unmount } = render(
        <HorizontalCarouselShim
          sentinelLeft={850}
          sentinelRight={950}
          load={load}
        />,
      );

      const observer = observerInstances[observerInstances.length - 1];
      expect(observer).toBeDefined();
      await act(async () => {
        observer.callback([
          {
            isIntersecting: true,
            target: screen.getByTestId("sentinel"),
          } as unknown as IntersectionObserverEntry,
        ]);
      });
      expect(load).toHaveBeenCalledTimes(1);

      await act(async () => {
        firstLoad.resolve();
        await Promise.resolve();
      });

      // In-margin sentinel: rect-check fires a second load.
      await waitFor(() => expect(load).toHaveBeenCalledTimes(2));

      // Unmount before resolving the second load. Otherwise the in-view
      // sentinel would chain a third load on every trailing edge in a
      // controlled-promise test environment.
      unmount();
      followupLoad.resolve();
    },
  );

  it(
    "still triggers a follow-up load for an in-view vertical sentinel (original watchlist fix is preserved)",
    async () => {
      // Vertical-list scenario from #3353: default rootMargin "200px",
      // sentinel near the bottom of an 800-tall window viewport. The
      // rect-check MUST still fire — this guards against regressing the
      // original fix while adding the horizontal axis.
      const firstLoad = deferred();
      const followupLoad = deferred();
      let callCount = 0;
      const load = vi.fn(() => {
        callCount += 1;
        return callCount === 1 ? firstLoad.promise : followupLoad.promise;
      });

      function VerticalShim() {
        const { sentinelRef, isLoading } = useInfiniteScroll({
          hasMore: true,
          load,
          // No explicit root → falls back to window viewport.
          rootMargin: "200px",
        });
        return (
          <div>
            <div data-testid="is-loading">{String(isLoading)}</div>
            <div
              data-testid="sentinel"
              ref={(el) => {
                sentinelRef(el as HTMLDivElement | null);
                if (el) {
                  // window.innerHeight is happy-dom's default (~768);
                  // place the sentinel inside the bottom margin.
                  el.getBoundingClientRect = () =>
                    ({
                      top: 700,
                      bottom: 720,
                      left: 0,
                      right: 200,
                      x: 0,
                      y: 700,
                      width: 200,
                      height: 20,
                      toJSON: () => ({}),
                    } as DOMRect);
                }
              }}
            />
          </div>
        );
      }

      const { unmount } = render(<VerticalShim />);

      const observer = observerInstances[observerInstances.length - 1];
      await act(async () => {
        observer.callback([
          {
            isIntersecting: true,
            target: screen.getByTestId("sentinel"),
          } as unknown as IntersectionObserverEntry,
        ]);
      });
      expect(load).toHaveBeenCalledTimes(1);

      await act(async () => {
        firstLoad.resolve();
        await Promise.resolve();
      });

      // Vertical rect-check must still fire — this is the watchlist
      // cold-start fix from the first commit on this branch.
      await waitFor(() => expect(load).toHaveBeenCalledTimes(2));

      unmount();
      followupLoad.resolve();
    },
  );
});
