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
});
