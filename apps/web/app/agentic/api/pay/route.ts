/**
 * POST /agentic/api/pay
 *
 * Agent submits an on-chain payment tx hash. We verify it on Sepolia,
 * confirm the recipient is the API wallet and the value >= PRICE_WEI,
 * then create an api_credit row and return a bearer token.
 *
 * Body: { txHash: string, walletAddress: string }
 * Returns: { token: string, creditsGranted: number, creditsRemaining: number }
 */
import { type NextRequest, NextResponse } from "next/server";
import { eq } from "drizzle-orm";
import { db } from "@/db";
import { apiCredit } from "@/db/schema";
import { PRICE_WEI, CREDITS_PER_PAYMENT, API_WALLET } from "@/lib/agentic/apiPaywall";
import crypto from "node:crypto";

const SEPOLIA_RPC =
  process.env.SEPOLIA_RPC_URL ?? "https://ethereum-sepolia-rpc.publicnode.com";

async function rpc(method: string, params: unknown[]) {
  const res = await fetch(SEPOLIA_RPC, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const json = await res.json();
  if (json.error) throw new Error(json.error.message);
  return json.result;
}

export async function POST(req: NextRequest) {
  let body: { txHash?: string; walletAddress?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON" }, { status: 400 });
  }

  const { txHash, walletAddress } = body;
  if (!txHash || !walletAddress) {
    return NextResponse.json(
      { error: "txHash and walletAddress are required" },
      { status: 400 },
    );
  }

  // Idempotency — same tx hash → return existing credit
  const existing = await db
    .select({ token: apiCredit.token, creditsGranted: apiCredit.creditsGranted, creditsUsed: apiCredit.creditsUsed })
    .from(apiCredit)
    .where(eq(apiCredit.txHash, txHash.toLowerCase()))
    .limit(1);

  if (existing.length) {
    const c = existing[0];
    return NextResponse.json({
      token: c.token,
      creditsGranted: c.creditsGranted,
      creditsRemaining: c.creditsGranted - c.creditsUsed,
      message: "Token already issued for this transaction.",
    });
  }

  // Fetch transaction receipt from Sepolia
  let receipt: { to: string; status: string } | null = null;
  let tx: { to: string; value: string } | null = null;
  try {
    receipt = await rpc("eth_getTransactionReceipt", [txHash]);
    tx = await rpc("eth_getTransactionByHash", [txHash]);
  } catch (e) {
    return NextResponse.json({ error: "Failed to fetch transaction: " + (e as Error).message }, { status: 502 });
  }

  if (!receipt || !tx) {
    return NextResponse.json(
      { error: "Transaction not found or not yet mined. Try again in a few seconds." },
      { status: 404 },
    );
  }

  if (receipt.status !== "0x1") {
    return NextResponse.json({ error: "Transaction failed on-chain." }, { status: 400 });
  }

  if (receipt.to?.toLowerCase() !== API_WALLET.toLowerCase()) {
    return NextResponse.json(
      { error: `Transaction recipient must be ${API_WALLET}. Got: ${receipt.to}` },
      { status: 400 },
    );
  }

  const valueWei = BigInt(tx.value);
  if (valueWei < PRICE_WEI) {
    return NextResponse.json(
      {
        error: `Insufficient payment. Required: ${PRICE_WEI} wei (0.001 ETH). Got: ${valueWei} wei.`,
      },
      { status: 402 },
    );
  }

  // Calculate credits (proportional if they overpaid)
  const creditsGranted = Math.floor(Number(valueWei / PRICE_WEI) * CREDITS_PER_PAYMENT);
  const token = crypto.randomUUID();

  await db.insert(apiCredit).values({
    token,
    walletAddress: walletAddress.toLowerCase(),
    txHash: txHash.toLowerCase(),
    chainId: 11155111,
    amountWei: valueWei.toString(),
    creditsGranted,
    creditsUsed: 0,
  });

  return NextResponse.json({
    token,
    creditsGranted,
    creditsRemaining: creditsGranted,
    message: `Payment verified. You have ${creditsGranted} API calls. Use Authorization: Bearer ${token}`,
  });
}
