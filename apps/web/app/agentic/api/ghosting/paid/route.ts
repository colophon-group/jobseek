/**
 * POST /agentic/api/ghosting/paid
 *
 * Paywalled version of the ghosting analysis endpoint.
 * Requires a valid Bearer token — either:
 *   (a) a Job Seek user ID with an active Stripe subscription, or
 *   (b) a crypto credit token from POST /agentic/api/pay (0.001 ETH = 1 000 calls)
 *
 * Returns 402 Payment Required when no valid token is present or credits are exhausted.
 *
 * Request body: same as POST /agentic/api/ghosting
 *   portalUrl    {string}  required
 *   companyName  {string}  optional
 *   inventoryMode {boolean} optional
 *   maxSnapshots {number}  optional
 *   delayMs      {number}  optional
 *
 * @example
 * // ── With a crypto credit token ──────────────────────────────────────────────
 * const res = await fetch('/agentic/api/ghosting/paid', {
 *   method: 'POST',
 *   headers: {
 *     'Content-Type': 'application/json',
 *     'Authorization': 'Bearer crd_9f2a1b3c4d5e6f7a',  // from POST /agentic/api/pay
 *   },
 *   body: JSON.stringify({
 *     portalUrl:   'https://jobs.lever.co/rippling',
 *     companyName: 'Rippling',
 *     maxSnapshots: 60,
 *   }),
 * });
 * // → 200  { runId: "HiZq3xNbPvFe9aW", status: "RUNNING" }
 *
 * // ── When credits are exhausted ──────────────────────────────────────────────
 * // → 402 {
 * //     error: "Credit Exhausted",
 * //     message: "Your 1000-call credit has been fully used...",
 * //     payTo: "0xBA4704...",
 * //     priceEth: "0.001",
 * //     callsPerPayment: 1000,
 * //     payEndpoint: "https://jseek.co/agentic/api/pay"
 * //   }
 *
 * // ── With no token at all ────────────────────────────────────────────────────
 * // → 401 {
 * //     error: "Unauthorized",
 * //     message: "Provide a bearer token..."
 * //   }
 *
 * // Poll for results the same way as the open endpoint:
 * // GET /agentic/api/ghosting/paid/:runId[?position=<title>]
 */
import { NextRequest, NextResponse } from "next/server";
import { checkPaywall } from "@/lib/agentic/apiPaywall";
import { triggerGhostingRun } from "@/lib/agentic/apify";

export async function POST(req: NextRequest) {
  const gate = await checkPaywall(req);
  if (!gate.ok) return gate.response;

  try {
    const body = await req.json().catch(() => ({}));
    const { portalUrl, companyName, inventoryMode, maxSnapshots, delayMs } =
      body as Record<string, unknown>;

    if (!portalUrl || typeof portalUrl !== "string") {
      return NextResponse.json(
        { error: "portalUrl is required" },
        { status: 400 },
      );
    }

    const run = await triggerGhostingRun({
      portalUrl,
      ...(companyName != null && { companyName: String(companyName) }),
      ...(inventoryMode != null && { inventoryMode: Boolean(inventoryMode) }),
      ...(maxSnapshots != null && { maxSnapshots: Number(maxSnapshots) }),
      ...(delayMs != null && { delayMs: Number(delayMs) }),
    });

    return NextResponse.json({ runId: run.id, status: run.status });
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return NextResponse.json({ error: message }, { status: 500 });
  }
}
