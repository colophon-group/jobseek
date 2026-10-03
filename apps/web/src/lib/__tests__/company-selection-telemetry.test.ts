import { afterEach, describe, expect, it, vi } from "vitest";
import { observeCompanySelection } from "../company-selection-telemetry";

afterEach(() => vi.restoreAllMocks());

describe("company selection outcome telemetry", () => {
  it.each([
    ["unknown_company", "lookup_miss"], ["company_lookup_unavailable", "lookup_unavailable"],
    ["company_identity_conflict", "identity_conflict"], ["company_limit_reached", "limit"],
    ["not_found", "not_found_or_forbidden"], ["secret customer payload", "rejected"],
  ])("classifies %s without copying private result fields", async (error, outcome) => {
    const log = vi.spyOn(console, "info").mockImplementation(() => {});
    const result = { error, customerEmail: "private@example.com", companyId: "private-id" };
    expect(await observeCompanySelection("add", async () => result)).toBe(result);
    expect(log).toHaveBeenCalledExactlyOnceWith(JSON.stringify({
      event: "company_selection_mutation", operation: "add", outcome,
    }));
  });

  it("preserves a wrapped FK error while logging no SQL, parameters or credentials", async () => {
    const log = vi.spyOn(console, "info").mockImplementation(() => {});
    const error = Object.assign(new Error("insert private@example.com secret-password"), {
      cause: { code: "23503", query: "private SQL", parameters: ["secret"] },
    });
    await expect(observeCompanySelection("update", async () => { throw error; })).rejects.toBe(error);
    expect(log).toHaveBeenCalledExactlyOnceWith(JSON.stringify({
      event: "company_selection_mutation", operation: "update", outcome: "database_foreign_key",
    }));
  });

  it("distinguishes authentication failure and terminates cyclic causes", async () => {
    const log = vi.spyOn(console, "info").mockImplementation(() => {});
    await expect(observeCompanySelection("star", async () => { throw new Error("Not authenticated"); })).rejects.toThrow();
    const cyclic: { cause?: unknown; message: string } = { message: "private" };
    cyclic.cause = cyclic;
    await expect(observeCompanySelection("star", async () => { throw cyclic; })).rejects.toBe(cyclic);
    expect(log.mock.calls.map(([value]) => JSON.parse(value).outcome)).toEqual(["unauthenticated", "unexpected_failure"]);
  });

  it("preserves mutation semantics when an error getter or logging sink throws", async () => {
    vi.spyOn(console, "info").mockImplementation(() => { throw new Error("logging failure"); });
    const error = new Error("original failure");
    Object.defineProperty(error, "code", { get() { throw new Error("getter failure"); } });
    await expect(observeCompanySelection("star", async () => { throw error; })).rejects.toBe(error);
    const success = { starred: true };
    await expect(observeCompanySelection("star", async () => success)).resolves.toBe(success);
  });
});
