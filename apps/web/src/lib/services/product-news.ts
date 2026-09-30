import "server-only";
import { desc, eq, sql } from "drizzle-orm";
import { db } from "@/db";
import { productNewsConsent, user } from "@/db/schema";
import { defaultLocale, isLocale, type Locale } from "@/lib/i18n";
import { PRODUCT_NEWS_CONSENT_TEXT, PRODUCT_NEWS_CONSENT_VERSION, normalizeProductNewsEmail, type ProductNewsSource } from "@/lib/product-news/policy";
import { productNewsConsentId, verifyProductNewsUnsubscribeToken } from "@/lib/product-news/unsubscribe-token";

type Transaction = Parameters<Parameters<typeof db.transaction>[0]>[0];

async function latestChoice(tx: Transaction | typeof db, userId: string) {
  const [choice] = await tx.select().from(productNewsConsent)
    .where(eq(productNewsConsent.userId, userId)).orderBy(desc(productNewsConsent.sequence)).limit(1);
  return choice;
}

async function lockUser(tx: Transaction, userId: string) {
  const [owner] = await tx.select({ email: user.email }).from(user)
    .where(eq(user.id, userId)).for("update");
  return owner;
}

export async function getProductNewsPreference(userId: string) {
  const [owner] = await db.select({ email: user.email }).from(user).where(eq(user.id, userId));
  const choice = await latestChoice(db, userId);
  return { enabled: Boolean(owner && choice?.enabled && choice.email === normalizeProductNewsEmail(owner.email)) };
}

// The row lock serializes changes, including the first choice when there is no
// consent row yet. Event sequence, rather than timestamp, defines the latest choice.
export async function setProductNewsPreference(userId: string, enabled: boolean, locale: Locale, source: Exclude<ProductNewsSource, "unsubscribe">) {
  return db.transaction(async tx => {
    const owner = await lockUser(tx, userId);
    if (!owner) return { error: "user_not_found" as const };
    const email = normalizeProductNewsEmail(owner.email);
    const latest = await latestChoice(tx, userId);
    if (latest?.email === email && latest.enabled === enabled) return { enabled };
    // No consent record is needed merely to preserve the default opt-out.
    if (!latest && !enabled) return { enabled: false };
    await tx.insert(productNewsConsent).values({
      userId, email, enabled, locale, source,
      consentVersion: PRODUCT_NEWS_CONSENT_VERSION,
      consentText: PRODUCT_NEWS_CONSENT_TEXT[locale],
    });
    return { enabled };
  });
}

// Server-only audience lookup for a future campaign sender. Recheck immediately
// before submission; an exported list alone is never authority to keep sending.
// Provider bounce/complaint suppressions must also be respected by that sender.
export async function getProductNewsRecipient(userId: string) {
  const rows = await db.execute<{
    id: string; email: string; locale: Locale;
  } & Record<string, unknown>>(sql`
    SELECT c.id, c.email, c.locale FROM public."user" u
    JOIN LATERAL (
      SELECT * FROM public.product_news_consent WHERE user_id = u.id
      ORDER BY sequence DESC LIMIT 1
    ) c ON true
    WHERE u.id = ${userId} AND u.email_verified = true AND c.enabled = true
      AND c.email = lower(btrim(u.email))
  `);
  return rows[0] ?? null;
}

export async function unsubscribeProductNews(token: string, revoke: boolean) {
  const id = productNewsConsentId(token);
  const secret = process.env.PRODUCT_NEWS_UNSUBSCRIBE_SECRET ?? "";
  if (!id || secret.length < 32) return null;
  const [original] = await db.select().from(productNewsConsent).where(eq(productNewsConsent.id, id));
  if (!original?.enabled || !verifyProductNewsUnsubscribeToken(token, original.email, secret)) return null;
  return db.transaction(async tx => {
    const owner = await lockUser(tx, original.userId);
    if (!owner || normalizeProductNewsEmail(owner.email) !== original.email) return null;
    const latest = await latestChoice(tx, original.userId);
    const locale = isLocale(original.locale) ? original.locale : defaultLocale;
    // Retries are harmless. An old email cannot undo a later affirmative opt-in.
    if (revoke && latest?.id === original.id) await tx.insert(productNewsConsent).values({
      userId: original.userId, email: original.email, enabled: false, locale, source: "unsubscribe",
      consentVersion: original.consentVersion, consentText: original.consentText,
    });
    return { locale, superseded: Boolean(latest?.enabled && latest.id !== original.id) };
  });
}
