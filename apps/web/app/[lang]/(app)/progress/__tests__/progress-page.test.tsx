import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import "@/test-utils/lingui-mock";

const mocks = vi.hoisted(() => ({
  getSiteStats: vi.fn(),
}));

vi.mock("@/lib/actions/stats", () => ({
  getSiteStats: () => mocks.getSiteStats(),
}));

vi.mock("../company-request-form", () => ({
  CompanyRequestForm: () => null,
}));

import AppPage from "../page";

describe("progress page server data", () => {
  beforeEach(() => {
    mocks.getSiteStats.mockReset();
  });

  it("includes cached site counters in the initial server render", async () => {
    mocks.getSiteStats.mockResolvedValue({
      companyCount: 1234,
      jobPostingCount: 5678,
    });

    render(await AppPage({ params: Promise.resolve({ lang: "en" }) }));

    expect(screen.getByText("1,234")).toBeTruthy();
    expect(screen.getByText("5,678")).toBeTruthy();
    expect(mocks.getSiteStats).toHaveBeenCalledOnce();
  });

  it("preserves the placeholder fallback when the cached read fails", async () => {
    mocks.getSiteStats.mockRejectedValue(new Error("typesense unavailable"));

    render(await AppPage({ params: Promise.resolve({ lang: "de" }) }));

    expect(screen.getAllByText("—")).toHaveLength(2);
  });

  it("keeps the presentation component free of mount-time actions", () => {
    const source = readFileSync(
      "app/[lang]/(app)/progress/progress-loader.tsx",
      "utf8",
    );

    expect(source).not.toContain('"use client"');
    expect(source).not.toContain("useEffect");
    expect(source).not.toContain("getSiteStats");
  });
});
