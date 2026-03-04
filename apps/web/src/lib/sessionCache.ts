import "server-only";
import { cache } from "react";
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { redis } from "@/lib/redis";

const SESSION_TTL = 300; // 5 minutes

type SessionResult = Awaited<ReturnType<typeof auth.api.getSession>>;

function extractToken(cookieHeader: string): string | null {
  for (const part of cookieHeader.split(";")) {
    const trimmed = part.trim();
    if (trimmed.startsWith("__Secure-better-auth.session_token=")) {
      return trimmed.slice("__Secure-better-auth.session_token=".length);
    }
    if (trimmed.startsWith("better-auth.session_token=")) {
      return trimmed.slice("better-auth.session_token=".length);
    }
  }
  return null;
}

async function fetchSession(): Promise<SessionResult> {
  const headersList = await headers();
  const cookieHeader = headersList.get("cookie") ?? "";
  const token = extractToken(cookieHeader);
  if (!token) return null;

  // Try Redis cache first
  try {
    const cached = await redis.get<SessionResult>(`session:${token}`);
    if (cached) return cached;
  } catch {
    // Redis unavailable — fall through to DB
  }

  // Cache miss — fetch from DB via Better Auth
  const result = await auth.api.getSession({ headers: headersList });
  if (!result) return null;

  // Store in Redis for subsequent requests
  try {
    await redis.set(`session:${token}`, JSON.stringify(result), {
      ex: SESSION_TTL,
    });
  } catch {
    // Redis unavailable — still return the DB result
  }

  return result;
}

/**
 * Per-request cached session getter (full user object).
 *
 * Checks Redis first (shared across all serverless instances),
 * then falls back to Better Auth DB query on cache miss.
 * React's `cache()` deduplicates within a single server render.
 */
export const getSession = cache(fetchSession);

/**
 * Lightweight session check — returns just the userId.
 *
 * Uses the Redis-backed getSession() instead of a separate DB query.
 */
export async function getSessionUserId(): Promise<string | null> {
  const session = await getSession();
  return session?.user?.id ?? null;
}

/**
 * Invalidate a session in Redis cache.
 *
 * Called on sign-out, password change, session revocation, etc.
 */
export async function invalidateSessionCache(token: string): Promise<void> {
  try {
    await redis.del(`session:${token}`);
  } catch {
    // Best effort — TTL will clean up eventually
  }
}
