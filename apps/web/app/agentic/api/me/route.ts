/**
 * GET /agentic/api/me
 *
 * Returns the current user's ID and subscription status.
 * Bearer token = userId.
 *
 * Used by agents to verify a key is valid and check subscription tier.
 */
import { type NextRequest, NextResponse } from "next/server";
import { checkPaywall } from "@/lib/agentic/apiPaywall";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { user, subscription } from "@/db/schema";

export async function GET(req: NextRequest) {
  const result = await checkPaywall(req);
  if (!result.ok) return result.response;

  const auth = req.headers.get("authorization") ?? "";
  const token = auth.split(" ")[1] ?? "";

  const rows = await db
    .select({
      userId: user.id,
      email: user.email,
      name: user.name,
      plan: subscription.plan,
      status: subscription.status,
      endsAt: subscription.endsAt,
    })
    .from(user)
    .leftJoin(subscription, eq(subscription.userId, user.id))
    .where(eq(user.id, token))
    .limit(1);

  const row = rows[0];
  return NextResponse.json({
    userId: row.userId,
    email: row.email,
    name: row.name,
    subscription: row.plan
      ? { plan: row.plan, status: row.status, endsAt: row.endsAt }
      : null,
  });
}
