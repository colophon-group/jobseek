/**
 * GET /agentic/api/ghosting/paid/:runId[?position=<title>]
 *
 * Paywalled results endpoint — same paywall as POST /agentic/api/ghosting/paid.
 * One credit is deducted per call, so poll conservatively.
 *
 * Returns run status while the actor is running; full ghost-analysis when done.
 * See POST /agentic/api/ghosting/paid for auth details and response shape.
 */
import { NextRequest, NextResponse } from "next/server";
import { checkPaywall } from "@/lib/agentic/apiPaywall";
import { getGhostingResult } from "@/lib/agentic/apify";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ runId: string }> },
) {
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
