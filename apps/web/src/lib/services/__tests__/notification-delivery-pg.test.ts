// @vitest-environment node
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import type { Sql } from "postgres";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { NotificationDeliveryPlan } from "@/lib/notifications/scheduler-core";
import type { WebhookEventPayload } from "resend";

const mocks = vi.hoisted(() => ({ client: null as Sql | null, send: vi.fn() }));
vi.mock("server-only", () => ({}));
vi.mock("@/lib/notifications/provider", () => ({ sendNotificationEmail: mocks.send }));
vi.mock("@/lib/services/watchlist-matcher", () => ({
  compileWatchlistMatcherSources: async (sources: unknown[]) => sources,
  matchCompiledWatchlistsInWindow: async () => ({ postings: [], watchlists: [{ total: 0, truncated: false }] }),
}));
vi.mock("@/db", async () => {
  const { default: postgres } = await import("postgres");
  const { drizzle } = await import("drizzle-orm/postgres-js");
  const schema = await import("@/db/schema");
  const url = process.env.NOTIFICATION_TEST_DATABASE_URL;
  if (!url) return { db: {} };
  const parsed = new URL(url);
  if (parsed.hostname !== "127.0.0.1" || !parsed.pathname.endsWith("_fixture")) throw new Error("Disposable localhost fixture database required");
  mocks.client = postgres(url, { max: 10, prepare: false, onnotice: () => {} });
  return { db: drizzle(mocks.client, { schema }) };
});
import { notificationSchedulerRepository, runNotificationScheduler } from "../notification-scheduler";
import { deliverNotificationPlan } from "../notification-delivery";
import { setNotificationsPausedForUser } from "../notification-preferences";
import { unsubscribeNotification } from "../notification-unsubscribe";
import { reconcileNotificationWebhook } from "../notification-webhook";
import { createUnsubscribeToken } from "@/lib/notifications/unsubscribe-token";
const config = { mode: "live" as const, dailyCap: 75, monthlyCap: 2400, internalUserIds: [] };
const secret = "fixture-secret-not-production-32-characters";
const getSql = () => mocks.client!;

async function fixture(): Promise<NotificationDeliveryPlan> {
  const sql = getSql();
  const userId = randomUUID();
  const deliveryId = randomUUID();
  const listId = randomUUID();
  const floor = new Date(Date.now() - 8 * 86400000);
  const now = new Date();
  await sql`INSERT INTO "user" (id, name, email, email_verified, updated_at) VALUES (${userId}, 'Fixture', ${`${userId}@example.com`}, true, ${floor.toISOString()})`;
  await sql`INSERT INTO user_preferences (user_id, locale, updated_at, notifications_state_changed_at) VALUES (${userId}, 'en', ${floor.toISOString()}, ${floor.toISOString()})`;
  await sql`INSERT INTO watchlist (id, user_id, alerts_enabled, alerts_enabled_at, updated_at) VALUES (${listId}, ${userId}, true, ${floor.toISOString()}, ${floor.toISOString()})`;
  await sql`INSERT INTO notification_delivery (id, user_id, cadence, scheduled_for, window_start, window_end, status, match_count, idempotency_key, updated_at)
    VALUES (${deliveryId}, ${userId}, 'weekly', ${now.toISOString()}, ${floor.toISOString()}, ${now.toISOString()}, 'pending', 1, ${`fixture-${deliveryId}`}, ${now.toISOString()})`;
  return { deliveryId, userId, cadence: "weekly", plannedAt: now, scheduledFor: now, windowStart: floor, windowEnd: now,
    idempotencyKey: `fixture-${deliveryId}`, totalMatches: 1, watchlistMatchCount: 1, sourceResultsTruncated: false,
    displayPostings: [{ id: randomUUID(), title: "Engineer", sourceUrl: "https://example.com/jobs/1", firstSeenAt: now.toISOString(), isActive: true,
      company: { id: randomUUID(), name: "Example", slug: "example", icon: null }, matchedWatchlists: [{ id: listId, label: "My jobs" }] }],
  };
}
async function row(plan: NotificationDeliveryPlan) {
  return (await getSql()`SELECT * FROM notification_delivery WHERE id=${plan.deliveryId}`)[0]!;
}

