import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { matchesBasicAuthorization } from "../basic-auth";

describe("matchesBasicAuthorization", () => {
  it("accepts the expected Basic token", () => {
    expect(
      matchesBasicAuthorization("Basic abc123", "abc123"),
    ).toBe(true);
  });

  it("rejects missing or malformed values", () => {
    expect(matchesBasicAuthorization(null, "abc123")).toBe(false);
    expect(matchesBasicAuthorization("Bearer abc123", "abc123")).toBe(false);
    expect(matchesBasicAuthorization("Basic wrong", "abc123")).toBe(false);
    expect(matchesBasicAuthorization("Basic abc123", undefined)).toBe(false);
  });

  it("rejects a same-length wrong token", () => {
    // Exercises the constant-time path: lengths match so the length
    // pre-check passes and `timingSafeEqual` is the one returning false.
    // A pre-#3225 `===` compare would have already returned false on
    // the first differing byte; this test still passes against either
    // impl but documents the constant-time intent.
    expect(matchesBasicAuthorization("Basic abc124", "abc123")).toBe(false);
  });

  it("rejects an empty token after the Basic scheme", () => {
    expect(matchesBasicAuthorization("Basic ", "abc123")).toBe(false);
    expect(matchesBasicAuthorization("Basic", "abc123")).toBe(false);
  });
});
