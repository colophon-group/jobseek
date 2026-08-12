import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const mocks = vi.hoisted(() => ({
  getMyJobs: vi.fn(),
  getAccountPageData: vi.fn(),
  getPlanInfo: vi.fn(),
  getMyJobsStats: vi.fn(),
  cookies: vi.fn(),
}));

vi.mock("@/lib/actions/my-jobs", () => ({
  getMyJobs: (...args: unknown[]) => mocks.getMyJobs(...args),
}));

vi.mock("@/lib/actions/preferences", () => ({
  getAccountPageData: (...args: unknown[]) =>
    mocks.getAccountPageData(...args),
}));

vi.mock("@/lib/actions/billing", () => ({
  getPlanInfo: (...args: unknown[]) => mocks.getPlanInfo(...args),
}));

vi.mock("@/lib/actions/my-jobs-stats", () => ({
  getMyJobsStats: (...args: unknown[]) => mocks.getMyJobsStats(...args),
}));

vi.mock("next/headers", () => ({
  cookies: () => mocks.cookies(),
}));

vi.mock("../my-jobs-page", () => ({
  MyJobsPage: ({
    initialJobs,
    initialTotal,
  }: {
    initialJobs: unknown[];
    initialTotal: number;
  }) => (
    <div
      data-testid="my-jobs-page"
      data-jobs={initialJobs.length}
      data-total={initialTotal}
    />
  ),
}));

vi.mock("@/components/settings/AccountSettings", () => ({
  AccountSettings: ({ initialData }: { initialData: unknown }) => (
    <div data-testid="account-page" data-loaded={String(Boolean(initialData))} />
  ),
}));

vi.mock("@/components/settings/BillingSettings", () => ({
  BillingSettings: ({
    planInfo,
  }: {
    planInfo: { plan: string; canReceiveAlerts: boolean };
  }) => <div data-testid="billing-page" data-plan={planInfo.plan} />,
}));

vi.mock("../stats/stats-page", async () => {
  const { useState } = await vi.importActual<typeof import("react")>("react");
  return {
    StatsPage: ({ initial }: { initial: { activityTotal: number } }) => {
      // Mirror the production component's useState(initial) contract. This
      // intentionally preserves the first total across a same-key rerender,
      // allowing the moved-timezone test to prove the loader remounts it.
      const [data] = useState(initial);
      return <div data-testid="stats-page" data-total={data.activityTotal} />;
    },
  };
});

vi.mock("@/components/ViewerTimezoneCookie", () => ({
  ViewerTimezoneCookie: ({
    serverTimeZone,
    refreshWhenChanged,
  }: {
    serverTimeZone?: string | null;
    refreshWhenChanged?: boolean;
  }) => (
    <div
      data-testid="timezone-cookie"
      data-server-time-zone={serverTimeZone ?? ""}
      data-refresh={String(Boolean(refreshWhenChanged))}
    />
  ),
}));

import { MyJobsLoader } from "../my-jobs-loader";
import { StatsLoader } from "../stats/stats-loader";
import { AccountLoader } from "../../settings/account/account-loader";
import { BillingLoader } from "../../settings/billing/billing-loader";

const statsData = {
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
  activityTotal: 7,
};

