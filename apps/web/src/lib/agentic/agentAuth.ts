import { NextRequest, NextResponse } from "next/server";
import { timingSafeEqual } from "crypto";

export function verifyAgentKey(req: NextRequest): boolean {
  const expected = process.env.AGENT_API_KEY;
  if (!expected) return false;
  const auth = req.headers.get("authorization") ?? "";
  const [scheme, token] = auth.split(" ");
  return scheme === "Bearer" && token === expected;
}

export function agentUnauthorized() {
  return NextResponse.json(
    { error: "Unauthorized: valid Bearer token required" },
    { status: 401 },
  );
}

/**
 * Checks for the ghosting admin credential.
 *
 * Expected header:  Authorization: Auth Bearer Basic <secret>
 *
 * The secret is read from the GHOSTING_ADMIN_SECRET environment variable.
 * Uses timing-safe comparison to prevent timing-based secret enumeration.
 *
 * Example env:  GHOSTING_ADMIN_SECRET=magic
 */
export function verifyGhostingAdminKey(req: NextRequest): boolean {
  const expected = process.env.GHOSTING_ADMIN_SECRET;
  if (!expected) return false;

  const auth = req.headers.get("authorization") ?? "";
  // Header format: "Auth Bearer Basic <token>"
  // Everything after the fixed three-word scheme is the secret.
  const prefix = "Auth Bearer Basic ";
  if (!auth.startsWith(prefix)) return false;

  const provided = auth.slice(prefix.length);
  if (provided.length === 0) return false;

  try {
    const a = Buffer.from(provided.padEnd(128));
    const b = Buffer.from(expected.padEnd(128));
    return (
      provided.length === expected.length &&
      timingSafeEqual(a.subarray(0, 128), b.subarray(0, 128))
    );
  } catch {
    return false;
  }
}

export function ghostingAdminUnauthorized() {
  return NextResponse.json(
    { error: "Unauthorized: Authorization: Auth Bearer Basic <secret> required" },
    { status: 401 },
  );
}
