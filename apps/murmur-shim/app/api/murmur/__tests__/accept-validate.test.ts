/**
 * Unit tests for the `final_output` schema validator.
 *
 * Covers:
 *   - top-level required fields
 *   - additionalProperties: false at every level
 *   - per-field type / pattern / minLength / maxLength / enum
 *   - array fields: type, items, minItems, maxItems
 *   - deep recursion through `boards[*]` (object inside array)
 *   - error path is a JSON Pointer (`/boards/0/board_url` etc.)
 *
 * @see colophon-group/jobseek#2763
 */

import { describe, it, expect } from "vitest";

import { FINAL_OUTPUT_SCHEMA } from "../_lib/accept-schema";
import { validateAcceptBody } from "../_lib/accept-validate";

const VALID = {
  canonical_name: "Acme",
  canonical_website: "https://acme.example.com",
  slug: "acme",
  description: "Test fixture company.",
  industry_ids: ["software"],
  boards: [
    {
      alias: "global",
      board_url: "https://job-boards.greenhouse.io/acme",
      provider: "greenhouse",
      outcome: "configured",
      monitor_type: "greenhouse",
      monitor_config: { token: "acme" },
      scraper_type: "skip",
      scraper_config: {},
      verdict: "ok",
    },
  ],
};

describe("validateAcceptBody", () => {
  it("returns no errors on a valid body", () => {
    expect(validateAcceptBody(VALID, FINAL_OUTPUT_SCHEMA)).toEqual([]);
  });

  it("flags missing top-level required fields", () => {
    const errs = validateAcceptBody(
      { canonical_name: "Acme" },
      FINAL_OUTPUT_SCHEMA,
    );
    const paths = errs.map((e) => e.path);
    expect(paths).toEqual(
      expect.arrayContaining([
        "/canonical_website",
        "/slug",
        "/description",
        "/industry_ids",
        "/boards",
      ]),
    );
    for (const e of errs) {
      if (e.path !== "" && e.path !== "/canonical_name") {
        expect(e.message).toBe("missing");
      }
    }
  });

  it("rejects unknown top-level keys", () => {
    const errs = validateAcceptBody(
      { ...VALID, sneaky: 1 },
      FINAL_OUTPUT_SCHEMA,
    );
    expect(errs).toEqual(
      expect.arrayContaining([{ path: "/sneaky", message: "unknown_property" }]),
    );
  });

  it("rejects a slug that violates the kebab-case pattern", () => {
    const errs = validateAcceptBody(
      { ...VALID, slug: "Bad Slug!" },
      FINAL_OUTPUT_SCHEMA,
    );
    expect(errs).toEqual(
      expect.arrayContaining([{ path: "/slug", message: "pattern_mismatch" }]),
    );
  });

  it("rejects a description over 400 chars", () => {
    const errs = validateAcceptBody(
      { ...VALID, description: "a".repeat(401) },
      FINAL_OUTPUT_SCHEMA,
    );
    expect(errs).toEqual(
      expect.arrayContaining([
        { path: "/description", message: "too_long" },
      ]),
    );
  });

  it("rejects an empty boards array (minItems: 1)", () => {
    const errs = validateAcceptBody(
      { ...VALID, boards: [] },
      FINAL_OUTPUT_SCHEMA,
    );
    expect(errs).toEqual(
      expect.arrayContaining([{ path: "/boards", message: "too_short" }]),
    );
  });

  it("rejects industry_ids over 4", () => {
    const errs = validateAcceptBody(
      { ...VALID, industry_ids: ["a", "b", "c", "d", "e"] },
      FINAL_OUTPUT_SCHEMA,
    );
    expect(errs).toEqual(
      expect.arrayContaining([
        { path: "/industry_ids", message: "too_long" },
      ]),
    );
  });

  it("rejects a board with a missing required field", () => {
    const broken = {
      ...VALID,
      boards: [{ ...VALID.boards[0], board_url: undefined }],
    };
    const errs = validateAcceptBody(broken, FINAL_OUTPUT_SCHEMA);
    const paths = errs.map((e) => e.path);
    expect(paths).toContain("/boards/0/board_url");
  });

  it("rejects a board verdict not in the enum", () => {
    const broken = {
      ...VALID,
      boards: [{ ...VALID.boards[0], verdict: "maybe" }],
    };
    const errs = validateAcceptBody(broken, FINAL_OUTPUT_SCHEMA);
    expect(errs).toEqual(
      expect.arrayContaining([
        { path: "/boards/0/verdict", message: "not_in_enum" },
      ]),
    );
  });

  it("rejects a board_url that is not https://", () => {
    const broken = {
      ...VALID,
      boards: [{ ...VALID.boards[0], board_url: "http://insecure.example.com/x" }],
    };
    const errs = validateAcceptBody(broken, FINAL_OUTPUT_SCHEMA);
    const paths = errs.map((e) => `${e.path}:${e.message}`);
    expect(paths).toContain("/boards/0/board_url:pattern_mismatch");
  });

  it("rejects a non-object root", () => {
    expect(validateAcceptBody("not an object", FINAL_OUTPUT_SCHEMA)).toEqual([
      { path: "", message: "must_be_object" },
    ]);
  });
});