describe("authenticated route initial server reads (#7201)", () => {
  beforeEach(() => {
    mocks.getMyJobs.mockReset();
    mocks.getAccountPageData.mockReset();
    mocks.getPlanInfo.mockReset();
    mocks.getMyJobsStats.mockReset();
    mocks.cookies.mockReset();
  });

  it("passes the first saved-job page into the interactive client", async () => {
    mocks.getMyJobs.mockResolvedValue({ jobs: [{ id: "saved-1" }], total: 4 });

    render(await MyJobsLoader({ locale: "en" }));

    expect(mocks.getMyJobs).toHaveBeenCalledOnce();
    expect(mocks.getMyJobs).toHaveBeenCalledWith({ offset: 0, limit: 20 });
    expect(screen.getByTestId("my-jobs-page").getAttribute("data-jobs")).toBe("1");
    expect(screen.getByTestId("my-jobs-page").getAttribute("data-total")).toBe("4");
  });

  it("passes account and billing action results into their existing clients", async () => {
    mocks.getAccountPageData.mockResolvedValue({
      accounts: [],
      hasPassword: false,
      username: "viewer",
    });
    mocks.getPlanInfo.mockResolvedValue({ plan: "pro", canReceiveAlerts: true });

    render(await AccountLoader({ locale: "en" }));
    render(await BillingLoader({ locale: "en" }));

    expect(mocks.getAccountPageData).toHaveBeenCalledOnce();
    expect(mocks.getPlanInfo).toHaveBeenCalledOnce();
    expect(screen.getByTestId("account-page").getAttribute("data-loaded")).toBe("true");
    expect(screen.getByTestId("billing-page").getAttribute("data-plan")).toBe("pro");
  });

  it("preserves the anonymous account result for the existing login prompt", async () => {
    mocks.getAccountPageData.mockResolvedValue(null);

    render(await AccountLoader({ locale: "en" }));

    expect(screen.getByTestId("account-page").getAttribute("data-loaded")).toBe("false");
  });

  it("buckets the initial stats response with the browser-maintained cookie", async () => {
    mocks.cookies.mockResolvedValue({
      get: () => ({ value: "America/New_York" }),
    });
    mocks.getMyJobsStats.mockResolvedValue(statsData);

    render(await StatsLoader({ locale: "en" }));

    expect(mocks.getMyJobsStats).toHaveBeenCalledOnce();
    expect(mocks.getMyJobsStats).toHaveBeenCalledWith({
      tz: "America/New_York",
    });
    expect(screen.getByTestId("stats-page").getAttribute("data-total")).toBe("7");
    expect(
      screen.getByTestId("timezone-cookie").getAttribute("data-server-time-zone"),
    ).toBe("America/New_York");
  });

  it("remounts stateful stats when an RSC refresh changes the bucket timezone", async () => {
    mocks.cookies
      .mockResolvedValueOnce({
        get: () => ({ value: "America/New_York" }),
      })
      .mockResolvedValueOnce({
        get: () => ({ value: "Europe/Athens" }),
      });
    mocks.getMyJobsStats
      .mockResolvedValueOnce(statsData)
      .mockResolvedValueOnce({ ...statsData, activityTotal: 11 });

    const view = render(await StatsLoader({ locale: "en" }));
    expect(screen.getByTestId("stats-page").getAttribute("data-total")).toBe("7");

    // Model the RSC payload produced after ViewerTimezoneCookie writes the
    // browser zone and calls router.refresh(). React preserves client state
    // unless the loader gives the stateful subtree a timezone-specific key.
    view.rerender(await StatsLoader({ locale: "en" }));

    expect(mocks.getMyJobsStats).toHaveBeenNthCalledWith(1, {
      tz: "America/New_York",
    });
    expect(mocks.getMyJobsStats).toHaveBeenNthCalledWith(2, {
      tz: "Europe/Athens",
    });
    expect(screen.getByTestId("stats-page").getAttribute("data-total")).toBe("11");
  });

  it.each([undefined, "Mars/Olympus", "'; DROP TABLE saved_job; --"])(
    "does not query or render mismatched stats for an untrusted timezone: %s",
    async (timeZone) => {
      mocks.cookies.mockResolvedValue({
        get: () => (timeZone === undefined ? undefined : { value: timeZone }),
      });

      render(await StatsLoader({ locale: "en" }));

      expect(mocks.getMyJobsStats).not.toHaveBeenCalled();
      expect(
        screen.getByTestId("timezone-cookie").getAttribute("data-refresh"),
      ).toBe("true");
      expect(screen.queryByTestId("stats-page")).toBeNull();
    },
  );

  it("lets read failures reach the route error boundary", async () => {
    mocks.getMyJobs.mockRejectedValue(new Error("database unavailable"));
    mocks.getAccountPageData.mockRejectedValue(new Error("database unavailable"));
    mocks.getPlanInfo.mockRejectedValue(new Error("database unavailable"));
    mocks.cookies.mockResolvedValue({ get: () => ({ value: "UTC" }) });
    mocks.getMyJobsStats.mockRejectedValue(new Error("database unavailable"));

    await expect(MyJobsLoader({ locale: "en" })).rejects.toThrow("database unavailable");
    await expect(AccountLoader({ locale: "en" })).rejects.toThrow("database unavailable");
    await expect(BillingLoader({ locale: "en" })).rejects.toThrow("database unavailable");
    await expect(StatsLoader({ locale: "en" })).rejects.toThrow("database unavailable");
  });
});

describe("authenticated route loading boundaries (#7201)", () => {
  const routeRoot = "app/[lang]/(app)";

  it.each([
    "my-jobs/my-jobs-loader.tsx",
    "my-jobs/stats/stats-loader.tsx",
    "settings/account/account-loader.tsx",
    "settings/billing/billing-loader.tsx",
  ])("keeps %s free of mount-time action effects", (relativePath) => {
    const source = readFileSync(`${routeRoot}/${relativePath}`, "utf8");

    expect(source).not.toContain('"use client"');
    expect(source).not.toContain("useEffect");
    expect(source).not.toContain("useState");
    expect(source).toContain("export async function");
  });

  it.each([
    ["my-jobs/page.tsx", "MyJobsLoader"],
    ["my-jobs/stats/page.tsx", "StatsLoader"],
    ["settings/account/page.tsx", "AccountLoader"],
    ["settings/billing/page.tsx", "BillingLoader"],
  ])("streams %s behind route-level Suspense", (relativePath, loaderName) => {
    const source = readFileSync(`${routeRoot}/${relativePath}`, "utf8");

    expect(source).toContain("<Suspense fallback={");
    expect(source).toContain(`<${loaderName} locale={locale} />`);
  });
});
