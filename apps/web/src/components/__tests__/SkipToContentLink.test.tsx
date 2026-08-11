import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import "@/test-utils/lingui-mock";

import { SkipToContentLink } from "../SkipToContentLink";

function visibleClientRects(): DOMRectList {
  return { length: 1 } as DOMRectList;
}

function hiddenClientRects(): DOMRectList {
  return { length: 0 } as DOMRectList;
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
        <div hidden>
          <main id="main-content" data-testid="hidden-main" tabIndex={-1} />
        </div>
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

    await waitFor(() => {
      expect(document.querySelectorAll("#main-content")).toHaveLength(1);
    });
    expect(hiddenTarget.getAttribute("id")).toBeNull();
    expect(document.querySelector("#main-content")).toBe(visibleTarget);

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

  it("reconciles a hidden duplicate streamed after hydration", async () => {
    render(
      <>
        <SkipToContentLink />
        <main id="main-content" data-testid="visible-main" />
      </>,
    );

    const visibleTarget = screen.getByTestId("visible-main");
    vi.spyOn(visibleTarget, "getClientRects").mockImplementation(visibleClientRects);

    const streamedFallback = document.createElement("div");
    streamedFallback.hidden = true;
    streamedFallback.innerHTML = '<main id="main-content">Fallback</main>';
    document.body.append(streamedFallback);

    await waitFor(() => {
      expect(document.querySelectorAll("#main-content")).toHaveLength(1);
    });
    expect(document.querySelector("#main-content")).toBe(visibleTarget);
    streamedFallback.remove();
  });

  it("moves the canonical id when Next promotes the hidden PPR tree", async () => {
    render(
      <>
        <SkipToContentLink />
        <div data-testid="staged-container" hidden>
          <main id="main-content" data-testid="staged-main" />
        </div>
        <div data-testid="current-container">
          <main id="main-content" data-testid="current-main" />
        </div>
      </>,
    );

    const stagedContainer = screen.getByTestId("staged-container");
    const currentContainer = screen.getByTestId("current-container");
    const stagedTarget = screen.getByTestId("staged-main");
    const currentTarget = screen.getByTestId("current-main");
    vi.spyOn(stagedTarget, "getClientRects").mockImplementation(() =>
      stagedContainer.hidden ? hiddenClientRects() : visibleClientRects(),
    );
    vi.spyOn(currentTarget, "getClientRects").mockImplementation(() =>
      currentContainer.hidden ? hiddenClientRects() : visibleClientRects(),
    );

    await waitFor(() => {
      expect(document.querySelector("#main-content")).toBe(currentTarget);
    });
    expect(stagedTarget.getAttribute("id")).toBeNull();

    currentContainer.hidden = true;
    stagedContainer.hidden = false;

    await waitFor(() => {
      expect(document.querySelectorAll("#main-content")).toHaveLength(1);
      expect(document.querySelector("#main-content")).toBe(stagedTarget);
    });
    expect(currentTarget.getAttribute("id")).toBeNull();
  });

  it.each(["aria-hidden", "inert"] as const)(
    "moves the canonical id when Next promotes a tree hidden by %s",
    async (hiddenAttribute) => {
      render(
        <>
          <SkipToContentLink />
          <div
            data-testid="staged-container"
            aria-hidden={hiddenAttribute === "aria-hidden" ? "true" : undefined}
            inert={hiddenAttribute === "inert" ? true : undefined}
          >
            <main id="main-content" data-testid="staged-main" />
          </div>
          <div data-testid="current-container">
            <main id="main-content" data-testid="current-main" />
          </div>
        </>,
      );

      const stagedContainer = screen.getByTestId("staged-container");
      const currentContainer = screen.getByTestId("current-container");
      const stagedTarget = screen.getByTestId("staged-main");
      const currentTarget = screen.getByTestId("current-main");
      vi.spyOn(stagedTarget, "getClientRects").mockImplementation(
        visibleClientRects,
      );
      vi.spyOn(currentTarget, "getClientRects").mockImplementation(
        visibleClientRects,
      );

      await waitFor(() => {
        expect(document.querySelector("#main-content")).toBe(currentTarget);
      });
      expect(stagedTarget.getAttribute("id")).toBeNull();

      currentContainer.setAttribute(hiddenAttribute, "true");
      stagedContainer.removeAttribute(hiddenAttribute);

      await waitFor(() => {
        expect(document.querySelectorAll("#main-content")).toHaveLength(1);
        expect(document.querySelector("#main-content")).toBe(stagedTarget);
      });
      expect(currentTarget.getAttribute("id")).toBeNull();
    },
  );

  it("reassigns the canonical id when the selected tree is removed", async () => {
    render(<SkipToContentLink />);

    const currentContainer = document.createElement("div");
    const currentTarget = document.createElement("main");
    const remainingTarget = document.createElement("main");
    currentTarget.id = "main-content";
    remainingTarget.id = "main-content";
    currentContainer.append(currentTarget);
    vi.spyOn(currentTarget, "getClientRects").mockImplementation(
      visibleClientRects,
    );
    vi.spyOn(remainingTarget, "getClientRects").mockImplementation(
      visibleClientRects,
    );
    document.body.append(currentContainer, remainingTarget);

    await waitFor(() => {
      expect(document.querySelector("#main-content")).toBe(currentTarget);
    });

    currentContainer.remove();

    await waitFor(() => {
      expect(document.querySelectorAll("#main-content")).toHaveLength(1);
      expect(document.querySelector("#main-content")).toBe(remainingTarget);
    });
    remainingTarget.remove();
  });

  it("does not remeasure the main target for unrelated subtree updates", async () => {
    render(
      <>
        <SkipToContentLink />
        <main id="main-content" data-testid="visible-main" />
      </>,
    );

    const visibleTarget = screen.getByTestId("visible-main");
    const getClientRects = vi
      .spyOn(visibleTarget, "getClientRects")
      .mockImplementation(visibleClientRects);
    visibleTarget.setAttribute("id", "main-content");

    await waitFor(() => expect(getClientRects).toHaveBeenCalled());
    getClientRects.mockClear();

    const unrelated = document.createElement("div");
    unrelated.textContent = "Unrelated search result";
    document.body.append(unrelated);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(getClientRects).not.toHaveBeenCalled();
    unrelated.remove();
  });
});
