import { NextRequest, NextResponse } from "next/server";
import { checkPaywall } from "@/lib/agentic/apiPaywall";
import { getRunStatus } from "@/lib/agentic/apify";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ runId: string }> },
) {
  // Require payment / valid subscription before exposing run status
  const gate = await checkPaywall(req);
  if (!gate.ok) return gate.response;

  try {
    const { runId } = await params;
    const status = await getRunStatus(runId);
    return NextResponse.json(status);
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
