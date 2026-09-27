import { timingSafeEqual } from "node:crypto";
import { runJobAlerts } from "@/lib/services/notification-runner";

export const maxDuration = 300;
const headers = { "Cache-Control": "private, no-store" };
export async function GET(request: Request) {
  const secret = process.env.CRON_SECRET;
  if (!secret) return Response.json({ error: "unavailable" }, { status: 503, headers });
  const actual = Buffer.from(request.headers.get("authorization") ?? "");
  const expected = Buffer.from(`Bearer ${secret}`);
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected)) {
    return Response.json({ error: "unauthorized" }, { status: 401, headers });
  }
  try { return Response.json(await runJobAlerts(), { headers }); }
  catch {
    console.error("job_alerts_run_failed");
    return Response.json({ error: "notification_run_failed" }, { status: 503, headers });
  }
}
