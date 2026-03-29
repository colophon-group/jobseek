import { NextRequest, NextResponse } from "next/server";

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
