import { describe, expect, it } from "vitest";
import { Client } from "typesense";
import { generateScopedSearchKey } from "../scoped-key";

const PARENT = "test-parent-key-abcdef0123456789";
const EXPIRES_AT = 2_000_000_000;

describe("generateScopedSearchKey", () => {
  it("matches typesense-js Client.keys().generateScopedSearchKey output", () => {
    const client = new Client({
      apiKey: PARENT,
      nodes: [{ host: "x", port: 1, protocol: "http" }],
    });
    const ours = generateScopedSearchKey(PARENT, {
      use_cache: true,
      expires_at: EXPIRES_AT,
    });
    const theirs = client.keys().generateScopedSearchKey(PARENT, {
      use_cache: true,
      expires_at: EXPIRES_AT,
    });
    expect(ours).toBe(theirs);
  });

  it("changes output when embed parameters change", () => {
    const a = generateScopedSearchKey(PARENT, {
      use_cache: true,
      expires_at: EXPIRES_AT,
    });
    const b = generateScopedSearchKey(PARENT, {
      use_cache: false,
      expires_at: EXPIRES_AT,
    });
    expect(a).not.toBe(b);
  });

  it("decodes to a valid Typesense scoped key envelope", () => {
    const key = generateScopedSearchKey(PARENT, {
      filter_by: "is_active:true",
      expires_at: EXPIRES_AT,
    });
    const decoded = Buffer.from(key, "base64").toString("utf-8");
    // Format: <44-char base64 hmac><4-char prefix><params json>
    expect(decoded.length).toBeGreaterThan(48);
    const prefix = decoded.slice(44, 48);
    expect(prefix).toBe(PARENT.slice(0, 4));
    const params = decoded.slice(48);
    expect(JSON.parse(params)).toEqual({
      filter_by: "is_active:true",
      expires_at: EXPIRES_AT,
    });
  });
});
