// @vitest-environment node
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { eq } from "drizzle-orm";
import postgres from "postgres";

vi.mock("server-only", () => ({}));
vi.mock("@/lib/email", () => ({ sendVerificationEmail: vi.fn(), sendResetPasswordEmail: vi.fn() }));
const mocks = vi.hoisted(() => {
  vi.stubEnv("BETTER_AUTH_SECRET", "product-news-fixture-auth-secret-for-testing-only");
  vi.stubEnv("BETTER_AUTH_URL", "http://localhost:3100");
  return { userId: "news-fixture-owner" };
});
vi.mock("@/lib/sessionCache", () => ({
  getSessionUserId: async () => mocks.userId,
  invalidateSessionCache: vi.fn(), invalidateAllUserSessionCacheEntries: vi.fn(),
}));

import { db } from "@/db";
import { productNewsConsent, user } from "@/db/schema";
import { auth } from "@/lib/auth";
import { updatePreferences } from "@/lib/actions/preferences";
import { getProductNewsPreference, getProductNewsRecipient, setProductNewsPreference, unsubscribeProductNews } from "../product-news";
import { createProductNewsUnsubscribeToken } from "@/lib/product-news/unsubscribe-token";
import { PRODUCT_NEWS_CONSENT_TEXT, PRODUCT_NEWS_CONSENT_VERSION } from "@/lib/product-news/policy";

const databaseUrl = process.env.PRODUCT_NEWS_TEST_DATABASE_URL;
const secret = "product-news-fixture-signing-secret".repeat(2);
let fixture: ReturnType<typeof postgres>;

