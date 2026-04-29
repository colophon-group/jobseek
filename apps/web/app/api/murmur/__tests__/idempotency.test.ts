/**
 * Unit tests for the canonical-JSON hash + idempotency helpers.
 *
 * @see colophon-group/jobseek#2763
 */

import { describe, it, expect } from "vitest";

import {
  canonicalizeJson,
  sha256Canonical,
} from "../_lib/idempotency";

describe("canonicalizeJson", () => {
  it("returns the same string regardless of object key order", () => {
    const a = canonicalizeJson({ a: 1, b: 2 });
    const b = canonicalizeJson({ b: 2, a: 1 });
    expect(a).toBe(b);
  });

  it("normalises whitespace + key order recursively", () => {
    const a = canonicalizeJson({
      slug: "acme",
      boards: [{ alias: "global", board_url: "x" }],
    });
    const b = canonicalizeJson({
      boards: [{ board_url: "x", alias: "global" }],
      slug: "acme",
    });
    expect(a).toBe(b);
  });

  it("differentiates whitespace inside string values", () => {
    expect(canonicalizeJson({ x: "a" })).not.toBe(
      canonicalizeJson({ x: "a " }),
    );
  });

  it("preserves array order (arrays are NOT sorted)", () => {
    expect(canonicalizeJson([1, 2])).not.toBe(canonicalizeJson([2, 1]));
  });

  it("handles primitives", () => {
    expect(canonicalizeJson(null)).toBe(canonicalizeJson(null));
    expect(canonicalizeJson(true)).toBe("true");
    expect(canonicalizeJson(123)).toBe("123");
    expect(canonicalizeJson("abc")).toBe('"abc"');
  });
});

describe("sha256Canonical", () => {
  it("produces a 64-char lowercase hex string", () => {
    const h = sha256Canonical({ a: 1 });
    expect(h).toMatch(/^[0-9a-f]{64}$/);
  });

  it("is stable for equivalent inputs", () => {
    expect(sha256Canonical({ a: 1, b: 2 })).toBe(
      sha256Canonical({ b: 2, a: 1 }),
    );
  });

  it("differs when content differs", () => {
    expect(sha256Canonical({ a: 1 })).not.toBe(sha256Canonical({ a: 2 }));
  });
});
