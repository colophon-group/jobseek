/**
 * GET /agentic/api/ping
 *
 * Lightweight paywall-gated endpoint for testing credit consumption.
 * Returns { pong: true, creditsRemaining: number } without hitting the web app.
 */
import { type NextRequest, NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { apiCredit } from "@/db/schema";
import { checkPaywall } from "@/lib/agentic/apiPaywall";

export async function GET(req: NextRequest) {
  const auth = req.headers.get("authorization") ?? "";
  const token = auth.split(" ")[1] ?? "";

  const paywall = await checkPaywall(req);
  if (!paywall.ok) return paywall.response;

  // Return remaining credits for visibility
  const rows = await db
    .select({ creditsGranted: apiCredit.creditsGranted, creditsUsed: apiCredit.creditsUsed })
    .from(apiCredit)
    .where(eq(apiCredit.token, token))
    .limit(1);

  const remaining = rows.length ? rows[0].creditsGranted - rows[0].creditsUsed : null;

  return NextResponse.json({ pong: true, creditsRemaining: remaining });
}
