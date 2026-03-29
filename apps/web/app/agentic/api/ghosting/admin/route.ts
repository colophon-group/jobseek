/**
 * POST /agentic/api/ghosting/admin
 *
 * Admin-only version of the ghosting analysis endpoint.
 * Requires the header:  Authorization: Auth Bearer Basic <secret>
 *
 * The secret is set via the GHOSTING_ADMIN_SECRET environment variable.
 * Returns 401 if the header is missing or the secret does not match.
 *
 * Request body: same as POST /agentic/api/ghosting
 *   portalUrl    {string}  required
 *   companyName  {string}  optional
 *   inventoryMode {boolean} optional
 *   maxSnapshots {number}  optional
 *   delayMs      {number}  optional
 *
 * @example
 * // ── Authorized request (GHOSTING_ADMIN_SECRET=magic) ───────────────────────
 * const res = await fetch('/agentic/api/ghosting/admin', {
 *   method: 'POST',
 *   headers: {
 *     'Content-Type': 'application/json',
 *     'Authorization': 'Auth Bearer Basic magic',
 *   },
 *   body: JSON.stringify({
 *     portalUrl:   'https://boards.greenhouse.io/openai',
 *     companyName: 'OpenAI',
 *     maxSnapshots: 80,
 *   }),
 * });
 * // → 200  { runId: "Kx9mTqLzRpWv2nB", status: "RUNNING" }
 *
 * // ── Wrong or missing secret ─────────────────────────────────────────────────
 * // → 401  { error: "Unauthorized: Authorization: Auth Bearer Basic <secret> required" }
 *
 * // Poll for results:
 * // GET /agentic/api/ghosting/admin/:runId[?position=<title>]
 */
import { NextRequest, NextResponse } from "next/server";
import {
  verifyGhostingAdminKey,
  ghostingAdminUnauthorized,
} from "@/lib/agentic/agentAuth";
import { triggerGhostingRun } from "@/lib/agentic/apify";

export async function POST(req: NextRequest) {
  if (!verifyGhostingAdminKey(req)) return ghostingAdminUnauthorized();

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
