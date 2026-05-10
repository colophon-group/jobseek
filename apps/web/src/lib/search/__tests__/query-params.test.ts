import { describe, expect, it } from "vitest";
import {
  parseWorkModeParam,
  buildFilterQuery,
  buildFilteredPath,
} from "../query-params";

// =====================================================================
// Issue #2983: `wm` URL parameter parsing + serialization.
// =====================================================================

describe("parseWorkModeParam", () => {
  it("returns empty for nullish / empty input", () => {
    expect(parseWorkModeParam(null)).toEqual([]);
    expect(parseWorkModeParam(undefined)).toEqual([]);
    expect(parseWorkModeParam("")).toEqual([]);
  });

  it("parses a single canonical value", () => {
    expect(parseWorkModeParam("remote")).toEqual(["remote"]);
  });

  it("parses comma-separated values, preserving order", () => {
    expect(parseWorkModeParam("hybrid,remote")).toEqual(["hybrid", "remote"]);
  });

  it("lower-cases input before validating", () => {
    expect(parseWorkModeParam("REMOTE,Hybrid")).toEqual(["remote", "hybrid"]);
  });

  it("trims whitespace around tokens", () => {
    expect(parseWorkModeParam(" remote , hybrid ")).toEqual(["remote", "hybrid"]);
  });

  it("drops invalid tokens but keeps valid ones", () => {
    // "wfh" is a synonym handled by the free-text tokenizer in
    // search-input.ts, NOT a canonical URL value. URL must use the
    // canonical token.
    expect(parseWorkModeParam("remote,bogus,onsite,wfh")).toEqual([
      "remote",
      "onsite",
    ]);
  });

  it("deduplicates repeated tokens", () => {
    expect(parseWorkModeParam("remote,remote,Hybrid,REMOTE")).toEqual([
      "remote",
      "hybrid",
    ]);
  });
});

describe("buildFilterQuery — workMode", () => {
  it("emits `wm` when present", () => {
    const qs = buildFilterQuery([], [], undefined, undefined, undefined, [
      "remote",
      "hybrid",
    ]);
    expect(qs).toBe("wm=remote%2Chybrid");
  });

  it("does NOT emit `wm` when empty / undefined", () => {
    expect(buildFilterQuery([], [], undefined, undefined, undefined, [])).toBe(
      "",
    );
    expect(
      buildFilterQuery([], [], undefined, undefined, undefined, undefined),
    ).toBe("");
  });
});

describe("buildFilteredPath — workMode", () => {
  it("appends `?wm=...` to the path", () => {
    expect(
      buildFilteredPath("/en/explore", [], [], undefined, undefined, undefined, undefined, [
        "remote",
      ]),
    ).toBe("/en/explore?wm=remote");
  });

  it("composes with other filters", () => {
    expect(
      buildFilteredPath(
        "/en/explore",
        ["foo"],
        [{ id: 1, slug: "berlin", name: "Berlin", type: "city" }],
        undefined,
        undefined,
        undefined,
        undefined,
        ["remote"],
      ),
    ).toBe("/en/explore?q=foo&loc=berlin&wm=remote");
  });

  it("returns the bare path when workMode is empty", () => {
    expect(
      buildFilteredPath("/en/explore", [], [], undefined, undefined, undefined, undefined, []),
    ).toBe("/en/explore");
  });
});
