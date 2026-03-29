/**
 * Paywall guard for agent-facing API endpoints.
 *
 * Two auth pathways:
 *
 * 1. Stripe subscription  — Bearer token = user UUID from Job Seek portal
 *    → unlimited calls while subscription is active
 *
 * 2. Crypto credits       — Bearer token = opaque token returned by POST /agentic/api/pay
 *    → fixed number of calls per on-chain payment; 402 when credits exhausted
 *
 * Return values:
 *   { ok: true }           — authorised, proceed
 *   { ok: false, response} — return this response to the caller
 */
import { type NextRequest, NextResponse } from "next/server";
import { eq, sql } from "drizzle-orm";
import { db } from "@/db";
import { user, subscription, apiCredit } from "@/db/schema";

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL ?? "https://jseek.co";
const PORTAL_URL = process.env.NEXT_PUBLIC_PORTAL_URL ?? `${SITE_URL}/agentic`;

// Price: 0.001 ETH = 1 000 calls
export const PRICE_WEI = BigInt("1000000000000000"); // 0.001 ETH
export const CREDITS_PER_PAYMENT = 1000;
export const API_WALLET = process.env.API_WALLET_ADDRESS ?? "0xBA4704538C1E76979AF56F42345Ec040e1199823";

export type PaywallOk = { ok: true };
export type PaywallDenied = { ok: false; response: NextResponse };
export type PaywallResult = PaywallOk | PaywallDenied;

export async function checkPaywall(req: NextRequest): Promise<PaywallResult> {
  const auth = req.headers.get("authorization") ?? "";
  const [scheme, token] = auth.split(" ");

  if (scheme !== "Bearer" || !token) {
    return deny(401, {
      error: "Unauthorized",
      message: "Provide a bearer token. Two options: (1) your Job Seek user ID for subscription access, or (2) a crypto credit token from POST /agentic/api/pay.",
      docs: PORTAL_URL,
    });
  }

  // ── Try crypto credit token first (opaque UUID-like strings) ──────────────
  const creditRows = await db
    .select({
      id: apiCredit.id,
      creditsGranted: apiCredit.creditsGranted,
      creditsUsed: apiCredit.creditsUsed,
    })
    .from(apiCredit)
    .where(eq(apiCredit.token, token))
    .limit(1);

  if (creditRows.length) {
    const credit = creditRows[0];
    const remaining = credit.creditsGranted - credit.creditsUsed;

    if (remaining <= 0) {
      return deny(402, {
        error: "Credit Exhausted",
        message: `Your ${credit.creditsGranted}-call credit has been fully used. Purchase more credits to continue.`,
        payTo: API_WALLET,
        priceWei: PRICE_WEI.toString(),
        priceEth: "0.001",
        callsPerPayment: CREDITS_PER_PAYMENT,
        payEndpoint: `${PORTAL_URL}/api/pay`,
      });
    }

    // Deduct one credit atomically
    await db
      .update(apiCredit)
      .set({ creditsUsed: sql`${apiCredit.creditsUsed} + 1` })
      .where(eq(apiCredit.id, credit.id));

    return { ok: true };
  }

  // ── Try Stripe subscription (token = userId) ──────────────────────────────
  let userRows: { userId: string; email: string; plan: string | null; status: string | null }[] = [];
  try {
    userRows = await db
      .select({
        userId: user.id,
        email: user.email,
        plan: subscription.plan,
        status: subscription.status,
      })
      .from(user)
      .leftJoin(subscription, eq(subscription.userId, user.id))
      .where(eq(user.id, token))
      .limit(1);
  } catch {
    return deny(500, { error: "Internal server error" });
  }

  if (!userRows.length) {
    // Unknown token — tell the agent how to pay (it may just have no token yet)
    return deny(402, {
      error: "Payment Required",
      message: "Token not recognised. Pay on-chain to get a credit token, or sign up at " + SITE_URL,
      payTo: API_WALLET,
      priceWei: PRICE_WEI.toString(),
      priceEth: "0.001",
      callsPerPayment: CREDITS_PER_PAYMENT,
      payEndpoint: `${PORTAL_URL}/api/pay`,
      docs: PORTAL_URL,
    });
  }

  const row = userRows[0];
  if (!row.plan || row.status !== "active") {
    const checkoutUrl =
      `${PORTAL_URL}/api/checkout?userId=${encodeURIComponent(row.userId)}` +
      `&successUrl=${encodeURIComponent(PORTAL_URL + "?subscribed=1")}` +
      `&cancelUrl=${encodeURIComponent(PORTAL_URL)}`;

    return deny(402, {
      error: "Payment Required",
      message: "Active subscription required. Pay via Stripe or buy crypto credits.",
      // Stripe path
      subscribe: SITE_URL + "/en/pricing",
      checkoutUrl,
      // Crypto path
      payTo: API_WALLET,
      priceWei: PRICE_WEI.toString(),
      priceEth: "0.001",
      callsPerPayment: CREDITS_PER_PAYMENT,
      payEndpoint: `${PORTAL_URL}/api/pay`,
    });
  }

  return { ok: true };
}

function deny(status: number, body: Record<string, unknown>): PaywallDenied {
  return { ok: false, response: NextResponse.json(body, { status }) };
}
