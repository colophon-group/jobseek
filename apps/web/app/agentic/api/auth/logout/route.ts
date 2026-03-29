import { NextResponse } from "next/server";
import { SESSION_COOKIE } from "@/lib/agentic/auth";

export async function POST() {
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, "", { maxAge: 0, path: "/" });
  return res;
}
