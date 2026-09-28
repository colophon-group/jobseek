import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppContentFrame } from "../AppContentFrame";

describe("AppContentFrame", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("updates the detail offset when alerts appear, wrap or disappear", () => {
    let notifyResize: () => void = () => {};
    let alerts: Element | undefined;
    const disconnect = vi.fn();
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: () => void) { notifyResize = callback; }
      observe(element: Element) { alerts = element; }
      disconnect = disconnect;
    });
    const { unmount } = render(
      <AppContentFrame alerts={<p>Alert</p>}>
        <main data-testid="page">Page</main>
      </AppContentFrame>,
    );
    const frame = screen.getByTestId("page").parentElement!;
    const rect = vi.spyOn(alerts!, "getBoundingClientRect");
    for (const height of [44, 80, 0]) {
      rect.mockReturnValue({ height } as DOMRect);
      act(() => notifyResize());
      expect(frame.style.getPropertyValue("--app-alert-height")).toBe(`${height}px`);
    }
    unmount();
    expect(disconnect).toHaveBeenCalledOnce();
  });
});
