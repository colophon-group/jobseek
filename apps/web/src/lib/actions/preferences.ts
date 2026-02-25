"use server";

import { eq } from "drizzle-orm";
import { headers } from "next/headers";
import { db } from "@/db";
import { account, userPreferences } from "@/db/schema";
import { withRLS } from "@/db/rls";
import { auth } from "@/lib/auth";
import { getSession } from "@/lib/sessionCache";

const PASSWORD_RESET_COOLDOWN_SECONDS = 60;

export async function getPreferences() {
  const session = await getSession();
  if (!session) return null;

  const row = await withRLS(session.user.id, async (tx) => {
    const [result] = await tx
      .select()
      .from(userPreferences)
      .where(eq(userPreferences.userId, session.user.id))
      .limit(1);
    return result ?? null;
  });

  return row;
}

export async function updatePreferences(
  data: {
    theme?: "light" | "dark";
    locale?: "en" | "de" | "fr" | "it";
    cookieConsent?: boolean;
  },
) {
  const session = await getSession();
  if (!session) throw new Error("Not authenticated");

  const row = await withRLS(session.user.id, async (tx) => {
    const [result] = await tx
      .insert(userPreferences)
      .values({
        userId: session.user.id,
        theme: data.theme ?? "light",
        locale: data.locale ?? "en",
        cookieConsent: data.cookieConsent ?? false,
      })
      .onConflictDoUpdate({
        target: userPreferences.userId,
        set: {
          ...(data.theme !== undefined && { theme: data.theme }),
          ...(data.locale !== undefined && { locale: data.locale }),
          ...(data.cookieConsent !== undefined && { cookieConsent: data.cookieConsent }),
          updatedAt: new Date(),
        },
      })
      .returning();
    return result;
  });

  return row;
}

export async function getPasswordResetCooldown(): Promise<number> {
  const session = await getSession();
  if (!session) return 0;

  const row = await withRLS(session.user.id, async (tx) => {
    const [result] = await tx
      .select({ lastPasswordResetAt: userPreferences.lastPasswordResetAt })
      .from(userPreferences)
      .where(eq(userPreferences.userId, session.user.id))
      .limit(1);
    return result ?? null;
  });

  if (!row?.lastPasswordResetAt) return 0;

  const elapsed = Math.floor((Date.now() - row.lastPasswordResetAt.getTime()) / 1000);
  return Math.max(0, PASSWORD_RESET_COOLDOWN_SECONDS - elapsed);
}

export async function recordPasswordResetRequest(): Promise<{ error?: string; cooldown?: number }> {
  const session = await getSession();
  if (!session) return { error: "Not authenticated" };

  const remaining = await getPasswordResetCooldown();
  if (remaining > 0) {
    return { cooldown: remaining };
  }

  await withRLS(session.user.id, async (tx) => {
    await tx
      .insert(userPreferences)
      .values({
        userId: session.user.id,
        theme: "light",
        locale: "en",
        cookieConsent: false,
        lastPasswordResetAt: new Date(),
      })
      .onConflictDoUpdate({
        target: userPreferences.userId,
        set: {
          lastPasswordResetAt: new Date(),
          updatedAt: new Date(),
        },
      });
  });

  return {};
}

export async function setPassword(newPassword: string): Promise<{ error?: string }> {
  const session = await getSession();
  if (!session) return { error: "Not authenticated" };

  try {
    await auth.api.setPassword({
      body: { newPassword },
      headers: await headers(),
    });
    return {};
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : "Failed to set password";
    return { error: message };
  }
}

/**
 * Returns everything the account settings page needs in a single call.
 * Called from the page server component to avoid client-side fetches.
 */
export async function getAccountPageData() {
  const session = await getSession();
  if (!session) return null;

  const accounts = await db
    .select({ providerId: account.providerId, accountId: account.accountId })
    .from(account)
    .where(eq(account.userId, session.user.id));

  return {
    accounts: accounts.map((a) => ({ providerId: a.providerId, accountId: a.accountId })),
    hasPassword: accounts.some((a) => a.providerId === "credential"),
  };
}
