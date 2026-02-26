"use server";

import { eq } from "drizzle-orm";
import { headers } from "next/headers";
import { db } from "@/db";
import { account, userPreferences } from "@/db/schema";
import { auth } from "@/lib/auth";
import { getSession, getSessionUserId } from "@/lib/sessionCache";

const PASSWORD_RESET_COOLDOWN_SECONDS = 60;

export async function getPreferences() {
  const session = await getSession();
  if (!session) return null;

  const [row] = await db
    .select()
    .from(userPreferences)
    .where(eq(userPreferences.userId, session.user.id))
    .limit(1);

  return row ?? null;
}

export async function updatePreferences(
  data: {
    theme?: "light" | "dark";
    locale?: "en" | "de" | "fr" | "it";
    cookieConsent?: boolean;
    themeUpdatedAt?: string;
    localeUpdatedAt?: string;
  },
) {
  const userId = await getSessionUserId();
  if (!userId) return null;

  const [existing] = await db
    .select()
    .from(userPreferences)
    .where(eq(userPreferences.userId, userId))
    .limit(1);

  // For updates (existing row), enforce "only update if newer" per field
  if (existing) {
    const set: Record<string, unknown> = {
      updatedAt: new Date(),
    };

    if (data.cookieConsent !== undefined) {
      set.cookieConsent = data.cookieConsent;
    }

    // Theme: only update if incoming timestamp >= existing, or no existing timestamp
    if (data.theme !== undefined) {
      const incomingTs = data.themeUpdatedAt ? new Date(data.themeUpdatedAt) : null;
      const existingTs = existing.themeUpdatedAt;
      if (!existingTs || !incomingTs || incomingTs >= existingTs) {
        set.theme = data.theme;
        set.themeUpdatedAt = incomingTs ?? new Date();
      }
    }

    // Locale: only update if incoming timestamp >= existing, or no existing timestamp
    if (data.locale !== undefined) {
      const incomingTs = data.localeUpdatedAt ? new Date(data.localeUpdatedAt) : null;
      const existingTs = existing.localeUpdatedAt;
      if (!existingTs || !incomingTs || incomingTs >= existingTs) {
        set.locale = data.locale;
        set.localeUpdatedAt = incomingTs ?? new Date();
      }
    }

    const [row] = await db
      .update(userPreferences)
      .set(set)
      .where(eq(userPreferences.userId, userId))
      .returning();

    return row;
  }

  // Insert (new row): always write everything
  const [row] = await db
    .insert(userPreferences)
    .values({
      userId,
      theme: data.theme ?? "light",
      locale: data.locale ?? "en",
      cookieConsent: data.cookieConsent ?? false,
      themeUpdatedAt: data.themeUpdatedAt ? new Date(data.themeUpdatedAt) : new Date(),
      localeUpdatedAt: data.localeUpdatedAt ? new Date(data.localeUpdatedAt) : new Date(),
    })
    .onConflictDoUpdate({
      target: userPreferences.userId,
      set: {
        updatedAt: new Date(),
      },
    })
    .returning();

  return row;
}

export async function getPasswordResetCooldown(): Promise<number> {
  const session = await getSession();
  if (!session) return 0;

  const [row] = await db
    .select({ lastPasswordResetAt: userPreferences.lastPasswordResetAt })
    .from(userPreferences)
    .where(eq(userPreferences.userId, session.user.id))
    .limit(1);

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

  await db
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
