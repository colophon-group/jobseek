import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

import { SkipToContentLink } from "../SkipToContentLink";

function visibleClientRects(): DOMRectList {
  return { length: 1 } as DOMRectList;
}

describe("SkipToContentLink", () => {
  it("renders above fixed chrome when focused", () => {
    render(<SkipToContentLink />);

    const link = screen.getByRole("link", { name: "Skip to content" });
    expect(link.className).toContain("fixed");
    expect(link.className).toContain("z-[100]");
    expect(link.className).toContain("-translate-y-16");
    expect(link.className).toContain("focus:translate-y-0");
    expect(link.className).not.toContain("sr-only");
  });

  it("focuses the visible target when a hidden streamed duplicate comes first", async () => {
    const scrollIntoView = vi.fn();

    render(
      <>
        <SkipToContentLink />
        <div id="main-content" data-testid="hidden-main" tabIndex={-1} />
        <main id="main-content" data-testid="visible-main">
          <button type="button">First main action</button>
        </main>
      </>,
    );

    const hiddenTarget = screen.getByTestId("hidden-main");
    const visibleTarget = screen.getByTestId("visible-main");
    vi.spyOn(hiddenTarget, "getClientRects").mockReturnValue({ length: 0 } as DOMRectList);
    vi.spyOn(visibleTarget, "getClientRects").mockImplementation(visibleClientRects);
    Object.defineProperty(visibleTarget, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });

    fireEvent.click(screen.getByRole("link", { name: "Skip to content" }));

    expect(document.activeElement).toBe(visibleTarget);
    expect(visibleTarget.getAttribute("tabindex")).toBe("-1");
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "start" });
    expect(window.location.hash).toBe("#main-content");

    await userEvent.setup().tab();
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "First main action" }),
    );
  });
});