describe.skipIf(!process.env.NOTIFICATION_TEST_DATABASE_URL)("notification delivery with PostgreSQL", () => {
  beforeAll(async () => {
    vi.stubEnv("JOB_ALERTS_UNSUBSCRIBE_SECRET", secret);
    const sql = getSql();
    await sql.unsafe(`DROP SCHEMA public CASCADE; CREATE SCHEMA public;
      DO $$ BEGIN CREATE ROLE anon; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
      DO $$ BEGIN CREATE ROLE authenticated; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
      CREATE TABLE "user" (id text PRIMARY KEY, name text NOT NULL, email text NOT NULL, email_verified boolean, updated_at timestamptz NOT NULL);
      CREATE TABLE user_preferences (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id text UNIQUE NOT NULL REFERENCES "user"(id) ON DELETE CASCADE, locale text NOT NULL DEFAULT 'en', theme text, job_languages text[] DEFAULT '{}', display_currency text, salary_period text, cookie_consent jsonb, dismissed_banners text[], theme_updated_at timestamptz, locale_updated_at timestamptz, last_password_reset_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now());
      CREATE TABLE watchlist (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id text NOT NULL REFERENCES "user"(id) ON DELETE CASCADE, alerts_enabled boolean NOT NULL DEFAULT false, title text DEFAULT 'Fixture', filters jsonb DEFAULT '{"anyCompany":true}', updated_at timestamptz NOT NULL DEFAULT now());`);
    for (const file of ["0088_notification_policy_foundation.sql", "0095_notification_delivery_quota.sql"]) {
      const migration = await readFile(`drizzle/${file}`, "utf8");
      for (const statement of migration.split("--> statement-breakpoint").filter(s => s.trim())) await sql.unsafe(statement);
    }
  });
  beforeEach(async () => {
    await getSql().unsafe('TRUNCATE "user", notification_quota CASCADE');
    mocks.send.mockReset().mockResolvedValue({ status: "sent", messageId: "provider-message" });
  });
  afterAll(async () => { vi.unstubAllEnvs(); await mocks.client?.end(); });

  it("maps the completed-window aggregate to a Date rather than losing the no-backlog floor", async () => {
    const plan = await fixture();
    await deliverNotificationPlan(plan, config);
    const page = await notificationSchedulerRepository.listEligibleUserCandidatesPage({ afterUserId: null, limit: 10 });
    expect(page.candidates[0]!.lastProcessedWindowEnd).toEqual(plan.windowEnd);
    expect(page.candidates[0]!.lastProcessedWindowEnd).toBeInstanceOf(Date);
  });
  it("can advance a now-empty period after a definitive rejected send without losing attempts", async () => {
    const plan = await fixture();
    mocks.send.mockResolvedValueOnce({ status: "failed", errorCode: "provider_http_429" });
    await deliverNotificationPlan(plan, config);
    const result = await runNotificationScheduler({ mode: "shadow",
      sweep: { windowStart: plan.windowStart, windowEnd: new Date(plan.windowEnd.getTime() + 1) },
      quota: { dailyCap: 75, monthlyCap: 2400, dailyUsed: 1, monthlyUsed: 1 }, concurrency: 1,
    });
    expect(result.telemetry.empty).toBe(1);
    expect((await row(plan)).status).toBe("skipped");
    expect((await row(plan)).provider_attempt_count).toBe(1);
  });
  it("sends only once across concurrent duplicate plans", async () => {
    const plan = await fixture();
    const results = await Promise.all([deliverNotificationPlan(plan, config), deliverNotificationPlan(plan, config)]);
    expect(results.sort()).toEqual(["duplicate", "sent"]);
    expect(mocks.send).toHaveBeenCalledTimes(1);
    expect((await row(plan)).provider_attempt_count).toBe(1);
    expect((await getSql()`SELECT used FROM notification_quota`).map(r => r.used)).toEqual([1, 1]);
  });
  it("atomically enforces a shared cap across different users", async () => {
    const plans = await Promise.all([fixture(), fixture(), fixture()]);
    const results = await Promise.all(plans.map(plan => deliverNotificationPlan(plan, { ...config, dailyCap: 1 })));
    expect(results.filter(r => r === "sent")).toHaveLength(1);
    expect(results.filter(r => r === "deferred")).toHaveLength(2);
    expect(mocks.send).toHaveBeenCalledTimes(1);
  });
  it("enforces monthly limits independently and preserves quota on rejection", async () => {
    const a = await fixture(); const b = await fixture();
    mocks.send.mockResolvedValueOnce({ status: "failed", errorCode: "provider_http_429" });
    expect(await deliverNotificationPlan(a, { ...config, monthlyCap: 1 })).toBe("failed");
    expect(await deliverNotificationPlan(b, { ...config, monthlyCap: 1 })).toBe("deferred");
    expect(new Date((await row(b)).deferred_until).getUTCDate()).toBe(1);
  });
  it("holds unknown outcomes for a signed webhook without resending", async () => {
    const plan = await fixture();
    mocks.send.mockResolvedValueOnce({ status: "unknown", errorCode: "timeout" });
    expect(await deliverNotificationPlan(plan, config)).toBe("unknown");
    expect(await deliverNotificationPlan(plan, config)).toBe("duplicate");
    const event = { type: "email.sent", data: { email_id: "reconciled-id", to: [`${plan.userId}@example.com`], tags: { notification_delivery: plan.deliveryId, notification_attempt: "1" } } } as unknown as WebhookEventPayload;
    await reconcileNotificationWebhook(event);
    await reconcileNotificationWebhook(event);
    expect((await row(plan)).status).toBe("sent");
    expect((await row(plan)).provider_message_id).toBe("reconciled-id");
    expect(mocks.send).toHaveBeenCalledTimes(1);
  });
  it("survives a crash after provider acceptance with an unknown barrier", async () => {
    const plan = await fixture();
    mocks.send.mockImplementationOnce(async () => {
      expect((await row(plan)).status).toBe("unknown");
      throw new Error("process failure");
    });
    await expect(deliverNotificationPlan(plan, config)).rejects.toThrow();
    expect((await row(plan)).status).toBe("unknown");
    expect(await deliverNotificationPlan(plan, config)).toBe("duplicate");
  });
  it.each(["paused", "unverified", "disabled", "edited", "deleted"])("rechecks %s before reserving or sending", async state => {
    const plan = await fixture(); const sql = getSql();
    if (state === "paused") await setNotificationsPausedForUser(plan.userId, true);
    if (state === "unverified") await sql`UPDATE "user" SET email_verified=false WHERE id=${plan.userId}`;
    if (state === "disabled") await sql`UPDATE watchlist SET alerts_enabled=false WHERE user_id=${plan.userId}`;
    if (state === "edited") await sql`UPDATE watchlist SET updated_at=${new Date(plan.plannedAt.getTime() + 1000).toISOString()} WHERE user_id=${plan.userId}`;
    if (state === "deleted") await sql`DELETE FROM watchlist WHERE user_id=${plan.userId}`;
    expect(await deliverNotificationPlan(plan, config)).toBe("cancelled");
    expect(mocks.send).not.toHaveBeenCalled();
    expect(await sql`SELECT * FROM notification_quota`).toHaveLength(0);
  });
  it("keeps GET unsubscribe read-only and POST idempotent, preserving choices", async () => {
    const plan = await fixture();
    const token = createUnsubscribeToken(plan.deliveryId, `${plan.userId}@example.com`, secret);
    expect(await unsubscribeNotification(token, false)).toEqual({ locale: "en" });
    expect((await getSql()`SELECT notifications_paused FROM user_preferences`)[0]!.notifications_paused).toBe(false);
    await unsubscribeNotification(token, true);
    const first = (await getSql()`SELECT * FROM user_preferences`)[0]!;
    await unsubscribeNotification(token, true);
    const second = (await getSql()`SELECT * FROM user_preferences`)[0]!;
    expect(second.notifications_paused).toBe(true);
    expect(second.notifications_state_changed_at).toEqual(first.notifications_state_changed_at);
    expect((await getSql()`SELECT alerts_enabled FROM watchlist`)[0]!.alerts_enabled).toBe(true);
    expect(await deliverNotificationPlan(plan, config)).toBe("cancelled");
    await setNotificationsPausedForUser(plan.userId, false);
    expect(new Date((await getSql()`SELECT notifications_state_changed_at FROM user_preferences`)[0]!.notifications_state_changed_at).getTime()).toBeGreaterThanOrEqual(new Date(first.notifications_state_changed_at).getTime());
  });
  it("ignores obsolete unsubscribe links after an email change", async () => {
    const plan = await fixture();
    const token = createUnsubscribeToken(plan.deliveryId, `${plan.userId}@example.com`, secret);
    await getSql()`UPDATE "user" SET email='changed@example.com' WHERE id=${plan.userId}`;
    expect(await unsubscribeNotification(token, true)).toBeNull();
  });
  it("pauses future job email after a signed bounce, without undoing a later resume on replay", async () => {
    const plan = await fixture();
    await deliverNotificationPlan(plan, config);
    const event = { type: "email.bounced", data: { email_id: "provider-message", to: [`${plan.userId}@example.com`], tags: { notification_delivery: plan.deliveryId, notification_attempt: "1" } } } as unknown as WebhookEventPayload;
    await reconcileNotificationWebhook(event);
    expect((await getSql()`SELECT notifications_paused FROM user_preferences`)[0]!.notifications_paused).toBe(true);
    await setNotificationsPausedForUser(plan.userId, false);
    await reconcileNotificationWebhook(event);
    expect((await getSql()`SELECT notifications_paused FROM user_preferences`)[0]!.notifications_paused).toBe(false);
  });
  it("never sends in off/shadow or outside the internal recipient list", async () => {
    const plan = await fixture();
    expect(await deliverNotificationPlan(plan, { ...config, mode: "off" })).toBe("cancelled");
    expect(await deliverNotificationPlan(plan, { ...config, mode: "shadow" })).toBe("cancelled");
    expect(await deliverNotificationPlan(plan, { ...config, mode: "internal", internalUserIds: ["other"] })).toBe("cancelled");
    expect(mocks.send).not.toHaveBeenCalled();
  });
});
