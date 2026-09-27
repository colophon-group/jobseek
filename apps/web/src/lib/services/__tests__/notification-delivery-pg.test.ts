// @vitest-environment node
import { readFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import type { Sql } from "postgres";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import type { NotificationDeliveryPlan } from "@/lib/notifications/scheduler-core";
import type { WebhookEventPayload } from "resend";

const mocks = vi.hoisted(() => ({ client: null as Sql | null, send: vi.fn(), match: vi.fn() }));
vi.mock("server-only", () => ({}));
vi.mock("@/lib/notifications/provider", () => ({ sendNotificationEmail: mocks.send }));
vi.mock("@/lib/services/watchlist-matcher", () => ({
  compileWatchlistMatcherSources: async (sources: { watchlistId: string; watchlistLabel: string }[]) => sources.map(s => ({ ...s, candidateFilters: { anyCompany: true, companyIds: [] } })),
  matchCompiledWatchlistsInWindow: mocks.match,
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
import { notificationSchedulerRepository, runNotificationScheduler, matchNotificationWatchlists } from "../notification-scheduler";
import { deliverNotificationPlan } from "../notification-delivery";
import { setNotificationsPausedForUser } from "../notification-preferences";
import { unsubscribeNotification } from "../notification-unsubscribe";
import { reconcileNotificationWebhook } from "../notification-webhook";
import { createUnsubscribeToken } from "@/lib/notifications/unsubscribe-token";
import { getNotificationWatchlistsForUser, setWatchlistNotificationModeForUser } from "../notification-watchlist-settings";
import { matchNarrowedNotificationWatchlist } from "../notification-narrowing";
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

beforeAll(async () => {
    if (!process.env.NOTIFICATION_TEST_DATABASE_URL) return;
    vi.stubEnv("JOB_ALERTS_UNSUBSCRIBE_SECRET", secret);
    const sql = getSql();
    await sql.unsafe(`DROP SCHEMA public CASCADE; CREATE SCHEMA public;
      DO $$ BEGIN CREATE ROLE anon; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
      DO $$ BEGIN CREATE ROLE authenticated; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
      CREATE TABLE "user" (id text PRIMARY KEY, name text NOT NULL, email text NOT NULL, email_verified boolean, updated_at timestamptz NOT NULL);
      CREATE TABLE user_preferences (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id text UNIQUE NOT NULL REFERENCES "user"(id) ON DELETE CASCADE, locale text NOT NULL DEFAULT 'en', theme text, job_languages text[] DEFAULT '{}', display_currency text, salary_period text, cookie_consent jsonb, dismissed_banners text[], theme_updated_at timestamptz, locale_updated_at timestamptz, last_password_reset_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now());
      CREATE TABLE watchlist (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id text NOT NULL REFERENCES "user"(id) ON DELETE CASCADE, alerts_enabled boolean NOT NULL DEFAULT false, title text DEFAULT 'Fixture', filters jsonb DEFAULT '{"anyCompany":true}', updated_at timestamptz NOT NULL DEFAULT now());
      CREATE TABLE subscription (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id text REFERENCES "user"(id) ON DELETE CASCADE, plan text, status text, ends_at timestamptz);
      CREATE TABLE ai_filter_configuration (id uuid PRIMARY KEY, owner_id text REFERENCES "user"(id) ON DELETE CASCADE, watchlist_id uuid REFERENCES watchlist(id) ON DELETE CASCADE, status text, current_revision integer, updated_at timestamptz, last_caught_up_at timestamptz);
      CREATE TABLE ai_filter_query_version (id uuid PRIMARY KEY, configuration_id uuid REFERENCES ai_filter_configuration(id) ON DELETE CASCADE, revision integer, query_text text);
      CREATE TABLE ai_filter_decision (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), owner_id text REFERENCES "user"(id) ON DELETE CASCADE, watchlist_id uuid REFERENCES watchlist(id) ON DELETE CASCADE, query_version_id uuid REFERENCES ai_filter_query_version(id) ON DELETE CASCADE, candidate_id uuid, model_decision text, user_override text, expires_at timestamptz, posting_first_seen_at timestamptz, decided_at timestamptz DEFAULT now());`);
    for (const file of ["0088_notification_policy_foundation.sql", "0095_notification_delivery_quota.sql"]) {
      const migration = await readFile(`drizzle/${file}`, "utf8");
      for (const statement of migration.split("--> statement-breakpoint").filter(s => s.trim())) await sql.unsafe(statement);
    }
  });

describe.skipIf(!process.env.NOTIFICATION_TEST_DATABASE_URL)("notification delivery with PostgreSQL", () => {
  beforeEach(async () => {
    await getSql().unsafe('TRUNCATE "user", notification_quota CASCADE');
    mocks.match.mockReset().mockResolvedValue({ postings: [], watchlists: [{ total: 0, truncated: false }] });
    mocks.send.mockReset().mockResolvedValue({ status: "sent", messageId: "provider-message" });
  });


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

async function narrowedFixture() {
  const plan = await fixture();
  const sql = getSql();
  const label = plan.displayPostings[0]!.matchedWatchlists[0]!;
  const configurationId = randomUUID();
  const queryId = randomUUID();
  const prompt = "Senior backend roles with distributed systems";
  const before = new Date(plan.plannedAt.getTime() - 1000).toISOString();
  await sql`INSERT INTO subscription (user_id, plan, status) VALUES (${plan.userId}, 'unlimited', 'active')`;
  await sql`UPDATE watchlist SET alerts_narrowed_only=true WHERE id=${label.id}`;
  await sql`INSERT INTO ai_filter_configuration (id, owner_id, watchlist_id, status, current_revision, updated_at, last_caught_up_at)
    VALUES (${configurationId}, ${plan.userId}, ${label.id}, 'enabled', 1, ${before}, ${plan.windowEnd.toISOString()})`;
  await sql`INSERT INTO ai_filter_query_version (id, configuration_id, revision, query_text) VALUES (${queryId}, ${configurationId}, 1, ${prompt})`;
  await sql`INSERT INTO ai_filter_decision (owner_id, watchlist_id, query_version_id, candidate_id, model_decision, expires_at, posting_first_seen_at)
    VALUES (${plan.userId}, ${label.id}, ${queryId}, ${plan.displayPostings[0]!.id}, 'accepted', ${new Date(Date.now()+86400000).toISOString()}, ${before})`;
  label.narrowedQueryVersionId = queryId;
  const input = { ownerId: plan.userId, compiled: { watchlistId: label.id, watchlistLabel: label.label, candidateFilters: { anyCompany: true, companyIds: [] } },
    windowStart: new Date(Math.floor(plan.windowStart.getTime()/1000)*1000), windowEnd: plan.windowEnd };
  return { plan, configurationId, queryId, prompt, input };
}

describe.skipIf(!process.env.NOTIFICATION_TEST_DATABASE_URL)("narrowed notification settings and delivery", () => {

  beforeEach(async () => {
    await getSql().unsafe('TRUNCATE "user", notification_quota CASCADE');
    mocks.send.mockReset().mockResolvedValue({ status: "sent", messageId: "provider-message" });
    mocks.match.mockReset().mockResolvedValue({ postings: [], watchlists: [{ total: 0, truncated: false }] });
  });
  it("persists each list's scope, exposes its prompt and enforces owner and pause boundaries", async () => {
    const { plan, prompt, input } = await narrowedFixture();
    expect((await getNotificationWatchlistsForUser(plan.userId))[0]).toMatchObject({ mode: "narrowed", prompt, narrowingAvailable: true });
    expect(await setWatchlistNotificationModeForUser("other-owner", input.compiled.watchlistId, "all")).toEqual({ error: "not_found" });
    expect(await setWatchlistNotificationModeForUser(plan.userId, input.compiled.watchlistId, "all")).toEqual({ mode: "all" });
    const [updated] = await getSql()`SELECT alerts_enabled_at FROM watchlist WHERE id=${input.compiled.watchlistId}`;
    expect(new Date(updated!.alerts_enabled_at).getTime()).toBeGreaterThan(plan.windowStart.getTime());
    await setNotificationsPausedForUser(plan.userId, true);
    expect(await setWatchlistNotificationModeForUser(plan.userId, input.compiled.watchlistId, "narrowed")).toEqual({ error: "notifications_paused" });
  });
  it("rejects narrowed opt-in without an enabled entitled prompt", async () => {
    const { plan, input } = await narrowedFixture();
    await getSql()`UPDATE subscription SET status='expired' WHERE user_id=${plan.userId}`;
    expect(await setWatchlistNotificationModeForUser(plan.userId, input.compiled.watchlistId, "narrowed")).toEqual({ error: "narrowing_unavailable" });
    expect((await matchNarrowedNotificationWatchlist(input)).postings).toEqual([]);
    expect(mocks.match).not.toHaveBeenCalled();
  });
  it("holds incomplete evaluation for retry without closing or broadening the period", async () => {
    const { input, configurationId } = await narrowedFixture();
    await getSql()`UPDATE ai_filter_configuration SET last_caught_up_at=NULL WHERE id=${configurationId}`;
    await expect(matchNarrowedNotificationWatchlist(input)).rejects.toThrow(/still being evaluated/);
    expect(mocks.match).not.toHaveBeenCalled();
  });
  it("intersects accepted decisions before search and uses the latest rejection instead of an older acceptance", async () => {
    const { plan, input, queryId } = await narrowedFixture();
    await matchNarrowedNotificationWatchlist(input);
    expect(mocks.match.mock.calls[0]![0].watchlists[0].candidateFilters.postingIds).toEqual([plan.displayPostings[0]!.id]);
    mocks.match.mockClear();
    await getSql()`INSERT INTO ai_filter_decision (owner_id, watchlist_id, query_version_id, candidate_id, model_decision, expires_at, posting_first_seen_at, decided_at)
      VALUES (${plan.userId}, ${input.compiled.watchlistId}, ${queryId}, ${plan.displayPostings[0]!.id}, 'rejected', ${new Date(Date.now()+86400000).toISOString()}, ${plan.windowStart.toISOString()}, ${new Date(Date.now()+1000).toISOString()})`;
    expect((await matchNarrowedNotificationWatchlist(input)).postings).toEqual([]);
    expect(mocks.match).not.toHaveBeenCalled();
  });
  it("sends accepted narrowed results and cancels stale prompts or newly rejected jobs", async () => {
    const accepted = await narrowedFixture();
    expect(await deliverNotificationPlan(accepted.plan, config)).toBe("sent");
    const changed = await narrowedFixture();
    await getSql()`UPDATE ai_filter_configuration SET current_revision=2 WHERE id=${changed.configurationId}`;
    expect(await deliverNotificationPlan(changed.plan, config)).toBe("cancelled");
    const rejected = await narrowedFixture();
    await getSql()`UPDATE ai_filter_decision SET user_override='rejected' WHERE query_version_id=${rejected.queryId}`;
    expect(await deliverNotificationPlan(rejected.plan, config)).toBe("cancelled");
    expect(mocks.send).toHaveBeenCalledTimes(1);
  });
  it("combines broad and narrowed watchlists, deduplicating jobs with both contributing labels", async () => {
    const { plan, input } = await narrowedFixture();
    const broadId = randomUUID();
    mocks.match.mockImplementation(async ({ watchlists }) => ({
      postings: [{ ...plan.displayPostings[0], matchedWatchlists: watchlists.map((w: { watchlistId: string; watchlistLabel: string }) => ({ id: w.watchlistId, label: w.watchlistLabel })) }],
      watchlists: watchlists.map((w: { watchlistId: string }) => ({ id: w.watchlistId, total: 1, truncated: false })),
    }));
    const source = { watchlistId: input.compiled.watchlistId, watchlistLabel: "Narrowed", filters: { anyCompany: true }, companyIds: [], locale: "en", jobLanguages: [] };
    const result = await matchNotificationWatchlists({ windowEnd: input.windowEnd, watchlists: [
      { source, ownerId: plan.userId, narrowedOnly: true, alertsEnabledAt: input.windowStart, windowStart: input.windowStart },
      { source: { ...source, watchlistId: broadId, watchlistLabel: "Broad" }, alertsEnabledAt: input.windowStart, windowStart: input.windowStart },
    ] });
    expect(result.postings).toHaveLength(1);
    expect(result.uniqueMatchCount).toBe(1);
    expect(result.postings[0]!.matchedWatchlists.map(w => w.id).sort()).toEqual([source.watchlistId, broadId].sort());
  });
});

afterAll(async () => { vi.unstubAllEnvs(); await mocks.client?.end(); });
