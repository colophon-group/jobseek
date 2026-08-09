import type { ReactNode } from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { StatsPage } from "../stats-page";

vi.mock("@lingui/react/macro", () => ({
  Trans: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/lib/useLocalePath", () => ({
  useLocalePath: () => (path: string) => path,
}));

vi.mock("@/components/BackLink", () => ({
  BackLink: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

vi.mock("@/components/my-jobs/sankey-funnel-lazy", () => ({
  SankeyFunnel: () => <div />,
}));

vi.mock("@/components/my-jobs/activity-heatmap", () => ({
  ActivityHeatmap: () => <div />,
}));

vi.mock("@/lib/actions/my-jobs-stats", () => ({
  getMyJobsStats: vi.fn(),
}));

vi.mock("@/lib/viewer-tz", () => ({
  getViewerTz: () => "UTC",
}));

const initial = {
  funnel: {
    saved: 0,
    applied: 0,
    offered: 0,
    offeredWithoutInterview: 0,
    rejectedAtSaved: 0,
    rejectedAtApplied: 0,
    noResponseAtSaved: 0,
    noResponseAtApplied: 0,
    interviewRounds: [],
    rejectedAtRound: [],
    noResponseAtRound: [],
    offeredAtRound: [],
  },
  activity: [],
  activityTotal: 0,
};

describe("StatsPage period filter", () => {
  it("gives both date boundaries visible, programmatic labels", () => {
    render(<StatsPage initial={initial} />);

    expect(screen.getByLabelText("From").getAttribute("type")).toBe("date");
    expect(screen.getByLabelText("To").getAttribute("type")).toBe("date");
  });
});
