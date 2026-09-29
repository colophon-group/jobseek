import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  insert: vi.fn(), values: vi.fn(), onConflictDoNothing: vi.fn(), limit: vi.fn(), log: vi.fn(),
}));
vi.mock("next/headers", () => ({ headers: async () => new Headers({ "x-real-ip": "192.0.2.1" }) }));
vi.mock("@/db", () => ({ db: { insert: mocks.insert } }));
vi.mock("@/lib/i18n", () => ({ defaultLocale: "en", isLocale: (s: string) => ["en", "de", "fr", "it"].includes(s) }));
vi.mock("@/lib/rate-limit", () => ({ proWaitlistLimiter: { limit: mocks.limit }, getClientIp: (h: Headers) => h.get("x-real-ip") }));
vi.mock("@/lib/safe-external-error", () => ({ logExternalError: mocks.log }));

import { proWaitlist } from "@/db/schema";
import { joinProWaitlist } from "../pro-waitlist";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.insert.mockReturnValue({ values: mocks.values });
  mocks.values.mockReturnValue({ onConflictDoNothing: mocks.onConflictDoNothing });
  mocks.onConflictDoNothing.mockResolvedValue(undefined);
  mocks.limit.mockResolvedValue({ success: true });
});

describe("joinProWaitlist", () => {
  it("saves anonymous signups with a normalized email and chosen locale", async () => {
    expect(await joinProWaitlist("  Reader+Pro@Example.com  ", "de")).toEqual({ success: true });
    expect(mocks.limit).toHaveBeenCalledWith("192.0.2.1");
    expect(mocks.insert).toHaveBeenCalledWith(proWaitlist);
    expect(mocks.values).toHaveBeenCalledWith({ email: "reader+pro@example.com", locale: "de" });
    expect(mocks.onConflictDoNothing).toHaveBeenCalledWith({ target: proWaitlist.email });
  });

  it.each(["", "bad", "a@@example.com", "a b@example.com", "x@example", `${"a".repeat(65)}@example.com`, `a@${"b".repeat(250)}.com`, null, {}, 123])("rejects invalid input before accessing services: %j", async (email) => {
    expect(await joinProWaitlist(email as string, "en")).toEqual({ error: "invalid_email" });
    expect(mocks.limit).not.toHaveBeenCalled();
    expect(mocks.insert).not.toHaveBeenCalled();
  });

  it("uses English for an unsupported or malformed locale", async () => {
    await joinProWaitlist("reader@example.com", { invalid: true } as unknown as string);
    expect(mocks.values).toHaveBeenCalledWith({ email: "reader@example.com", locale: "en" });
  });

  it("returns the same confirmation for duplicate signups", async () => {
    expect(await joinProWaitlist("reader@example.com", "en")).toEqual({ success: true });
    expect(await joinProWaitlist("READER@example.com", "fr")).toEqual({ success: true });
    expect(mocks.onConflictDoNothing).toHaveBeenCalledTimes(2);
  });

  it("does not write when the rate limit is exceeded", async () => {
    mocks.limit.mockResolvedValue({ success: false });
    expect(await joinProWaitlist("reader@example.com", "en")).toEqual({ error: "rate_limited" });
    expect(mocks.insert).not.toHaveBeenCalled();
  });

  it("does not write when the limiter times out", async () => {
    mocks.limit.mockResolvedValue({ success: true, reason: "timeout" });
    expect(await joinProWaitlist("reader@example.com", "en")).toEqual({ error: "unavailable" });
    expect(mocks.insert).not.toHaveBeenCalled();
  });

  it("handles limiter outages without writing", async () => {
    mocks.limit.mockRejectedValueOnce(new Error("Private service error"));
    expect(await joinProWaitlist("reader@example.com", "en")).toEqual({ error: "unavailable" });
    expect(mocks.insert).not.toHaveBeenCalled();
  });

  it("never confirms a signup that failed to save", async () => {
    mocks.onConflictDoNothing.mockRejectedValueOnce(new Error("Private database error"));
    expect(await joinProWaitlist("reader@example.com", "en")).toEqual({ error: "unavailable" });
    expect(mocks.log).toHaveBeenCalled();
  });
});
