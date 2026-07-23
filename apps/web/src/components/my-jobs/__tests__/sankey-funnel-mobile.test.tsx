import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import type { FunnelData } from "@/lib/actions/my-jobs-stats";
import { SankeyFunnel } from "../sankey-funnel";

vi.mock("@lingui/react/macro", () => ({
  useLingui: () => ({
    t: ({ message }: { message?: string }) => message ?? "",
  }),
}));

vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light" }),
}));

vi.mock("@nivo/sankey", () => ({
  ResponsiveSankey: () => <div data-testid="sankey-chart" />,
}));

const data: FunnelData = {
  saved: 10,
  applied: 5,
  offered: 1,
  offeredWithoutInterview: 0,
  rejectedAtSaved: 2,
  rejectedAtApplied: 1,
  noResponseAtSaved: 3,
  noResponseAtApplied: 1,
  interviewRounds: [{ round: 1, count: 3 }],
  rejectedAtRound: [{ round: 1, count: 1 }],
  noResponseAtRound: [],
  offeredAtRound: [{ round: 1, count: 1 }],
};

describe("SankeyFunnel accessible summary", () => {
  function mockSmallScreen(matches: boolean) {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        matches,
        media: "(max-width: 640px)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  }

  beforeEach(() => mockSmallScreen(true));

  it("renders readable stage rows instead of a vertically labelled chart", async () => {
    const { container, getByTestId } = render(<SankeyFunnel data={data} />);

    await waitFor(() => expect(getByTestId("funnel-summary")).toBeTruthy());

    expect(container.querySelector('[data-testid="sankey-chart"]')).toBeNull();
    expect(container.textContent).toContain("Saved");
    expect(container.textContent).toContain("Applied");
    expect(container.textContent).toContain("Round 1");
    expect(container.textContent).toContain("Offered");
    expect(container.textContent).toContain("Rejected: 2");
  });

  it("keeps the structured funnel in the desktop accessibility tree", async () => {
    mockSmallScreen(false);

    const { getByRole, getByTestId } = render(<SankeyFunnel data={data} />);

    await waitFor(() => expect(getByTestId("sankey-chart")).toBeTruthy());

    const summary = getByRole("list", { name: "Application funnel" });
    expect(summary.className).toContain("sr-only");
    expect(summary.textContent).toContain("Saved");
    expect(summary.textContent).toContain("Applied");
    expect(summary.textContent).toContain("Round 1");
    expect(summary.textContent).toContain("Offered");
    expect(summary.textContent).toContain("Rejected: 2");
    expect(getByTestId("sankey-visual").getAttribute("aria-hidden")).toBe("true");
  });
});
