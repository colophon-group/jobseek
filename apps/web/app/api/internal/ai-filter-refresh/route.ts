import { timingSafeEqual } from "node:crypto";
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
    const result = await runAiFilterRefreshSweep();
    console.info("ai_filter_refresh", result);
    return Response.json({ contractVersion: "narrowed-refresh-v1", ...result }, { status: result.failed ? 503 : 200, headers });
  } catch {
    console.error("ai_filter_refresh_failed");
    return Response.json({ error: "refresh_unavailable" }, { status: 503, headers });
  }
}
