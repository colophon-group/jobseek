// @vitest-environment node
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { eq, sql } from "drizzle-orm";
import postgres from "postgres";
import type Stripe from "stripe";

vi.mock("server-only", () => ({}));
vi.mock("../checkout-policy", () => ({ checkoutPolicyMessage: async () => "I agree to the [Terms](https://jseek.co/en/terms)." }));
const mocks = vi.hoisted(() => ({ userId: "stripe-fixture-user", sessions: [] as Record<string, unknown>[], subscriptions: [] as Record<string, unknown>[], retrieve: vi.fn(), create: vi.fn(), cancel: vi.fn(), expire: vi.fn(), portal: vi.fn(), price: vi.fn() }));
vi.mock("@/lib/sessionCache", () => ({ getSession: async () => mocks.userId ? { user: { id: mocks.userId, email: "stripe-fixture@example.com" } } : null, getSessionUserId: async () => mocks.userId || null }));
function listing(data: Record<string, unknown>[]) {
  return Object.assign(Promise.resolve({ data, has_more: false }), { async *[Symbol.asyncIterator]() { yield* data; } });
}
vi.mock("../client", () => ({ getStripe: () => ({
  prices: { retrieve: mocks.price }, customers: { create: async () => ({ id: "cus_fixture" }) },
  checkout: { sessions: { list: () => listing(mocks.sessions), retrieve: async (id: string) => mocks.sessions.find(s => s.id === id), create: mocks.create, expire: mocks.expire } },
  subscriptions: { retrieve: mocks.retrieve, list: () => listing(mocks.subscriptions), cancel: mocks.cancel },
  billingPortal: { sessions: { create: mocks.portal } },
}) }));
import { db } from "@/db";
import { stripeAccount, stripeSubscription, subscription, paddleAccount } from "@/db/schema";
import { applyStripeEvent } from "../webhooks";
import { checkout } from "../checkout";
import { prepareStripeAccountDeletion } from "../account-deletion";
import { createCheckoutSession, createPortalSession, getPlanInfo } from "@/lib/actions/billing";
import { getUserPlan } from "@/lib/plans";

const databaseUrl = process.env.STRIPE_TEST_DATABASE_URL;
const price = { id: "price_fixture", active: true, livemode: false, currency: "usd", unit_amount: 1000, type: "recurring", billing_scheme: "per_unit", tax_behavior: "exclusive", recurring: { interval: "month", interval_count: 1 } };
let accountId: string;
function current(status = "trialing", overrides = {}) {
  return { object: "subscription", id: "sub_fixture", customer: "cus_fixture", livemode: false, status, cancel_at: null, cancel_at_period_end: false, pause_collection: null, items: { has_more: false, data: [{ quantity: 1, current_period_end: Math.floor(Date.now() / 1000) + 7 * 86400, price }] }, ...overrides };
}
function event(type = "customer.subscription.created", overrides = {}) {
  return { id: "evt_fixture", type, livemode: false, created: 1, data: { object: current() }, ...overrides } as unknown as Stripe.Event;
}

