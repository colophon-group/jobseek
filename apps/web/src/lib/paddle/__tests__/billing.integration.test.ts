// @vitest-environment node
import { randomUUID } from "node:crypto";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { sql } from "drizzle-orm";
import type { EventEntity } from "@paddle/paddle-node-sdk";

vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({
  userId: "paddle-fixture-user",
  create: vi.fn(), get: vi.fn(), portal: vi.fn(), update: vi.fn(), getSubscription: vi.fn(), cancel: vi.fn(),
}));
vi.mock("@/lib/sessionCache", () => ({
  getSession: async () => mocks.userId ? { user: { id: mocks.userId, email: "paddle-test@example.com" } } : null,
  getSessionUserId: async () => mocks.userId || null,
}));
vi.mock("@/lib/paddle/client", () => ({
  getPaddle: () => ({ transactions: { create: mocks.create, get: mocks.get, update: mocks.update }, subscriptions: { get: mocks.getSubscription, cancel: mocks.cancel }, customerPortalSessions: { create: mocks.portal } }),
}));
import { db } from "@/db";
import { paddleAccount, paddleSubscription, subscription, user } from "@/db/schema";
import { applyPaddleEvent } from "../webhooks";
import { preparePaddleAccountDeletion } from "../account-deletion";
import { getUserPlan } from "@/lib/plans";
import { hasPaidEntitlement } from "@/lib/paid-entitlement";

const databaseUrl = process.env.PADDLE_TEST_DATABASE_URL;
const trial = `pri_${"a".repeat(26)}`;
const returning = `pri_${"b".repeat(26)}`;
const customer = `ctm_${"c".repeat(26)}`;
const subId = `sub_${"d".repeat(26)}`;
const txnId = `txn_${"e".repeat(26)}`;
let accountId: string;
function event(type = "subscription.created", status = "trialing", offset = 0, overrides = {}): EventEntity {
  return {
    eventId: `evt_${randomUUID()}`, notificationId: null, eventType: type,
    occurredAt: new Date(Date.now() + offset).toISOString(),
    data: {
      id: subId, customerId: customer, transactionId: txnId, status,
      customData: { jobseek_account_id: accountId },
      currentBillingPeriod: { startsAt: new Date().toISOString(), endsAt: new Date(Date.now() + 7 * 86400000).toISOString() },
      scheduledChange: null,
      items: [{ quantity: 1, price: { id: trial }, trialDates: null }],
      ...overrides,
    },
  } as unknown as EventEntity;
}

