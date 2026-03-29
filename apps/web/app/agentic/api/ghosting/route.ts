/**
 * POST /agentic/api/ghosting
 *
 * Triggers the wayback-job-history Apify actor to research whether a company
 * (or a specific role at that company) exhibits ghost-job patterns.
 *
 * Request body:
 *   portalUrl    {string}  required — career page URL (e.g. https://boards.greenhouse.io/stripe)
 *   companyName  {string}  optional — human-readable name for reports
 *   position     {string}  optional — position title to filter results (passed through to GET)
 *   inventoryMode {boolean} optional — CDX inventory mode for Workday/SPA portals (default false)
 *   maxSnapshots {number}  optional — max daily snapshots to process (default 100)
 *   delayMs      {number}  optional — ms between Wayback requests (default 1500)
 *
 * Response: { runId, status }
 * Poll GET /agentic/api/ghosting/:runId?position=<title> for status/results.
 *
 * @example
 * // 1. Kick off a ghost-job analysis for Rippling (Lever board),
 * //    scoped to "Staff Engineer" roles.
 *
 * const res = await fetch('/agentic/api/ghosting', {
 *   method: 'POST',
 *   headers: { 'Content-Type': 'application/json' },
 *   body: JSON.stringify({
 *     portalUrl:   'https://jobs.lever.co/rippling',
 *     companyName: 'Rippling',
 *     maxSnapshots: 60,
 *   }),
 * });
 * const { runId } = await res.json();
 * // → { runId: "HiZq3xNbPvFe9aW", status: "RUNNING" }
 *
 * // 2. Poll until finished (status transitions: RUNNING → SUCCEEDED | FAILED).
 *
 * const poll = await fetch(
 *   `/agentic/api/ghosting/${runId}?position=Staff+Engineer`,
 * );
 * const data = await poll.json();
 *
 * // While still running:
 * // {
 * //   runId: "HiZq3xNbPvFe9aW",
 * //   status: "RUNNING",
 * //   finishedAt: null,
 * //   result: null
 * // }
 *
 * // When complete:
 * // {
 * //   runId: "HiZq3xNbPvFe9aW",
 * //   status: "SUCCEEDED",
 * //   finishedAt: "2026-03-29T14:22:07.000Z",
 * //   result: {
 * //     company: "Rippling",
 * //     portalUrl: "https://jobs.lever.co/rippling",
 * //     analysisDate: "2026-03-29",
 * //     periodStart: "2025-03-29",
 * //     periodEnd: "2026-03-29",
 * //     totalUniqueJobs: 184,
 * //     ghostCandidates: 31,
 * //     ghostRate: 0.17,
 * //     medianDurationDays: 42,
 * //     avgDurationDays: 67,
 * //     overallGhostRisk: 58,
 * //     hiringHealthScore: 42,
 * //     recommendation: "Proceed with caution",
 * //     topGhostRoles: ["Staff Engineer, Identity", "Staff Engineer, Platform"],
 * //     patterns: [
 * //       "Engineering roles reposted every 90 days with no visible hires",
 * //       "Identical JD re-listed under different requisition IDs"
 * //     ],
 * //     geminiSummary: "Rippling shows a moderate ghost-job signal...",
 * //     geminiAvailable: true,
 * //     matchingJobs: [
 * //       {
 * //         title: "Staff Engineer, Identity",
 * //         url: "https://jobs.lever.co/rippling/a1b2c3d4",
 * //         firstSeen: "2025-04-11",
 * //         lastSeen: "2026-03-20",
 * //         durationDays: 343,
 * //         archiveCount: 18,
 * //         reposted: true,
 * //         ghostScore: 84,
 * //         ghostReason: "Open 343 days, reposted twice, no hire signal"
 * //       }
 * //     ]
 * //   }
 * // }
 */
import { NextRequest, NextResponse } from "next/server";
import { triggerGhostingRun } from "@/lib/agentic/apify";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const {
      portalUrl,
      companyName,
      inventoryMode,
      maxSnapshots,
      delayMs,
      // position is not forwarded to the actor — it's a client-side filter
      // used when polling GET /agentic/api/ghosting/:runId?position=...
    } = body as Record<string, unknown>;

    if (!portalUrl || typeof portalUrl !== "string") {
      return NextResponse.json(
        { error: "portalUrl is required" },
        { status: 400 },
      );
    }

    const run = await triggerGhostingRun({
      portalUrl,
      ...(companyName != null && { companyName: String(companyName) }),
      ...(inventoryMode != null && { inventoryMode: Boolean(inventoryMode) }),
      ...(maxSnapshots != null && { maxSnapshots: Number(maxSnapshots) }),
      ...(delayMs != null && { delayMs: Number(delayMs) }),
    });

    return NextResponse.json({ runId: run.id, status: run.status });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
