import { NextRequest, NextResponse } from "next/server";
import { verifyGhostingAdminKey, ghostingAdminUnauthorized } from "@/lib/agentic/agentAuth";
import { triggerOrchestratorRun } from "@/lib/agentic/apify";

/**
 * POST /agentic/api/apify/run
 *
 * Admin-only. Triggers the Apify orchestrator actor.
 * Requires: Authorization: Auth Bearer Basic <GHOSTING_ADMIN_SECRET>
 */
export async function POST(req: NextRequest) {
  if (!verifyGhostingAdminKey(req)) return ghostingAdminUnauthorized();

  try {
    const body = await req.json().catch(() => ({}));
    const defaultInput = process.env.APIFY_DEFAULT_INPUT
      ? JSON.parse(process.env.APIFY_DEFAULT_INPUT)
      : {};
    const input = { ...defaultInput, ...body };
    const run = await triggerOrchestratorRun(input);
    return NextResponse.json({ runId: run.id, status: run.status });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
