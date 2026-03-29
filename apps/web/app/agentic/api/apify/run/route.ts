import { NextRequest, NextResponse } from "next/server";
import { triggerOrchestratorRun } from "@/lib/agentic/apify";

export async function POST(req: NextRequest) {
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
