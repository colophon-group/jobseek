import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { useInfiniteScroll } from "../use-infinite-scroll";

// Stub IntersectionObserver — happy-dom doesn't ship one. We capture the
// element passed to `observe()` so tests can assert what got attached.
type ObserverInstance = {
  observe: ReturnType<typeof vi.fn>;
  unobserve: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  rootMargin?: string;
  root?: Element | null;
};
const observerInstances: ObserverInstance[] = [];

class MockIntersectionObserver {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
  rootMargin?: string;
  root?: Element | null;
  constructor(_callback: unknown, options?: { rootMargin?: string; root?: Element | null }) {
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
