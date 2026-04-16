import "server-only";
import { cache } from "react";
import { headers } from "next/headers";
import { auth } from "@/lib/auth";
import { redis } from "@/lib/redis";
import { db } from "@/db";
import { user } from "@/db/schema";

const SESSION_TTL = 300; // 5 minutes

type SessionResult = Awaited<ReturnType<typeof auth.api.getSession>>;

function isLocalDevAuthBypassEnabled(): boolean {
  return process.env.NODE_ENV !== "production"
    && process.env.LOCAL_DEV_AUTH_BYPASS === "true";
}

function getLocalDevUser() {
  return {
    id: process.env.LOCAL_DEV_AUTH_USER_ID ?? "local-dev-user",
    name: process.env.LOCAL_DEV_AUTH_NAME ?? "Local Dev",
    email: process.env.LOCAL_DEV_AUTH_EMAIL ?? "local-dev@example.com",
    username: process.env.LOCAL_DEV_AUTH_USERNAME ?? "local-dev",
    displayUsername: process.env.LOCAL_DEV_AUTH_DISPLAY_USERNAME ?? "local-dev",
    emailVerified: true,
    image: null,
  };
}

async function ensureLocalDevUser(): Promise<void> {
  const localUser = getLocalDevUser();
  await db
    .insert(user)
    .values({
      id: localUser.id,
      name: localUser.name,
      email: localUser.email,
      emailVerified: true,
      username: localUser.username,
      displayUsername: localUser.displayUsername,
      image: null,
    })
    .onConflictDoNothing();
}

async function getLocalDevSession(): Promise<SessionResult> {
  await ensureLocalDevUser();
  const localUser = getLocalDevUser();
  const now = new Date();
  const expiresAt = new Date(now.getTime() + 365 * 24 * 60 * 60 * 1000);

  return {
    user: localUser,
    session: {
      id: "local-dev-session",
      createdAt: now,
      updatedAt: now,
      expiresAt,
      token: "local-dev-session-token",
      userId: localUser.id,
      ipAddress: null,
      userAgent: "local-dev-auth-bypass",
    },
  } as SessionResult;
}

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
  if (isLocalDevAuthBypassEnabled()) {
    return getLocalDevSession();
  }

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
  let result: SessionResult;
  try {
    result = await auth.api.getSession({ headers: headersList });
  } catch {
    // DB unavailable (e.g. statement timeout) — treat as unauthenticated
    return null;
  }
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
