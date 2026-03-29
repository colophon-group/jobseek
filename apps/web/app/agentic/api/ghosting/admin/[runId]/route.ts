/**
 * GET /agentic/api/ghosting/admin/:runId[?position=<title>]
 *
 * Admin-only results endpoint — same auth as POST /agentic/api/ghosting/admin.
 * Header required:  Authorization: Auth Bearer Basic <secret>
 *
 * Returns run status while the actor is running; full ghost-analysis when done.
 * See POST /agentic/api/ghosting/admin for auth details and response shape.
 */
import { NextRequest, NextResponse } from "next/server";
import {
  verifyGhostingAdminKey,
  ghostingAdminUnauthorized,
} from "@/lib/agentic/agentAuth";
import { getGhostingResult } from "@/lib/agentic/apify";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ runId: string }> },
) {
  if (!verifyGhostingAdminKey(req)) return ghostingAdminUnauthorized();

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
