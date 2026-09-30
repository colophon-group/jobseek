import { describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { createProductNewsUnsubscribeToken, productNewsConsentId, verifyProductNewsUnsubscribeToken } from "../unsubscribe-token";
import { createUnsubscribeToken } from "@/lib/notifications/unsubscribe-token";

const id = "00000000-0000-4000-8000-000000000001";
const secret = "fixture-secret".repeat(4);
describe("product news unsubscribe tokens", () => {
  it("binds the consent event, email and purpose without exposing the address", () => {
    const token = createProductNewsUnsubscribeToken(id, " PERSON@Example.com ", secret);
    expect(productNewsConsentId(token)).toBe(id);
    expect(verifyProductNewsUnsubscribeToken(token, "person@example.com", secret)).toBe(true);
    expect(verifyProductNewsUnsubscribeToken(token, "other@example.com", secret)).toBe(false);
    expect(verifyProductNewsUnsubscribeToken(token, "person@example.com", "different".repeat(5))).toBe(false);
    expect(verifyProductNewsUnsubscribeToken(createUnsubscribeToken(id, "person@example.com", secret), "person@example.com", secret)).toBe(false);
    expect(token).not.toContain("person");
    expect(verifyProductNewsUnsubscribeToken(token.slice(1), "person@example.com", secret)).toBe(false);
    expect(verifyProductNewsUnsubscribeToken(token, "person@example.com", "short")).toBe(false);
    expect(() => createProductNewsUnsubscribeToken(id, "person@example.com", "short")).toThrow();
  });
});
