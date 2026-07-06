import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

// Lingui shim — register before imports of Lingui-aware modules.
import "@/test-utils/lingui-mock";

import { SkeletonCards } from "../skeleton-card";

// Regression test for #3190: skeleton placeholders must surface as a polite
// live region in a busy state so screen readers announce that content is
// loading instead of staying silent on the visual pulse.
describe("SkeletonCards — aria-busy/aria-live (#3190)", () => {
  it("renders the outer container with aria-busy and aria-live=polite", () => {
    const { container } = render(<SkeletonCards count={3} />);
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper).toBeTruthy();
    expect(wrapper.getAttribute("aria-busy")).toBe("true");
    expect(wrapper.getAttribute("aria-live")).toBe("polite");
    expect(wrapper.getAttribute("role")).toBe("status");
  });

  it("includes a visually-hidden screen-reader announcement", () => {
    const { container } = render(<SkeletonCards count={1} />);
    const srOnly = container.querySelector("span.sr-only");
    expect(srOnly).toBeTruthy();
    expect(srOnly?.textContent).toBe("Loading results");
  });
});
