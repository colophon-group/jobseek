"use client";

import dynamic from "next/dynamic";
import type { FunnelData } from "@/lib/actions/my-jobs-stats";

/**
 * Lazy wrapper for the nivo Sankey funnel.
 *
 * The chart is below the fold on /my-jobs/stats and pulls in ~250 KB of
 * `@nivo/sankey` + `@nivo/core` + d3 dependencies. Loading it on demand
 * keeps the stats route bundle small (closes #3189).
 *
 * `ssr: false` is required because nivo uses browser-only APIs (e.g.
 * `window.matchMedia` and SVG measurement) that don't render meaningfully
 * during server prerender.
 *
 * The skeleton's height approximates the rendered funnel's minimum height
 * (~400 px) so swap-in doesn't shift surrounding content.
 */
export const SankeyFunnel = dynamic<{ data: FunnelData }>(
  () => import("./sankey-funnel").then((m) => m.SankeyFunnel),
  {
    ssr: false,
    loading: () => (
      <div
        aria-hidden
        className="h-[400px] animate-pulse rounded-md bg-muted/20"
      />
    ),
  },
);
