import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { useRef } from "react";

import { VirtualizedList } from "../virtualized-list";

/**
 * Render-perf tests for #2982. In happy-dom (the test environment) the
 * scroll container has `clientHeight === 0` because there's no layout
 * engine, so VirtualizedList falls back to flat rendering. We exercise:
 *
 *  1. **Flat-fallback correctness** — small lists render every row in
 *     normal flow. This keeps the existing modal tests
 *     (`location-search-modal`, `technology-modal`) passing without
 *     having to mock the virtualizer.
 *  2. **Flat-fallback perf budget** — even the slow path must not blow
 *     up. A 10,000-row flat render should complete under 1500 ms in
 *     happy-dom. The real virtualized perf gain is measured in the
 *     PR's Playwright numbers, not here.
 */
function Harness({ count }: { count: number }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const items = Array.from({ length: count }, (_, i) => ({
    id: i,
    label: `Row ${i}`,
  }));
  return (
    <div ref={scrollRef} style={{ height: 400, overflow: "auto" }}>
      <VirtualizedList
        items={items}
        getKey={(item) => item.id}
        estimateSize={32}
        scrollRef={scrollRef}
        render={(item) => <div>{item.label}</div>}
      />
    </div>
  );
}

describe("VirtualizedList — perf budget (#2982)", () => {
  it("flat-fallback path: renders every item when the scroll container is unmeasurable", () => {
    const { container } = render(<Harness count={50} />);
    const rows = container.querySelectorAll("[data-index]");
    expect(rows.length).toBe(50);
  });

  it("flat-fallback perf budget: 10,000 rows render under 1500ms in happy-dom", () => {
    const t0 = performance.now();
    const { unmount } = render(<Harness count={10_000} />);
    const elapsed = performance.now() - t0;
    unmount();
    expect(elapsed).toBeLessThan(1500);
  });
});
