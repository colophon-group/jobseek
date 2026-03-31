import { NextRequest, NextResponse } from "next/server";
import { checkPassword, createSessionToken, SESSION_COOKIE, SESSION_TTL_SECONDS } from "@/lib/agentic/auth";
import { agenticLoginLimiter } from "@/lib/rate-limit";

export async function POST(req: NextRequest) {
  // Brute-force protection: 5 attempts per 15 minutes per IP
  const ip =
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown";
  try {
    const { success, reset } = await agenticLoginLimiter.limit(ip);
    if (!success) {
      const retryAfter = Math.ceil((reset - Date.now()) / 1000);
      return new NextResponse("Too Many Requests", {
        status: 429,
        headers: { "Retry-After": String(Math.max(1, retryAfter)) },
      });
    }
  } catch {
    // Redis unavailable — allow request through
  }

  const body = await req.json().catch(() => ({}));
  const { password } = body as { password?: string };

  if (!password || !checkPassword(password)) {
    return NextResponse.json({ error: "Invalid password" }, { status: 401 });
  }

  const token = await createSessionToken();
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "strict",
    maxAge: SESSION_TTL_SECONDS,
    path: "/",
  });
  return res;
}
