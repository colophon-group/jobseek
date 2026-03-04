import { auth } from "@/lib/auth";
import { toNextJsHandler } from "better-auth/next-js";
import { authLimiter } from "@/lib/rate-limit";
import { type NextRequest, NextResponse } from "next/server";

const { GET: authGet, POST: authPost } = toNextJsHandler(auth);

function getClientIp(request: NextRequest): string {
  return (
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ?? "unknown"
  );
}

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
  const ip = getClientIp(request);
  const { success, reset } = await authLimiter.limit(ip);
  if (!success) return rateLimitResponse(reset);
  return authPost(request);
}