describe.skipIf(!databaseUrl)("Stripe billing operational contract in disposable PostgreSQL", () => {
  beforeAll(async () => {
    const url = new URL(databaseUrl!);
    if (!url.pathname.includes("fixture") || !["127.0.0.1", "localhost"].includes(url.hostname)) throw new Error("Use a named local fixture database");
    const fixture = postgres(url.href, { max: 1 });
    await fixture.unsafe('CREATE TABLE IF NOT EXISTS "user" (id text PRIMARY KEY)');
    await fixture.unsafe(`CREATE TABLE IF NOT EXISTS subscription (id uuid PRIMARY KEY DEFAULT gen_random_uuid(),user_id text UNIQUE REFERENCES "user"(id),plan text,status text,stripe_customer_id text,stripe_subscription_id text,starts_at timestamp,ends_at timestamp,created_at timestamp DEFAULT now(),updated_at timestamp DEFAULT now())`);
    for (const tag of ["0096_paddle_billing", "0098_stripe_billing"]) {
      const [exists] = await fixture`SELECT to_regclass(${tag.includes("paddle") ? "paddle_account" : "stripe_account"}) AS table_name`;
      if (!exists.table_name) for (const statement of (await readFile(resolve("drizzle", `${tag}.sql`), "utf8")).split("--> statement-breakpoint").filter(s => s.trim())) await fixture.unsafe(statement);
    }
    await fixture`INSERT INTO "user" VALUES ('stripe-fixture-user'),('stripe-fixture-other') ON CONFLICT DO NOTHING`;
    await fixture.end();
    for (const [key, value] of Object.entries({ DATABASE_URL: databaseUrl!, STRIPE_ENVIRONMENT: "sandbox", STRIPE_SECRET_KEY: "sk_test_fixture", STRIPE_PRO_PRICE_ID: price.id, STRIPE_WEBHOOK_SECRET: "whsec_fixture", STRIPE_CHECKOUT_ENABLED: "true", STRIPE_PORTAL_CONFIGURATION_ID: "bpc_fixture", BETTER_AUTH_URL: "http://localhost:3100", PADDLE_ENVIRONMENT: "" })) vi.stubEnv(key, value);
  });
  beforeEach(async () => {
    vi.clearAllMocks(); mocks.userId = "stripe-fixture-user";
    await db.execute(sql`TRUNCATE stripe_account, stripe_subscription, paddle_account, subscription CASCADE`);
    const [account] = await db.insert(stripeAccount).values({ userId: mocks.userId, environment: "sandbox", customerId: "cus_fixture", pendingCheckoutId: "cs_fixture", pendingPriceId: price.id, checkoutParameters: { mode: "subscription" } }).returning();
    accountId = account.id;
    mocks.sessions = [{ id: "cs_fixture", customer: "cus_fixture", subscription: "sub_fixture", mode: "subscription", client_reference_id: account.id, status: "complete", livemode: false }];
    mocks.subscriptions = [];
    mocks.retrieve.mockResolvedValue(current()); mocks.price.mockResolvedValue(price);
    mocks.create.mockImplementation(async (params) => {
      const session = { id: "cs_new", ...params, status: "open", livemode: false, url: "https://checkout.stripe.com/c/pay/cs_new" };
      mocks.sessions.push(session); return session;
    });
    mocks.portal.mockResolvedValue({ url: "https://billing.stripe.com/p/session/fixture" });
  });
  afterAll(() => vi.unstubAllEnvs());

  it("binds only a completed server Checkout and grants trial access with a portal", async () => {
    await applyStripeEvent(event());
    expect(await getUserPlan(mocks.userId)).toBe("unlimited");
    expect(await getUserPlan("stripe-fixture-other")).toBe("free");
    expect(await getPlanInfo()).toMatchObject({ trialEligible: false, status: "trialing", hasBillingAccount: true });
    expect((await createPortalSession("de")).url).toBeTruthy();
    expect(mocks.portal).toHaveBeenCalledWith(expect.objectContaining({ customer: "cus_fixture", return_url: "http://localhost:3100/de/settings/billing", configuration: "bpc_fixture" }));
  });
  it("ignores forged metadata and mismatched Checkout subscription", async () => {
    mocks.sessions[0].subscription = "sub_foreign";
    await applyStripeEvent(event());
    expect(await getUserPlan(mocks.userId)).toBe("free");
    expect(await db.select().from(stripeSubscription)).toHaveLength(0);
  });
  it("retries subscription delivery before checkout completes", async () => {
    mocks.sessions[0].status = "open";
    await expect(applyStripeEvent(event())).rejects.toThrow("Awaiting");
    mocks.sessions[0].status = "complete";
    await applyStripeEvent(event());
    expect(await getUserPlan(mocks.userId)).toBe("unlimited");
  });
  it("duplicates and older same-second snapshots converge without reviving canceled access", async () => {
    await applyStripeEvent(event());
    mocks.retrieve.mockResolvedValue(current("canceled"));
    await applyStripeEvent(event("customer.subscription.deleted"));
    await applyStripeEvent(event()); await applyStripeEvent(event());
    expect(await getUserPlan(mocks.userId)).toBe("free");
    expect(await db.select().from(stripeSubscription)).toHaveLength(1);
  });
  it.each(["past_due", "unpaid", "incomplete", "incomplete_expired", "paused", "canceled"])("revokes %s and retains portal access", async status => {
    mocks.retrieve.mockResolvedValue(current(status));
    await applyStripeEvent(event());
    expect(await getUserPlan(mocks.userId)).toBe("free");
    expect((await createPortalSession()).url).toBeTruthy();
  });
  it("keeps scheduled cancellation until expiry and reads flexible cancel_at", async () => {
    mocks.retrieve.mockResolvedValue(current("active", { cancel_at: Math.floor(Date.now() / 1000) + 100 }));
    await applyStripeEvent(event());
    expect(await getUserPlan(mocks.userId)).toBe("unlimited");
    expect((await getPlanInfo()).cancellationScheduled).toBe(true);
    await db.update(stripeSubscription).set({ currentPeriodEnd: new Date(0) });
    expect(await getUserPlan(mocks.userId)).toBe("free");
  });
  it("rejects wrong quantities/prices, collection pauses and mixed modes", async () => {
    for (const overrides of [{ items: { data: [{ quantity: 2, price }], has_more: false } }, { items: { data: [{ quantity: 1, price: { ...price, id: "price_foreign" } }], has_more: false } }, { pause_collection: { behavior: "void" } }, { livemode: true }]) {
      mocks.retrieve.mockResolvedValue(current("active", overrides));
      if (overrides.livemode) await expect(applyStripeEvent(event())).rejects.toThrow("environment");
      else { await applyStripeEvent(event()); expect(await getUserPlan(mocks.userId)).toBe("free"); }
    }
    await expect(applyStripeEvent(event(undefined, { livemode: true }))).rejects.toThrow("environment");
  });
  it("preserves independent manual grants when Stripe cancels", async () => {
    await db.insert(subscription).values({ userId: mocks.userId, plan: "unlimited", status: "active", startsAt: new Date() });
    mocks.retrieve.mockResolvedValue(current("canceled")); await applyStripeEvent(event());
    expect(await getUserPlan(mocks.userId)).toBe("unlimited");
  });
  async function clearCheckout() {
    await db.update(stripeAccount).set({ pendingCheckoutId: null, pendingPriceId: null, checkoutParameters: null }).where(eq(stripeAccount.id, accountId)); mocks.sessions = [];
  }
  it("serializes double clicks, fixes the price/trial, and preserves localized internal destinations", async () => {
    await clearCheckout();
    const results = await Promise.all([createCheckoutSession("fr", "/fr/explore?q=python"), createCheckoutSession("fr", "/fr/explore?q=python")]);
    expect(results[0].url).toBe(results[1].url); expect(mocks.create).toHaveBeenCalledTimes(1);
    const params = mocks.create.mock.calls[0][0];
    expect(params).toMatchObject({ mode: "subscription", payment_method_collection: "always", line_items: [{ price: price.id, quantity: 1 }], subscription_data: { trial_period_days: 7, billing_mode: { type: "flexible" } }, automatic_tax: { enabled: true } });
    expect(new URL(params.success_url).searchParams.get("next")).toBe("/fr/explore?q=python");
    expect(new URL(params.success_url).pathname).toBe("/fr/settings/billing");
  });
  it("recovers a created session after a lost API response without a second purchase", async () => {
    await clearCheckout();
    const create = mocks.create.getMockImplementation()!;
    mocks.create.mockImplementationOnce(async (...args) => { await create(...args); throw new Error("lost response"); });
    expect((await createCheckoutSession()).error).toBe("payments_unavailable");
    expect((await createCheckoutSession()).url).toBeTruthy();
    expect(mocks.create).toHaveBeenCalledTimes(1);
  });
  it("blocks completed checkout pending its webhook and blocks an unindexed live subscription", async () => {
    expect((await createCheckoutSession()).error).toBe("billing_processing");
    await clearCheckout(); mocks.subscriptions = [current("past_due")];
    expect((await createCheckoutSession()).error).toBe("billing_already_subscribed");
    expect(mocks.create).not.toHaveBeenCalled();
  });
  it("never repeats a trial for returning Stripe or Paddle subscribers", async () => {
    await clearCheckout(); await db.insert(paddleAccount).values({ userId: mocks.userId, environment: "sandbox", trialUsedAt: new Date() });
    await createCheckoutSession();
    expect(mocks.create.mock.calls[0][0].subscription_data.trial_period_days).toBeUndefined();
    expect((await getPlanInfo()).trialEligible).toBe(false);
  });
  it("validates the configured price rather than trusting client inputs", async () => {
    await clearCheckout(); mocks.price.mockResolvedValue({ ...price, unit_amount: 100 });
    expect((await createCheckoutSession()).error).toBe("payments_unavailable");
    expect(mocks.create).not.toHaveBeenCalled();
  });
  it("rejects external return URLs and requires authentication", async () => {
    await clearCheckout(); await checkout({ id: mocks.userId, email: "fixture@example.com" }, "invalid", "https://evil.test");
    expect(mocks.create.mock.calls[0][0].success_url).toBe("http://localhost:3100/en/settings/billing?checkout=complete");
    mocks.userId = "";
    expect((await createCheckoutSession()).error).toBe("not_authenticated");
    expect((await createPortalSession()).error).toBe("not_authenticated");
  });
  it("replaces expired checkout safely without consuming a trial", async () => {
    mocks.sessions[0].status = "expired";
    const result = await createCheckoutSession();
    expect(result.url).toBeTruthy();
    expect(mocks.create).toHaveBeenCalledTimes(1);
    expect(mocks.create.mock.calls[0][0].subscription_data.trial_period_days).toBe(7);
  });
  it("does not grant sandbox access when configured for production", async () => {
    await applyStripeEvent(event());
    vi.stubEnv("STRIPE_ENVIRONMENT", "production");
    expect(await getUserPlan(mocks.userId)).toBe("free");
    vi.stubEnv("STRIPE_ENVIRONMENT", "sandbox");
  });
  it("fails deletion closed on provider failure, disables checkout, and permits retry", async () => {
    mocks.cancel.mockRejectedValueOnce(new Error("provider unavailable"));
    await expect(prepareStripeAccountDeletion(mocks.userId)).rejects.toThrow();
    expect((await createCheckoutSession()).error).toBe("payments_unavailable");
    expect(await db.select().from(stripeAccount)).toHaveLength(1);
    await prepareStripeAccountDeletion(mocks.userId);
    expect(mocks.cancel).toHaveBeenCalledWith("sub_fixture", { prorate: false, invoice_now: false });
    await applyStripeEvent(event()); expect(await getUserPlan(mocks.userId)).toBe("free");
  });
  it("expires an orphaned open checkout and cancels unindexed subscriptions before deletion", async () => {
    mocks.sessions[0].status = "open"; mocks.subscriptions = [current("active")];
    await prepareStripeAccountDeletion(mocks.userId);
    expect(mocks.expire).toHaveBeenCalledWith("cs_fixture"); expect(mocks.cancel).toHaveBeenCalledTimes(1);
  });
});
