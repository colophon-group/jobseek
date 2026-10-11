import { timingSafeEqual } from "node:crypto";
import { inspectAiFilterRecovery } from "@/lib/ai-filter/recovery-service";
import { isUuid } from "@/lib/ai-filter/route-utils";
import { runAiFilterRefreshSweep } from "@/lib/ai-filter/refresh-service";

export const maxDuration = 60;
const headers = { "Cache-Control": "private, no-store" };

export async function GET(request: Request) {
  const secret = process.env.AI_FILTER_REFRESH_SECRET;
  if (!secret) return Response.json({ error: "unavailable" }, { status: 503, headers });
  const actual = Buffer.from(request.headers.get("authorization") ?? "");
  const expected = Buffer.from(`Bearer ${secret}`);
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
    return Response.json({ error: "unauthorized" }, { status: 401, headers });
  }
  try {
    const inspect = new URL(request.url).searchParams.get("inspect");
    if (inspect!==null) {
      if (!isUuid(inspect)) return Response.json({error:"invalid_scope"},{status:400,headers});
      return Response.json({contractVersion:"narrowed-worker-v2",recovery:await inspectAiFilterRecovery(inspect)},{headers});
    }
    const result = await runAiFilterRefreshSweep();
    console.info("ai_filter_refresh", result);
    return Response.json({ contractVersion: "narrowed-worker-v2", ...result }, { status: result.failed ? 503 : 200, headers });
  } catch {
    console.error("ai_filter_refresh_failed");
    return Response.json({ error: "refresh_unavailable" }, { status: 503, headers });
  }
}
