/**
 * Tests for `/api/stripe/webhook`.
 *
 * Regression context: #3209 — the route's signature-verification block was
 * commented out, leaving a "stub" path that parsed JSON directly and wrote
 * to the `subscription` table. A one-line `curl POST` of a forged
 * `checkout.session.completed` event could upgrade any user to
 * `plan: "unlimited"` or cancel any subscription.
 *
 * These tests assert the fail-closed behaviour when the env var is absent
 * AND the fail-closed behaviour when a signature is missing or invalid.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// We never touch the DB in these tests — the request must be rejected
// before any handler logic runs. We still need to mock `@/db` because the
// route imports it at module top.
vi.mock("@/db", () => ({
  db: {
    select: vi.fn(() => {
      throw new Error("db.select called — webhook should have rejected before reaching DB");
    }),
    insert: vi.fn(() => {
      throw new Error("db.insert called — webhook should have rejected before reaching DB");
    }),
    update: vi.fn(() => {
      throw new Error("db.update called — webhook should have rejected before reaching DB");
    }),
  },
}));

// Mock the schema barrel so importing the route doesn't pull in
// drizzle-zod / postgres-js at test time.
vi.mock("@/db/schema", () => ({
  subscription: {
    userId: "userId",
    stripeSubscriptionId: "stripeSubscriptionId",
    id: "id",
  },
}));

import { POST } from "./route";

const URL_BASE = "http://localhost/api/stripe/webhook";

function makeRequest(
  body: unknown,
  init: { signature?: string | null } = {},
): Request {
  const headers: Record<string, string> = {
    "content-type": "application/json",
  };
  if (init.signature !== null && init.signature !== undefined) {
    headers["stripe-signature"] = init.signature;
  }
  return new Request(URL_BASE, {
    method: "POST",
    body: typeof body === "string" ? body : JSON.stringify(body),
    headers,
  });
}

const FORGED_CHECKOUT_EVENT = {
  id: "evt_forged_001",
  type: "checkout.session.completed",
  data: {
    object: {
      customer: "cus_attacker",
      subscription: "sub_attacker",
      metadata: { userId: "victim-user-id" },
    },
  },
};

let warnSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
  delete process.env.STRIPE_WEBHOOK_SECRET;
});

afterEach(() => {
  warnSpy.mockRestore();
  delete process.env.STRIPE_WEBHOOK_SECRET;
  delete process.env.STRIPE_SECRET_KEY;
});

describe("POST /api/stripe/webhook — fail-closed when STRIPE_WEBHOOK_SECRET unset", () => {
  it("returns 503 when STRIPE_WEBHOOK_SECRET is not set (regression: #3209)", async () => {
    // This is THE security regression test. On main (before this fix) the
    // route's signature-verification block was commented out and the
    // handler would parse JSON directly, advancing to `activateSubscription`.
    // After the fix, the request must be rejected with 503 before any DB
    // write is attempted.
    const res = await POST(
      makeRequest(FORGED_CHECKOUT_EVENT, { signature: "t=1,v1=anything" }),
    );
    expect(res.status).toBe(503);
    // Cache-Control: no-store guards against an intermediary caching the
    // 503 and serving it to a legitimate Stripe retry once the env var is
    // set.
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    // Log line for operability — the warning helps detect misconfiguration.
    expect(warnSpy).toHaveBeenCalled();
    const warnMsg = String(warnSpy.mock.calls[0]?.[0] ?? "");
    expect(warnMsg).toContain("STRIPE_WEBHOOK_SECRET");
  });

  it("returns 503 when STRIPE_WEBHOOK_SECRET is the empty string", async () => {
    process.env.STRIPE_WEBHOOK_SECRET = "";
    const res = await POST(
      makeRequest(FORGED_CHECKOUT_EVENT, { signature: "t=1,v1=anything" }),
    );
    expect(res.status).toBe(503);
  });

  it("503 path returns even when no signature header is present", async () => {
    // The env gate runs BEFORE the signature header check, so a forged
    // request without any signature still gets 503 (not 400) when the env
    // var is unset.
    const res = await POST(makeRequest(FORGED_CHECKOUT_EVENT, { signature: null }));
    expect(res.status).toBe(503);
  });
});

describe("POST /api/stripe/webhook — signature verification when STRIPE_WEBHOOK_SECRET set", () => {
  beforeEach(() => {
    process.env.STRIPE_WEBHOOK_SECRET = "whsec_test_only_not_a_real_secret";
    process.env.STRIPE_SECRET_KEY = "sk_test_unused";
  });

  it("returns 400 when stripe-signature header is missing", async () => {
    const res = await POST(makeRequest(FORGED_CHECKOUT_EVENT, { signature: null }));
    expect(res.status).toBe(400);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe("Missing stripe-signature header");
  });

  it("returns 400 when stripe-signature header is malformed / does not verify", async () => {
    // A signature header that doesn't match the body+secret HMAC must be
    // rejected with 400. We use a value that's syntactically plausible to
    // Stripe's parser but cryptographically invalid.
    const res = await POST(
      makeRequest(FORGED_CHECKOUT_EVENT, {
        signature: "t=1,v1=0000000000000000000000000000000000000000000000000000000000000000",
      }),
    );
    expect(res.status).toBe(400);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    const body = (await res.json()) as { error: string };
    expect(body.error).toBe("Invalid signature");
  });

  it("returns 400 when stripe-signature header is total garbage", async () => {
    const res = await POST(
      makeRequest(FORGED_CHECKOUT_EVENT, { signature: "garbage" }),
    );
    expect(res.status).toBe(400);
  });
});
