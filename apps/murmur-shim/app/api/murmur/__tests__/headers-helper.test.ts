/**
 * Tests for `_lib/headers-helper.ts`.
 *
 * @see colophon-group/jobseek#2759
 */
import { describe, it, expect } from "vitest";
import { requireMurmurHeaders } from "../_lib/headers-helper";

const make = (h?: Record<string, string>): Request =>
  new Request("https://test.local", { headers: new Headers(h ?? {}) });

describe("requireMurmurHeaders", () => {
  it("flags both headers as missing when neither is set", () => {
    const r = requireMurmurHeaders(make());
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.missing).toEqual(
        expect.arrayContaining([
          "x-murmur-claim-token",
          "x-murmur-subcommand",
        ]),
      );
    }
  });

  it("flags claim-token alone when only it is missing", () => {
    const r = requireMurmurHeaders(make({ "x-murmur-subcommand": "probe monitor" }));
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.missing).toEqual(["x-murmur-claim-token"]);
    }
  });

  it("treats an empty-string claim-token as missing", () => {
    const r = requireMurmurHeaders(
      make({ "x-murmur-claim-token": "   ", "x-murmur-subcommand": "probe monitor" }),
    );
    expect(r.ok).toBe(false);
  });

  it("succeeds when both headers are present and non-empty", () => {
    const r = requireMurmurHeaders(
      make({
        "x-murmur-claim-token": "claim-abc",
        "x-murmur-subcommand": "probe monitor",
      }),
    );
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.claim_token).toBe("claim-abc");
      expect(r.subcommand).toBe("probe monitor");
    }
  });
});
