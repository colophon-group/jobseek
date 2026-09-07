import { describe, expect, it } from "vitest";
import {
  parseCompanyOgCompletionMarker,
  sameCompanyOgCompletionCoverage,
  type CompanyOgCompletionMarker,
} from "../company-og-completion-marker";

const valid: CompanyOgCompletionMarker = {
  schemaVersion: 2,
  complete: true,
  rendererVersion: "render-v1",
  sourceVersion: "source-v1",
  revision: "a".repeat(40),
  baseRevision: "b".repeat(40),
  companies: 2,
  locales: ["en", "de", "fr", "it"],
  expected: 8,
  completedAt: "2026-09-07T00:00:00.000Z",
};

describe("company OG completion marker", () => {
  it("accepts the exact revision-aware schema", () => {
    expect(parseCompanyOgCompletionMarker(valid, "render-v1"))
      .toEqual(valid);
  });

  it("fails closed on legacy, malformed, and cross-renderer markers", () => {
    const { schemaVersion: _schemaVersion, ...legacy } = valid;
    expect(parseCompanyOgCompletionMarker(legacy, "render-v1")).toBeNull();
    expect(parseCompanyOgCompletionMarker({ ...valid, revision: "main" }))
      .toBeNull();
    expect(parseCompanyOgCompletionMarker({ ...valid, expected: 7 }))
      .toBeNull();
    expect(parseCompanyOgCompletionMarker(valid, "render-v2")).toBeNull();
  });

  it("reuses immutable source coverage when a later revision restores it", () => {
    const laterPointer = {
      ...valid,
      revision: "c".repeat(40),
      baseRevision: "b".repeat(40),
      completedAt: "2026-09-08T00:00:00.000Z",
    };

    expect(sameCompanyOgCompletionCoverage(valid, laterPointer)).toBe(true);
  });
});