describe.skipIf(!databaseUrl)("Paddle billing with disposable PostgreSQL", () => {
  beforeAll(async () => {
    const url = new URL(databaseUrl!);
    if (!url.pathname.includes("fixture") || !["localhost", "127.0.0.1"].includes(url.hostname)) {
      throw new Error("Paddle tests require an explicitly named local fixture database");
    }
    vi.stubEnv("DATABASE_URL", databaseUrl!);
    vi.stubEnv("PADDLE_ENVIRONMENT", "sandbox");
    vi.stubEnv("PADDLE_API_KEY", "pdl_sdbx_fixture");
    vi.stubEnv("PADDLE_PRO_PRICE_ID", trial);
    vi.stubEnv("PADDLE_PRO_RETURNING_PRICE_ID", returning);
    vi.stubEnv("NEXT_PUBLIC_PADDLE_ENVIRONMENT", "sandbox");
    vi.stubEnv("NEXT_PUBLIC_PADDLE_CLIENT_TOKEN", "test_fixture");
    vi.stubEnv("PADDLE_WEBHOOK_SECRET", "fixture-secret");
    vi.stubEnv("PADDLE_CHECKOUT_ENABLED", "true");
    await db.insert(user).values({ id: "paddle-fixture-user", name: "Paddle Fixture", email: "paddle-test@example.com" }).onConflictDoNothing();
    await db.insert(user).values({ id: "paddle-fixture-other", name: "Other Fixture", email: "paddle-other@example.com" }).onConflictDoNothing();
  });
  beforeEach(async () => {
    mocks.userId = "paddle-fixture-user";
    vi.clearAllMocks();
    await db.execute(sql`TRUNCATE paddle_account, paddle_subscription, subscription CASCADE`);
    const [account] = await db.insert(paddleAccount).values({ userId: mocks.userId, environment: "sandbox", pendingTransactionId: txnId, pendingPriceId: trial }).returning();
    accountId = account.id;
    mocks.get.mockResolvedValue({ id: txnId, status: "draft" });
    mocks.create.mockResolvedValue({ id: `txn_${"f".repeat(26)}`, status: "draft" });
    mocks.portal.mockResolvedValue({ urls: { general: { overview: "https://sandbox-customer-portal.paddle.com/test" } } });
  });
  afterAll(async () => { vi.unstubAllEnvs(); });

  it("binds the verified transaction, grants trial access across entitlement readers, and stores the customer", async () => {
    await applyPaddleEvent(event());
    expect(await getUserPlan(mocks.userId)).toBe("unlimited");
    expect(await hasPaidEntitlement(db, mocks.userId)).toBe(true);
    expect(await getUserPlan("paddle-fixture-other")).toBe("free");
  });

  it("does not trust copied account metadata on a foreign transaction", async () => {
    await applyPaddleEvent(event("subscription.created", "trialing", 0, { transactionId: "txn_unrelated" }));
    expect(await getUserPlan(mocks.userId)).toBe("free");
    expect(await db.select().from(paddleSubscription)).toHaveLength(0);
  });

  it("retries an update arriving before creation, then converges without reviving stale access", async () => {
    const canceled = event("subscription.canceled", "canceled", 2000);
    await expect(applyPaddleEvent(canceled)).rejects.toThrow("Awaiting");
    const created = event();
    await applyPaddleEvent(created);
    await applyPaddleEvent(canceled);
    await Promise.all([applyPaddleEvent(created), applyPaddleEvent(canceled), applyPaddleEvent(created)]);
    expect(await getUserPlan(mocks.userId)).toBe("free");
    expect(await db.select().from(paddleSubscription)).toHaveLength(1);
  });

  it("compares provider microseconds, not rounded JavaScript milliseconds", async () => {
    const created = event(); created.occurredAt = "2026-09-27T10:00:00.123100Z";
    const canceled = event("subscription.canceled", "canceled"); canceled.occurredAt = "2026-09-27T10:00:00.123900Z";
    await applyPaddleEvent(created); await applyPaddleEvent(canceled); await applyPaddleEvent(created);
    expect((await db.select().from(paddleSubscription))[0].status).toBe("canceled");
  });

  it("keeps scheduled-cancellation access until the period expires", async () => {
    await applyPaddleEvent(event("subscription.created", "active", 0, {
      scheduledChange: { action: "cancel", effectiveAt: new Date(Date.now() + 86400000).toISOString() },
    }));
    expect(await getUserPlan(mocks.userId)).toBe("unlimited");
    expect(await hasPaidEntitlement(db, mocks.userId, new Date(Date.now() + 8 * 86400000))).toBe(false);
  });

  it.each(["past_due", "paused", "canceled"])("revokes %s for historical Paddle subscriptions", async status => {
    await applyPaddleEvent(event());
    await applyPaddleEvent(event("subscription.updated", status, 1000));
    expect(await getUserPlan(mocks.userId)).toBe("free");
  });

  it("rejects a changed price or quantity", async () => {
    await applyPaddleEvent(event("subscription.created", "trialing", 0, { items: [{ quantity: 2, price: { id: trial } }] }));
    expect(await getUserPlan(mocks.userId)).toBe("free");
    await applyPaddleEvent(event("subscription.canceled", "canceled", 1000));
  });

  it("preserves a manually granted subscription when Paddle cancels", async () => {
    await db.insert(subscription).values({ userId: mocks.userId, plan: "unlimited", status: "active", startsAt: new Date() });
    await applyPaddleEvent(event()); await applyPaddleEvent(event("subscription.canceled", "canceled", 1000));
    expect(await getUserPlan(mocks.userId)).toBe("unlimited");
  });

  it("does not let sandbox access leak into the production environment", async () => {
    await applyPaddleEvent(event());
    vi.stubEnv("PADDLE_ENVIRONMENT", "production");
    expect(await getUserPlan(mocks.userId)).toBe("free");
    vi.stubEnv("PADDLE_ENVIRONMENT", "sandbox");
  });



  it("cancels billing before deletion and blocks concurrent new checkout", async () => {
    await applyPaddleEvent(event());
    mocks.getSubscription.mockResolvedValue({ status: "trialing" });
    await preparePaddleAccountDeletion(mocks.userId);
    expect(mocks.cancel).toHaveBeenCalledWith(subId, { effectiveFrom: "immediately" });
    await applyPaddleEvent(event("subscription.canceled", "canceled", 1000));
  });

  it("preserves the account when billing cancellation fails and allows retry", async () => {
    mocks.update.mockRejectedValueOnce(new Error("Paddle unavailable"));
    await expect(preparePaddleAccountDeletion(mocks.userId)).rejects.toThrow();
    expect(await db.select().from(paddleAccount)).toHaveLength(1);
    mocks.update.mockResolvedValue({ status: "canceled" });
    await expect(preparePaddleAccountDeletion(mocks.userId)).resolves.toBeUndefined();
  });

  it("finds and cancels a completed checkout even before its webhook", async () => {
    mocks.get.mockResolvedValue({ id: txnId, status: "completed", subscriptionId: subId });
    mocks.getSubscription.mockResolvedValue({ status: "trialing" });
    await preparePaddleAccountDeletion(mocks.userId);
    expect(mocks.cancel).toHaveBeenCalledWith(subId, { effectiveFrom: "immediately" });
  });

});
