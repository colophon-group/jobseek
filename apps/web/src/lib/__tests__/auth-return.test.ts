import { describe, expect, it } from "vitest";
import {
  localizeAuthReturnPath,
  normalizeAuthReturnPath,
  withAuthReturnPath,
} from "../auth-return";

describe("auth return paths", () => {
  it("preserves a same-origin path, query, and selected job hash", () => {
    const path = "/en/company/acme?q=safety&show=post-1#details";
    expect(normalizeAuthReturnPath(path)).toBe(path);
    expect(withAuthReturnPath("/en/sign-in", path)).toBe(
      "/en/sign-in?next=%2Fen%2Fcompany%2Facme%3Fq%3Dsafety%26show%3Dpost-1%23details",
    );
  });

  it.each([
    "https://example.com/steal",
    "//example.com/steal",
    "/\\example.com/steal",
    "javascript:alert(1)",
  ])("rejects unsafe redirect target %s", (target) => {
    expect(normalizeAuthReturnPath(target)).toBeNull();
  });

  it("updates only a supported locale prefix", () => {
    expect(localizeAuthReturnPath("/en/company/acme?show=1", "de")).toBe(
      "/de/company/acme?show=1",
    );
    expect(localizeAuthReturnPath("/company/acme", "de")).toBe("/company/acme");
  });
});
