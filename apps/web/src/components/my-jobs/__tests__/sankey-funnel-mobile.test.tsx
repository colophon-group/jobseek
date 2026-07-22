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

describe("SankeyFunnel mobile summary", () => {
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn(() => ({
        matches: true,
        media: "(max-width: 640px)",
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
  });

  it("renders readable stage rows instead of a vertically labelled chart", async () => {
    const { container, getByTestId } = render(<SankeyFunnel data={data} />);

    await waitFor(() => expect(getByTestId("mobile-funnel")).toBeTruthy());

    expect(container.querySelector('[data-testid="sankey-chart"]')).toBeNull();
    expect(container.textContent).toContain("Saved");
    expect(container.textContent).toContain("Applied");
    expect(container.textContent).toContain("Round 1");
    expect(container.textContent).toContain("Offered");
    expect(container.textContent).toContain("Rejected: 2");
  });
});
