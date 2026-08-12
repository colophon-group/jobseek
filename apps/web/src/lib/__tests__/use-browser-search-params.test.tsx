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
  it("observes pushState, replaceState, and back/forward notifications", () => {
    render(<Probe />);
    expect(screen.getByTestId("query").textContent).toBe("");

    act(() => {
      window.history.pushState(null, "", "/en/explore?q=python");
    });
    expect(screen.getByTestId("query").textContent).toBe("q=python");

    act(() => {
      window.history.replaceState(null, "", "/en/explore?wm=remote");
    });
    expect(screen.getByTestId("query").textContent).toBe("wm=remote");

    act(() => {
      window.history.replaceState(null, "", "/en/explore?loc=zurich");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(screen.getByTestId("query").textContent).toBe("loc=zurich");
  });
});
