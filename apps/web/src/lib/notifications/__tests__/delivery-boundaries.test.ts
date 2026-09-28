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
  it.each(["en", "de", "fr", "it"])("links each job to its matching watchlist on the sending domain in %s", locale => {
    const item = { ...plan.displayPostings[0]!, id: "posting-1",
      sourceUrl: "https://careers.example.org/jobs/123",
      matchedWatchlists: [{ id: "list-1", label: "Product" }, { id: "list-2", label: "Remote" }] };
    const email = renderNotificationEmail({ plan: { ...plan, displayPostings: [item] }, locale, origin: "https://jseek.co", unsubscribeUrl: "https://jseek.co/api/notifications/unsubscribe?token=test" });
    const document = new DOMParser().parseFromString(email.html, "text/html");
    const firstUrl = `https://jseek.co/${locale}/watchlists/list-1?show=posting-1`;
    const secondUrl = `https://jseek.co/${locale}/watchlists/list-2?show=posting-1`;
    expect(document.querySelector("h2 a")?.getAttribute("href")).toBe(firstUrl);
    expect(Array.from(document.querySelectorAll("a")).find(a => a.textContent === "Remote")?.getAttribute("href")).toBe(secondUrl);
    for (const anchor of document.querySelectorAll("a")) {
      expect(new URL(anchor.getAttribute("href")!).origin).toBe("https://jseek.co");
    }
    expect(email.text).toContain(firstUrl);
    expect(email.text).toContain(secondUrl);
    expect(email.html).not.toContain(item.sourceUrl);
    expect(email.text).not.toContain(item.sourceUrl);
  });
  it("encodes watchlist and posting IDs without letting them change the URL", () => {
    const item = { ...plan.displayPostings[0]!, id: 'job&other="value"#fragment',
      matchedWatchlists: [{ id: "list/?query", label: "List" }] };
    const email = renderNotificationEmail({ plan: { ...plan, displayPostings: [item] }, locale: "en", origin: "https://jseek.co", unsubscribeUrl: "https://jseek.co/unsubscribe" });
    const document = new DOMParser().parseFromString(email.html, "text/html");
    const url = new URL(document.querySelector("h2 a")!.getAttribute("href")!);
    expect(url.pathname).toBe("/en/watchlists/list%2F%3Fquery");
    expect(url.searchParams.get("show")).toBe(item.id);
    expect([...url.searchParams.keys()]).toEqual(["show"]);
    expect(url.hash).toBe("");
    expect(email.text).toContain(url.href);
  });
  it("falls back to the watchlist overview when a posting has no matching watchlist", () => {
    const item = { ...plan.displayPostings[0]!, sourceUrl: "https://careers.example.org/job", matchedWatchlists: [] };
    const email = renderNotificationEmail({ plan: { ...plan, displayPostings: [item] }, locale: "unsupported", origin: "https://jseek.co", unsubscribeUrl: "https://jseek.co/unsubscribe" });
    const document = new DOMParser().parseFromString(email.html, "text/html");
    expect(document.querySelector("h2 a")?.getAttribute("href")).toBe("https://jseek.co/en/watchlists");
    expect(email.text).toContain("https://jseek.co/en/watchlists");
    expect(email.html).not.toContain(item.sourceUrl);
    expect(email.text).not.toContain(item.sourceUrl);
  });
  it.each([
    ["en", "Added 2 days ago"], ["de", "Hinzugefügt: vor 2 Tagen"],
    ["fr", "Ajoutée il y a 2 jours"], ["it", "Aggiunta 2 giorni fa"],
  ])("renders company icons and localized first-seen age in %s", (locale, age) => {
    const item = { ...plan.displayPostings[0]!, firstSeenAt: "2026-09-25T10:00:00Z",
      company: { ...plan.displayPostings[0]!.company, icon: 'https://assets.example/icon.png?a=1&b="test"' } };
    const email = renderNotificationEmail({ plan: { ...plan, displayPostings: [item] }, now: new Date("2026-09-27T10:00:00Z"), locale, origin: "https://jseek.co", unsubscribeUrl: "https://jseek.co/unsubscribe" });
    expect(email.html).toContain('<img src="https://assets.example/icon.png?a=1&amp;b=%22test%22"');
    expect(email.html).toContain('width="32" height="32"');
    expect(email.html).toContain("A &amp; B");
    expect(email.html).toContain(age);
    expect(email.text).toContain(age);
    expect(email.text).not.toContain("https://assets.example/icon.png");
  });
  it.each([null, "javascript:alert(1)", "data:image/svg+xml,<svg/>", "https://user:pass@example.com/icon.png", "not a url"])("uses initials for an unavailable or unsafe company icon (%s)", icon => {
    const item = { ...plan.displayPostings[0]!, firstSeenAt: "invalid", company: { ...plan.displayPostings[0]!.company, name: '<A & B>', icon } };
    const email = renderNotificationEmail({ plan: { ...plan, displayPostings: [item] }, locale: "en", origin: "https://jseek.co", unsubscribeUrl: "https://jseek.co/unsubscribe" });
    expect(email.html).not.toContain("<img");
    expect(email.html).toContain("&lt;A</span>");
    expect(email.text).not.toContain("Added");
    expect(email.html).not.toContain("NaN");
  });
  it.each([
    ["2026-09-27T08:00:00Z", "Added 2 hours ago"],
    ["2026-09-27T09:59:00Z", "Added 1 minute ago"],
    ["2026-09-27T10:01:00Z", "Added now"],
  ])("measures age at render time and clamps clock skew (%s)", (firstSeenAt, age) => {
    const email = renderNotificationEmail({ plan: { ...plan, displayPostings: [{ ...plan.displayPostings[0]!, firstSeenAt }] }, now: new Date("2026-09-27T10:00:00Z"), locale: "en", origin: "https://jseek.co", unsubscribeUrl: "https://jseek.co/unsubscribe" });
    expect(email.html).toContain(age);
    expect(email.text).toContain(age);
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
  it("sends from jseek.co with a monitored reply address and preserves unsubscribe headers", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"id":"message-1"}', { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    const result = await sendNotificationEmail({ to: "a@example.com", deliveryId: id, attempt: 1, idempotencyKey: "same-period", unsubscribeUrl: "https://jseek.co/unsubscribe", email: { subject: "Jobs", html: "<p>Job</p>", text: "Job" } });
    expect(result).toEqual({ status: "sent", messageId: "message-1" });
    const payload = JSON.parse(fetch.mock.calls[0]![1].body);
    expect(payload.from).toBe("Job Seek <hello@jseek.co>");
    expect(payload.reply_to).toBe("business@colophon-group.org");
    expect(payload.headers).toEqual({
      "List-Unsubscribe": "<https://jseek.co/unsubscribe>",
      "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    });
  });
  it("holds transport failures and malformed success responses as unknown", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("timeout")));
    const input = { to: "a@example.com", deliveryId: id, attempt: 1, idempotencyKey: "key", unsubscribeUrl: "https://jseek.co/unsubscribe", email: { subject: "Jobs", html: "Jobs", text: "Jobs" } };
    expect((await sendNotificationEmail(input)).status).toBe("unknown");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", { status: 200 })));
    expect((await sendNotificationEmail(input)).status).toBe("unknown");
  });
});
