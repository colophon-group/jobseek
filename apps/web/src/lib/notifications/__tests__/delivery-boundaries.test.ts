import { afterEach, describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { getJobAlertsConfig } from "../config";
import { createUnsubscribeToken, verifyUnsubscribeToken } from "../unsubscribe-token";
import { renderNotificationEmail } from "../render-email";
import { sendNotificationEmail } from "../provider";
import type { NotificationDeliveryPlan } from "../scheduler-core";

const secret = "a".repeat(32);
const id = "11111111-1111-4111-8111-111111111111";
const plan: NotificationDeliveryPlan = {
  deliveryId: id, userId: "owner", cadence: "weekly", plannedAt: new Date(), scheduledFor: new Date(),
  windowStart: new Date(), windowEnd: new Date(), idempotencyKey: "notification-key", totalMatches: 21,
  watchlistMatchCount: 21, sourceResultsTruncated: true,
  displayPostings: Array.from({ length: 21 }, (_, i) => ({
    id: String(i), title: '<script>alert("job")</script>', sourceUrl: "javascript:alert(1)", firstSeenAt: new Date().toISOString(), isActive: true,
    company: { id: "company", name: "A & B", slug: "a", icon: null }, locationNames: ["Zurich"],
    matchedWatchlists: [{ id: "list", label: '<img src=x onerror="x">' }],
  })),
};
afterEach(() => vi.unstubAllGlobals());
describe("notification delivery boundaries", () => {
  it("defaults off and rejects invalid caps, incomplete live config and preview sends", () => {
    expect(getJobAlertsConfig({}).mode).toBe("off");
    expect(() => getJobAlertsConfig({ JOB_ALERTS_MODE: "typo" })).toThrow();
    expect(() => getJobAlertsConfig({ JOB_ALERTS_DAILY_CAP: "76" })).toThrow();
    expect(() => getJobAlertsConfig({ JOB_ALERTS_MONTHLY_CAP: "NaN" })).toThrow();
    expect(() => getJobAlertsConfig({ JOB_ALERTS_MODE: "internal" })).toThrow();
    expect(() => getJobAlertsConfig({ JOB_ALERTS_MODE: "live" })).toThrow();
    expect(() => getJobAlertsConfig({ JOB_ALERTS_MODE: "live", RESEND_API_KEY: "test", JOB_ALERTS_UNSUBSCRIBE_SECRET: secret, RESEND_WEBHOOK_SECRET: "test", VERCEL_ENV: "preview" })).toThrow();
  });
  it("binds unsubscribe to the exact delivery and current email without exposing the email", () => {
    const token = createUnsubscribeToken(id, "a@example.com", secret);
    expect(token).not.toContain("example");
    expect(verifyUnsubscribeToken(token, "A@example.com", secret)).toBe(true);
    expect(verifyUnsubscribeToken(token, "b@example.com", secret)).toBe(false);
    expect(verifyUnsubscribeToken(token.replace("1111", "2222"), "a@example.com", secret)).toBe(false);
    expect(verifyUnsubscribeToken(token, "a@example.com", "wrong".repeat(8))).toBe(false);
    expect(verifyUnsubscribeToken("invalid", "a@example.com", secret)).toBe(false);
  });
  it.each(["en", "de", "fr", "it"])("escapes dynamic data, limits items and includes management links in %s", locale => {
    const email = renderNotificationEmail({ plan, locale, origin: "https://jseek.co", unsubscribeUrl: "https://jseek.co/unsubscribe?token=test" });
    expect(email.html).not.toContain("<script>");
    expect(email.html).not.toContain("<img");
    expect(email.html).not.toContain("javascript:");
    expect(email.html.match(/<h2/g)).toHaveLength(20);
    expect(email.html).toContain(`/${locale}/settings#notifications`);
    expect(email.html).toContain(`/${locale}/watchlists/list`);
    expect(email.text).toContain("unsubscribe?token=test");
  });
  it.each([400, 401, 403, 422, 429, 409, 500, 503])("classifies provider HTTP %i conservatively without retry", async status => {
    const fetch = vi.fn().mockResolvedValue(new Response("failure", { status }));
    vi.stubGlobal("fetch", fetch);
    const result = await sendNotificationEmail({ to: "a@example.com", deliveryId: id, attempt: 1, idempotencyKey: "same-period", unsubscribeUrl: "https://jseek.co/unsubscribe", email: { subject: "Jobs", html: "<p>Job</p>", text: "Job" } });
    expect(result.status).toBe([409, 500, 503].includes(status) ? "unknown" : "failed");
    expect(fetch).toHaveBeenCalledTimes(1);
    const options = fetch.mock.calls[0]![1];
    expect(options.headers["Idempotency-Key"]).toBe("same-period");
    expect(JSON.parse(options.body).headers["List-Unsubscribe-Post"]).toBe("List-Unsubscribe=One-Click");
  });
  it("holds transport failures and malformed success responses as unknown", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("timeout")));
    const input = { to: "a@example.com", deliveryId: id, attempt: 1, idempotencyKey: "key", unsubscribeUrl: "https://jseek.co/unsubscribe", email: { subject: "Jobs", html: "Jobs", text: "Jobs" } };
    expect((await sendNotificationEmail(input)).status).toBe("unknown");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    expect((await sendNotificationEmail(input)).status).toBe("unknown");
  });
});
