import { auth } from "@/lib/auth";
import { toNextJsHandler } from "better-auth/next-js";
import { authLimiter, getClientIp } from "@/lib/rate-limit";
import { type NextRequest, NextResponse } from "next/server";

const { GET: authGet, POST: authPost } = toNextJsHandler(auth);

function rateLimitResponse(reset: number): NextResponse {
  const retryAfter = Math.ceil((reset - Date.now()) / 1000);
  return new NextResponse("Too Many Requests", {
    status: 429,
    headers: {
      "Retry-After": String(Math.max(1, retryAfter)),
    },
  });
}

export async function GET(request: NextRequest) {
  return authGet(request);
}

export async function POST(request: NextRequest) {
  try {
    const ip = getClientIp(request.headers);
    const { success, reset } = await authLimiter.limit(ip);
    if (!success) return rateLimitResponse(reset);
  } catch {
    // Redis unavailable — allow request through
  }
  return authPost(request);
}
