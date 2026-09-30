// @vitest-environment node
import Stripe from "stripe";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
vi.mock("@/lib/stripe/webhooks", () => ({ applyStripeEvent: vi.fn() }));
import { applyStripeEvent } from "@/lib/stripe/webhooks";
import { POST } from "./route";
const stripe = new Stripe("sk_test_fixture");
const secret = "whsec_fixture";
const payload = JSON.stringify({ id: "evt_fixture", type: "customer.subscription.updated", livemode: false, data: { object: { id: "sub_fixture" } } });
function request(body = payload, signature = stripe.webhooks.generateTestHeaderString({ payload: body, secret })) {
  return new Request("http://localhost/api/stripe/webhook", { method: "POST", body, headers: { "stripe-signature": signature } });
}
describe("Stripe raw-body signature verification", () => {
  beforeEach(() => { vi.clearAllMocks(); vi.stubEnv("STRIPE_ENVIRONMENT", "sandbox"); vi.stubEnv("STRIPE_SECRET_KEY", "sk_test_fixture"); vi.stubEnv("STRIPE_WEBHOOK_SECRET", secret); });
  afterEach(() => vi.unstubAllEnvs());
  it("passes a valid signed event to state reconciliation", async () => {
    vi.mocked(applyStripeEvent).mockResolvedValue();
    expect((await POST(request())).status).toBe(200);
    expect(applyStripeEvent).toHaveBeenCalledWith(expect.objectContaining({ id: "evt_fixture" }));
  });
  it("rejects unsigned, forged, modified and expired requests", async () => {
    expect((await POST(new Request("http://localhost", { method: "POST", body: payload }))).status).toBe(400);
    expect((await POST(request(payload, "t=1,v1=fake"))).status).toBe(400);
    const signature = stripe.webhooks.generateTestHeaderString({ payload, secret });
    expect((await POST(request(payload + " ", signature))).status).toBe(400);
    expect((await POST(request(payload, stripe.webhooks.generateTestHeaderString({ payload, secret, timestamp: 1 })))).status).toBe(400);
    expect(applyStripeEvent).not.toHaveBeenCalled();
  });
  it("returns retryable failures for missing configuration and processing errors", async () => {
    vi.stubEnv("STRIPE_WEBHOOK_SECRET", ""); expect((await POST(request())).status).toBe(503);
    vi.stubEnv("STRIPE_WEBHOOK_SECRET", secret); vi.mocked(applyStripeEvent).mockRejectedValue(new Error("temporary failure"));
    expect((await POST(request())).status).toBe(503);
  });
});
