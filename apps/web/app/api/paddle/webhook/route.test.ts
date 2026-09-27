// @vitest-environment node
import { createHmac } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
const mocks = vi.hoisted(() => ({ apply: vi.fn() }));
vi.mock("@/lib/paddle/webhooks", () => ({ applyPaddleEvent: mocks.apply }));
import { POST } from "./route";
const secret = "test-webhook-secret";
const body = JSON.stringify({ event_id: "evt_fixture", event_type: "product.created", occurred_at: "2026-09-27T12:00:00Z", notification_id: "ntf_fixture", data: { id: "pro_fixture", name: "Pro", description: null, type: "standard", tax_category: "saas", image_url: null, custom_data: null, status: "active", import_meta: null, created_at: "2026-09-27T12:00:00Z", updated_at: "2026-09-27T12:00:00Z" } });
function request(payload = body, timestamp = Math.floor(Date.now() / 1000), signedBody = payload) {
  const signature = createHmac("sha256", secret).update(`${timestamp}:${signedBody}`).digest("hex");
  return new Request("http://localhost/api/paddle/webhook", { method: "POST", body: payload, headers: { "paddle-signature": `ts=${timestamp};h1=${signature}` } });
}
describe("Paddle webhook trust boundary", () => {
  beforeEach(() => { vi.stubEnv("PADDLE_ENVIRONMENT", "sandbox"); vi.stubEnv("PADDLE_API_KEY", "pdl_sdbx_fixture"); vi.stubEnv("PADDLE_WEBHOOK_SECRET", secret); mocks.apply.mockReset().mockResolvedValue(undefined); });
  afterEach(() => vi.unstubAllEnvs());
  it("fails closed when no secret is configured", async () => {
    vi.stubEnv("PADDLE_WEBHOOK_SECRET", "");
    expect((await POST(request())).status).toBe(503); expect(mocks.apply).not.toHaveBeenCalled();
  });
  it("rejects missing, altered, or expired signatures before any database work", async () => {
    expect((await POST(new Request("http://localhost/api/paddle/webhook", { method: "POST", body }))).status).toBe(400);
    expect((await POST(request(body + " ", undefined, body))).status).toBe(400);
    expect((await POST(request(body, Math.floor(Date.now() / 1000) - 60))).status).toBe(400);
    expect(mocks.apply).not.toHaveBeenCalled();
  });
  it("accepts a real SDK-verified signature and waits for processing", async () => {
    expect((await POST(request())).status).toBe(200);
    expect(mocks.apply).toHaveBeenCalledWith(expect.objectContaining({ eventId: "evt_fixture", eventType: "product.created" }));
  });
  it("returns a retryable error if durable processing fails", async () => {
    mocks.apply.mockRejectedValue(new Error("database unavailable"));
    expect((await POST(request())).status).toBe(503);
  });
});
