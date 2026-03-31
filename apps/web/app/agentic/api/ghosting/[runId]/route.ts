/**
 * GET /agentic/api/ghosting/:runId[?position=<title>]
 *
 * Returns the status and (when complete) the ghost-job analysis result for
 * a run previously triggered via POST /agentic/api/ghosting.
 *
 * While the actor is still running:
 *   { runId, status, finishedAt: null, result: null }
 *
 * When the run succeeds:
 *   {
 *     runId, status, finishedAt,
 *     result: {
 *       company, portalUrl, analysisDate, periodStart, periodEnd,
 *       totalUniqueJobs, ghostCandidates, ghostRate,
 *       medianDurationDays, avgDurationDays,
 *       overallGhostRisk, hiringHealthScore, recommendation,
 *       topGhostRoles, patterns, geminiSummary, geminiAvailable,
 *       matchingJobs  // job records filtered by ?position= (all if omitted)
 *     }
 *   }
 *
 * Query params:
 *   position  {string}  optional — filter matchingJobs to titles containing this string
 */
import { NextRequest, NextResponse } from "next/server";
import { checkPaywall } from "@/lib/agentic/apiPaywall";
import { getGhostingResult } from "@/lib/agentic/apify";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ runId: string }> },
) {
  // Require payment / valid subscription to read actor results
  const gate = await checkPaywall(req);
  if (!gate.ok) return gate.response;

  try {
    const { runId } = await params;
    const position = req.nextUrl.searchParams.get("position") ?? undefined;
    const result = await getGhostingResult(runId, position);
    return NextResponse.json(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
