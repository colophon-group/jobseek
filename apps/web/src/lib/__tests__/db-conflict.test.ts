import { describe, expect, it } from "vitest";

import { isUniqueViolation } from "../db-conflict";

describe("isUniqueViolation", () => {
  it("matches 23505 with the targeted constraint_name", () => {
    const e = new Error("dup") as Error & {
      code: string;
      constraint_name: string;
    };
    e.code = "23505";
    e.constraint_name = "idx_sj_user_posting";
    expect(isUniqueViolation(e, "idx_sj_user_posting")).toBe(true);
  });

  it("rejects 23505 with a different constraint_name", () => {
    const e = new Error("dup") as Error & {
      code: string;
      constraint_name: string;
    };
    e.code = "23505";
    e.constraint_name = "idx_fc_user_company";
    expect(isUniqueViolation(e, "idx_sj_user_posting")).toBe(false);
  });

  it("rejects non-23505 errors regardless of constraint", () => {
    const e = new Error("connection terminated") as Error & {
      code: string;
    };
    e.code = "ECONNRESET";
    expect(isUniqueViolation(e, "idx_sj_user_posting")).toBe(false);
  });

  it("falls back to message substring when constraint_name is missing", () => {
    const e = new Error(
      'duplicate key value violates unique constraint "idx_fc_user_company"',
    ) as Error & { code: string };
    e.code = "23505";
    expect(isUniqueViolation(e, "idx_fc_user_company")).toBe(true);
    expect(isUniqueViolation(e, "idx_sj_user_posting")).toBe(false);
  });

  it("rejects null / undefined / non-object errors", () => {
    expect(isUniqueViolation(null, "x")).toBe(false);
    expect(isUniqueViolation(undefined, "x")).toBe(false);
    expect(isUniqueViolation("oops", "x")).toBe(false);
    expect(isUniqueViolation(42, "x")).toBe(false);
  });

  it("rejects an empty object that happens to have no code", () => {
    expect(isUniqueViolation({}, "x")).toBe(false);
  });
});
