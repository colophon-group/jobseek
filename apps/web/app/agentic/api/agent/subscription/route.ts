/**
 * GET /agentic/api/agent/subscription?email=...
 *
 * Returns the subscription status for a given user email.
 * Authenticated via Bearer token (AGENT_API_KEY).
 */
import { NextRequest, NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { user, subscription } from "@/db/schema";
import { verifyAgentKey, agentUnauthorized } from "@/lib/agentic/agentAuth";

export async function GET(req: NextRequest) {
  if (!verifyAgentKey(req)) return agentUnauthorized();

  const email = req.nextUrl.searchParams.get("email");
  if (!email) {
    return NextResponse.json({ error: "email query param required" }, { status: 400 });
  }

  const rows = await db
    .select({
      userId: user.id,
      email: user.email,
      name: user.name,
      plan: subscription.plan,
      status: subscription.status,
      startsAt: subscription.startsAt,
      endsAt: subscription.endsAt,
      stripeCustomerId: subscription.stripeCustomerId,
      stripeSubscriptionId: subscription.stripeSubscriptionId,
    })
    .from(user)
    .leftJoin(subscription, eq(subscription.userId, user.id))
    .where(eq(user.email, email))
    .limit(1);

  if (!rows.length) {
    return NextResponse.json({ error: "User not found" }, { status: 404 });
  }

  const row = rows[0];
  return NextResponse.json({
    userId: row.userId,
    email: row.email,
    name: row.name,
    subscription: row.plan
      ? {
          plan: row.plan,
          status: row.status,
          startsAt: row.startsAt,
          endsAt: row.endsAt,
          stripeCustomerId: row.stripeCustomerId,
          stripeSubscriptionId: row.stripeSubscriptionId,
        }
      : null,
  });
}
