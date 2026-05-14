import { describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";
import type { FunnelData } from "@/lib/actions/my-jobs-stats";

/**
 * #3189 — Lazy-load nivo Sankey funnel on /my-jobs/stats.
 *
 * The funnel pulls in ~250 KB of `@nivo/sankey` + `@nivo/core`. We lazy-load
 * it via `next/dynamic`. These tests pin the contract:
 *
 *   - While the dynamic import is pending, a skeleton placeholder is rendered
 *     in place of the chart (so the page doesn't shift when the chart loads).
 *   - Once the import resolves, the real `SankeyFunnel` mounts and renders.
 */

// Stub Lingui — the real funnel uses it for translated labels.
vi.mock("@lingui/react/macro", () => ({
  useLingui: () => ({
    t: ({ message }: { message?: string }) => message ?? "",
  }),
}));

// Stub next-themes — the real funnel reads `resolvedTheme`.
vi.mock("next-themes", () => ({
  useTheme: () => ({ resolvedTheme: "light" }),
}));

// Stub `@nivo/sankey` so the test runner doesn't have to pull in the real
// chart (we're only verifying the lazy-load wiring, not nivo's rendering).
vi.mock("@nivo/sankey", () => ({
  ResponsiveSankey: () => <div data-testid="sankey-chart" />,
}));

function makeFunnelData(): FunnelData {
  return {
    saved: 10,
    applied: 5,
    offered: 1,
    offeredWithoutInterview: 0,
    rejectedAtSaved: 2,
    rejectedAtApplied: 1,
    noResponseAtSaved: 3,
    noResponseAtApplied: 1,
    interviewRounds: [{ round: 1, count: 3 }],
    rejectedAtRound: [],
    noResponseAtRound: [],
    offeredAtRound: [{ round: 1, count: 1 }],
  };
}

describe("SankeyFunnel (lazy wrapper) — #3189", () => {
  it("renders a skeleton placeholder while the chart import is pending and swaps in the chart once it resolves", async () => {
    // Don't mock next/dynamic — exercise the real Next.js lazy loader so we
    // pin the genuine load → swap sequence.
    const { SankeyFunnel } = await import("../sankey-funnel-lazy");
    const { container } = render(<SankeyFunnel data={makeFunnelData()} />);

    // Skeleton: a div sized to match the rendered funnel, marked aria-hidden
    // so screen readers ignore the loading state. It uses the
    // `animate-pulse` Tailwind class to indicate loading.
    const skeleton = container.querySelector('[aria-hidden="true"]');
    expect(skeleton).not.toBeNull();
    expect(skeleton!.className).toContain("animate-pulse");
    expect(skeleton!.className).toContain("h-[400px]");

    // The actual chart shows up after the dynamic import resolves.
    await waitFor(() => {
      expect(container.querySelector('[data-testid="sankey-chart"]')).not.toBeNull();
    });
  });

  it("mounts the real funnel component (not the skeleton) once loaded", async () => {
    const { SankeyFunnel } = await import("../sankey-funnel-lazy");
    const { container } = render(<SankeyFunnel data={makeFunnelData()} />);

    await waitFor(() => {
      expect(container.querySelector('[data-testid="sankey-chart"]')).not.toBeNull();
    });

    // After resolution the skeleton is gone.
    expect(container.querySelector('[aria-hidden="true"]')).toBeNull();
  });
});
