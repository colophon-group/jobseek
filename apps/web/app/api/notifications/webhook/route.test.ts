// @vitest-environment node
import { createHmac } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ reconcile: vi.fn() }));
vi.mock("@/lib/services/notification-webhook", () => ({ reconcileNotificationWebhook: mocks.reconcile }));
import { POST } from "./route";
const key = Buffer.from("fixture-webhook-key");
beforeEach(() => { mocks.reconcile.mockReset(); vi.stubEnv("RESEND_API_KEY", "test-key"); vi.stubEnv("RESEND_WEBHOOK_SECRET", `whsec_${key.toString("base64")}`); });
afterEach(() => vi.unstubAllEnvs());
describe("Resend webhook verification", () => {
  it("rejects unsigned events before touching the database", async () => {
    expect((await POST(new Request("https://jseek.co/api/notifications/webhook", { method: "POST", body: "{}" }))).status).toBe(400);
    expect(mocks.reconcile).not.toHaveBeenCalled();
  });
  it("verifies the raw body using the SDK before reconciliation", async () => {
    const payload = JSON.stringify({ type: "email.sent", data: { email_id: "provider-id" } });
    const timestamp = String(Math.floor(Date.now() / 1000));
    const signature = createHmac("sha256", key).update(`fixture-id.${timestamp}.${payload}`).digest("base64");
    const request = () => new Request("https://jseek.co/api/notifications/webhook", { method: "POST", body: payload, headers: {
      "svix-id": "fixture-id", "svix-timestamp": timestamp, "svix-signature": `v1,${signature}`,
    } });
    expect((await POST(request())).status).toBe(204);
    expect(mocks.reconcile).toHaveBeenCalledWith(JSON.parse(payload));
    mocks.reconcile.mockRejectedValue(new Error("database down"));
    expect((await POST(request())).status).toBe(503);
  });
});