describe.skipIf(!databaseUrl)("product-news operational contract in disposable PostgreSQL", () => {
  beforeAll(async () => {
    const url = new URL(databaseUrl!);
    if (!url.pathname.includes("product_news_fixture") || !["127.0.0.1", "localhost"].includes(url.hostname)) throw new Error("Use a named local product_news_fixture database");
    fixture = postgres(url.href, { max: 1, onnotice: () => {} });
    await fixture.unsafe(`
      CREATE TABLE IF NOT EXISTS public."user" (id text PRIMARY KEY, name text NOT NULL DEFAULT 'Fixture', email text UNIQUE NOT NULL,
        email_verified boolean NOT NULL DEFAULT false, image text, username text UNIQUE, display_username text,
        created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
      CREATE TABLE IF NOT EXISTS public.account (id text PRIMARY KEY, account_id text NOT NULL, provider_id text NOT NULL, user_id text REFERENCES public."user"(id) ON DELETE CASCADE,
        access_token text, refresh_token text, id_token text, access_token_expires_at timestamptz, refresh_token_expires_at timestamptz,
        scope text, password text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
      CREATE TABLE IF NOT EXISTS public.verification (id text PRIMARY KEY, identifier text NOT NULL, value text NOT NULL,
        expires_at timestamptz NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
      CREATE TABLE IF NOT EXISTS public.user_preferences (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id text UNIQUE REFERENCES public."user"(id) ON DELETE CASCADE,
        theme text DEFAULT 'light', locale text DEFAULT 'en', job_languages text[] DEFAULT '{}', display_currency text DEFAULT 'EUR',
        cookie_consent boolean DEFAULT false, dismissed_banners text[] NOT NULL DEFAULT '{}', theme_updated_at timestamptz DEFAULT now(),
        locale_updated_at timestamptz DEFAULT now(), salary_period text, notification_cadence text DEFAULT 'weekly', last_password_reset_at timestamptz,
        notifications_state_changed_at timestamptz DEFAULT now(), notifications_paused boolean NOT NULL DEFAULT false, updated_at timestamptz DEFAULT now());
      DO $$ BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon; END IF;
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated; END IF;
      END $$;
      ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated;
      ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated;
    `);
    const [existing] = await fixture`SELECT to_regclass('public.product_news_consent') AS name`;
    if (!existing.name) for (const statement of (await readFile(resolve("drizzle/0099_product_news_consent.sql"), "utf8")).split("--> statement-breakpoint").filter(s => s.trim())) await fixture.unsafe(statement);
    vi.stubEnv("DATABASE_URL", databaseUrl!);
    vi.stubEnv("PRODUCT_NEWS_UNSUBSCRIBE_SECRET", secret);
  });

  beforeEach(async () => {
    mocks.userId = "news-fixture-owner";
    await fixture.unsafe('TRUNCATE public."user", public.account, public.verification, public.user_preferences, public.product_news_consent CASCADE');
    await fixture`INSERT INTO public."user" (id, email, email_verified) VALUES ('news-fixture-owner','owner@example.com',true),('news-fixture-other','other@example.com',true)`;
  });

  afterAll(async () => {
    if (fixture) await fixture.end();
    const globalDb = globalThis as unknown as { _db?: { $client: ReturnType<typeof postgres> } };
    if (globalDb._db) await globalDb._db.$client.end();
    delete globalDb._db;
    vi.unstubAllEnvs();
  });

  it("keeps existing users opted out and receipts preserve wording, source and language", async () => {
    expect(await getProductNewsPreference(mocks.userId)).toEqual({ enabled: false });
    expect(await getProductNewsRecipient(mocks.userId)).toBeNull();
    await setProductNewsPreference(mocks.userId, true, "fr", "settings");
    const [receipt] = await db.select().from(productNewsConsent);
    expect(receipt).toMatchObject({ email: "owner@example.com", enabled: true, locale: "fr", source: "settings", consentVersion: PRODUCT_NEWS_CONSENT_VERSION, consentText: PRODUCT_NEWS_CONSENT_TEXT.fr });
    expect(receipt.createdAt).toBeInstanceOf(Date);
    expect(await getProductNewsRecipient(mocks.userId)).toMatchObject({ id: receipt.id, locale: "fr" });
  });

  it("requires email verification, and a changed email needs a new explicit choice", async () => {
    await setProductNewsPreference(mocks.userId, true, "en", "settings");
    await db.update(user).set({ emailVerified: false }).where(eq(user.id, mocks.userId));
    expect(await getProductNewsRecipient(mocks.userId)).toBeNull();
    await db.update(user).set({ emailVerified: true, email: "changed@example.com" }).where(eq(user.id, mocks.userId));
    expect(await getProductNewsPreference(mocks.userId)).toEqual({ enabled: false });
    expect(await getProductNewsRecipient(mocks.userId)).toBeNull();
    await setProductNewsPreference(mocks.userId, true, "de", "settings");
    expect(await getProductNewsRecipient(mocks.userId)).toMatchObject({ email: "changed@example.com", locale: "de" });
  });

  it("serializes duplicate affirmative writes and keeps withdrawal history", async () => {
    await Promise.all(Array.from({ length: 4 }, () => setProductNewsPreference(mocks.userId, true, "en", "settings")));
    expect(await db.select().from(productNewsConsent)).toHaveLength(1);
    await setProductNewsPreference(mocks.userId, false, "en", "settings");
    expect(await db.select().from(productNewsConsent)).toHaveLength(2);
    expect(await getProductNewsPreference(mocks.userId)).toEqual({ enabled: false });
    expect(await getProductNewsRecipient(mocks.userId)).toBeNull();
  });

  it("one-click unsubscribe does not alter job-alert preferences; GET and retries do not add revocations", async () => {
    await fixture`INSERT INTO public.user_preferences (user_id, notifications_paused, dismissed_banners) VALUES (${mocks.userId}, false, ARRAY['cookie-consent'])`;
    await setProductNewsPreference(mocks.userId, true, "it", "settings");
    const recipient = (await getProductNewsRecipient(mocks.userId))!;
    const token = createProductNewsUnsubscribeToken(recipient.id, recipient.email, secret);
    expect(await unsubscribeProductNews(token, false)).toMatchObject({ locale: "it" });
    expect(await getProductNewsPreference(mocks.userId)).toEqual({ enabled: true });
    await unsubscribeProductNews(token, true);
    await unsubscribeProductNews(token, true);
    expect(await db.select().from(productNewsConsent)).toHaveLength(2);
    expect(await getProductNewsPreference(mocks.userId)).toEqual({ enabled: false });
    const [prefs] = await fixture`SELECT notifications_paused, dismissed_banners FROM user_preferences WHERE user_id = ${mocks.userId}`;
    expect(prefs).toEqual({ notifications_paused: false, dismissed_banners: ["cookie-consent"] });
    await setProductNewsPreference(mocks.userId, true, "en", "settings");
    expect(await unsubscribeProductNews(token, true)).toMatchObject({ superseded: true });
    expect(await getProductNewsPreference(mocks.userId)).toEqual({ enabled: true });
  });

  it("rejects tampered, wrong-recipient and changed-email tokens", async () => {
    await setProductNewsPreference(mocks.userId, true, "en", "settings");
    const recipient = (await getProductNewsRecipient(mocks.userId))!;
    const token = createProductNewsUnsubscribeToken(recipient.id, recipient.email, secret);
    expect(await unsubscribeProductNews(createProductNewsUnsubscribeToken(recipient.id, "other@example.com", secret), true)).toBeNull();
    expect(await unsubscribeProductNews(token.slice(1), true)).toBeNull();
    await db.update(user).set({ email: "changed@example.com" }).where(eq(user.id, mocks.userId));
    expect(await unsubscribeProductNews(token, true)).toBeNull();
  });

  it("persists first-use banner dismissal and preserves other banners under a racing upsert", async () => {
    const result = await updatePreferences({ dismissBanner: "pro-launch-v1" });
    expect(result?.dismissedBanners).toEqual(["pro-launch-v1"]);
    await fixture`DELETE FROM user_preferences WHERE user_id = ${mocks.userId}`;
    await Promise.all([updatePreferences({ dismissBanner: "pro-launch-v1" }), updatePreferences({ dismissBanner: "cookie-consent" })]);
    const [prefs] = await fixture`SELECT dismissed_banners FROM user_preferences WHERE user_id = ${mocks.userId}`;
    expect(prefs.dismissed_banners.sort()).toEqual(["cookie-consent", "pro-launch-v1"]);
  });

  it("real Better Auth email signup persists only the checked choice and verification gates sending", async () => {
    for (const choice of [false, true]) {
      const body = { name: "Fixture", email: `signup-${choice}@example.com`, password: "fixture-password-for-tests", productNews: choice, productNewsLocale: "de", productNewsConsentVersion: PRODUCT_NEWS_CONSENT_VERSION };
      const result = await auth.api.signUpEmail({ body });
      const id = result.user.id;
      expect(await getProductNewsPreference(id)).toEqual({ enabled: choice });
      expect(await getProductNewsRecipient(id)).toBeNull();
      if (choice) {
        const [receipt] = await db.select().from(productNewsConsent).where(eq(productNewsConsent.userId, id));
        expect(receipt).toMatchObject({ source: "signup", locale: "de", consentText: PRODUCT_NEWS_CONSENT_TEXT.de });
      }
    }
  });

  it("denies browser table and sequence access and removes consent when the account is deleted", async () => {
    const [policy] = await fixture`SELECT relrowsecurity FROM pg_class WHERE oid = 'public.product_news_consent'::regclass`;
    expect(policy.relrowsecurity).toBe(true);
    for (const role of ["anon", "authenticated"]) {
      for (const privilege of ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"]) {
        const [permission] = await fixture`SELECT has_table_privilege(${role}, 'public.product_news_consent', ${privilege}) AS allowed`;
        expect(permission.allowed).toBe(false);
      }
      const [permission] = await fixture`SELECT has_sequence_privilege(${role}, 'public.product_news_consent_sequence_seq', 'USAGE') AS allowed`;
      expect(permission.allowed).toBe(false);
    }
    await setProductNewsPreference(mocks.userId, true, "en", "settings");
    await db.delete(user).where(eq(user.id, mocks.userId));
    expect(await db.select().from(productNewsConsent)).toHaveLength(0);
  });
});
