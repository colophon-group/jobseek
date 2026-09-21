import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { useBrowserSearchParams } from "@/lib/use-browser-search-params";

function Probe() {
  const searchParams = useBrowserSearchParams();
  return <output data-testid="query">{searchParams.toString()}</output>;
}

beforeEach(() => {
  window.history.replaceState(null, "", "/en/explore");
});

describe("useBrowserSearchParams", () => {
  it("observes pushState, replaceState, and back/forward notifications", async () => {
    render(<Probe />);
    expect(screen.getByTestId("query").textContent).toBe("");

    await act(async () => {
      window.history.pushState(null, "", "/en/explore?q=python");
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByTestId("query").textContent).toBe("q=python");

    await act(async () => {
      window.history.replaceState(null, "", "/en/explore?wm=remote");
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByTestId("query").textContent).toBe("wm=remote");

    await act(async () => {
      window.history.replaceState(null, "", "/en/explore?loc=zurich");
      window.dispatchEvent(new PopStateEvent("popstate"));
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(screen.getByTestId("query").textContent).toBe("loc=zurich");
  });
});
