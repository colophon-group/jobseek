import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

// Lingui shim — register before imports of Lingui-aware modules.
import "@/test-utils/lingui-mock";

import { InfiniteScrollSentinel } from "../InfiniteScrollSentinel";

// Regression test for #3190: the infinite-scroll sentinel renders a spinner
// when the next page is being fetched. Without role=status + aria-live the
// SR stays silent and the user never learns more results are arriving.
describe("InfiniteScrollSentinel — role=status + aria-live (#3190)", () => {
  it("renders the sentinel with role=status, aria-live=polite and an aria-label", () => {
    const { container } = render(
      <InfiniteScrollSentinel sentinelRef={() => {}} isLoading={false} />,
    );
    const sentinel = container.firstElementChild as HTMLElement;
    expect(sentinel).toBeTruthy();
    expect(sentinel.getAttribute("role")).toBe("status");
    expect(sentinel.getAttribute("aria-live")).toBe("polite");
    expect(sentinel.getAttribute("aria-label")).toBe("Loading more results");
  });

  it("flips aria-busy from false to true while the next page is loading", () => {
    const { container, rerender } = render(
      <InfiniteScrollSentinel sentinelRef={() => {}} isLoading={false} />,
    );
    const sentinel = container.firstElementChild as HTMLElement;
    expect(sentinel.getAttribute("aria-busy")).toBe("false");

    rerender(
      <InfiniteScrollSentinel sentinelRef={() => {}} isLoading={true} />,
    );
    expect(sentinel.getAttribute("aria-busy")).toBe("true");
  });

  it("includes a visually-hidden announcement while loading", () => {
    const { container } = render(
      <InfiniteScrollSentinel sentinelRef={() => {}} isLoading={true} />,
    );
    const srOnly = container.querySelector("span.sr-only");
    expect(srOnly).toBeTruthy();
    expect(srOnly?.textContent).toBe("Loading more results");
  });

  it("does not render the announcement when idle (no spurious polite hits)", () => {
    const { container } = render(
      <InfiniteScrollSentinel sentinelRef={() => {}} isLoading={false} />,
    );
    expect(container.querySelector("span.sr-only")).toBeNull();
  });
});
