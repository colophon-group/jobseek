import { describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

import { assertBoundedPublicPagination } from "../public-read-input";

describe("assertBoundedPublicPagination", () => {
  it.each([
    { offset: 0, limit: 10 },
    { offset: 5_000, limit: 100 },
    {},
  ])("accepts bounded pagination %#", (params) => {
    expect(() => assertBoundedPublicPagination(params)).not.toThrow();
  });

  it.each([
    { offset: -1, limit: 10 },
    { offset: 5_001, limit: 10 },
    { offset: 1.5, limit: 10 },
    { offset: 0, limit: 0 },
    { offset: 0, limit: 101 },
    { offset: 0, limit: Number.POSITIVE_INFINITY },
  ])("rejects pagination amplification %#", (params) => {
    expect(() => assertBoundedPublicPagination(params)).toThrow(
      /Invalid public read/,
    );
  });

  it("supports a tighter action-specific ceiling", () => {
    expect(() =>
      assertBoundedPublicPagination(
        { offset: 101, limit: 21 },
        { maxOffset: 100, maxLimit: 20 },
      ),
    ).toThrow(/Invalid public read/);
  });
});
