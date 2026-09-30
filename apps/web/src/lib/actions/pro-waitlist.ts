"use server";

import { headers } from "next/headers";
import { db } from "@/db";
import { proWaitlist } from "@/db/schema";
import { defaultLocale, isLocale } from "@/lib/i18n";
import { getClientIp, proWaitlistLimiter } from "@/lib/rate-limit";
import { logExternalError } from "@/lib/safe-external-error";

export type ProWaitlistResult =
  | { success: true }
  | { error: "invalid_email" | "rate_limited" | "unavailable" };

/** Public opt-in: no account required, and existing addresses are never disclosed. */
export async function joinProWaitlist(email: string, locale: string): Promise<ProWaitlistResult> {
  if (typeof email !== "string") return { error: "invalid_email" };
  const normalizedEmail = email.trim().toLowerCase();
  if (
    normalizedEmail.length > 254 ||
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail) ||
    normalizedEmail.split("@")[0].length > 64
  ) {
    return { error: "invalid_email" };
  }

  try {
    const limit = await proWaitlistLimiter.limit(getClientIp(await headers()));
    // Upstash otherwise allows requests on timeout; do not accept unbounded writes.
    if (limit.reason === "timeout") return { error: "unavailable" };
    if (!limit.success) return { error: "rate_limited" };
  } catch (error) {
    logExternalError("warn", { service: "redis", operation: "pro_waitlist_limit" }, error);
    return { error: "unavailable" };
  }

  try {
    await db.insert(proWaitlist).values({
      email: normalizedEmail,
      locale: typeof locale === "string" && isLocale(locale) ? locale : defaultLocale,
    }).onConflictDoNothing({ target: proWaitlist.email });
    return { success: true };
  } catch (error) {
    logExternalError("error", { service: "database", operation: "join_pro_waitlist" }, error);
    return { error: "unavailable" };
  }
}
