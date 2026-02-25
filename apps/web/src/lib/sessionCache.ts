import "server-only";
import { cache } from "react";
import { headers, cookies } from "next/headers";
import { eq, and, gt } from "drizzle-orm";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { session } from "@/db/schema";

/**
 * Per-request cached session getter (full user object).
 *
 * React's `cache()` deduplicates calls within a single server render,
 * so the (app) layout, page component, and any server actions called
 * during SSR all share a single `getSession()` DB query.
 */
export const getSession = cache(async () => {
  return auth.api.getSession({ headers: await headers() });
});

/**
 * Lightweight session check — returns just the userId.
 *
 * Reads the session cookie directly and does a single minimal DB lookup,
 * bypassing Better Auth's full validation pipeline. Use this for
 * fire-and-forget server actions where the full user object isn't needed.
 */
export async function getSessionUserId(): Promise<string | null> {
  const cookieStore = await cookies();
  const token =
    cookieStore.get("__Secure-better-auth.session_token")?.value ??
    cookieStore.get("better-auth.session_token")?.value;
  if (!token) {
    return null;
  }

  const [row] = await db
    .select({ userId: session.userId })
    .from(session)
    .where(and(eq(session.token, token), gt(session.expiresAt, new Date())))
    .limit(1);

  return row?.userId ?? null;
}
