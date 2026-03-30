/**
 * POST /agentic/api/discovery/trigger
 *
 * Manually triggers a fresh company-discovery-actor run.
 * Results will appear in GET /agentic/api/discovery once the run completes
 * (typically 15–30 minutes).
 *
 * Request body (all optional):
 *   sources  {string[]}  — Override the default sources list
 *   enableAiDiscovery {boolean} — Enable Gemini portal discovery (default true)
 *
 * Response:
 *   { runId: string, status: string }
 *
 * Poll GET /api/apify/status?runId=<runId> to track progress.
 */
import { NextRequest, NextResponse } from "next/server";
import { triggerDiscoveryRun } from "@/lib/agentic/apify";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const run = await triggerDiscoveryRun(body as Record<string, unknown>);
    return NextResponse.json({ runId: run.id, status: run.status });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
