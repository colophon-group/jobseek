/**
 * Tests for `_lib/validate.ts` — the JSON-Schema-subset validator.
 *
 * Covers the constructs used by the YAML schemas:
 *   - missing required keys
 *   - additionalProperties: false rejects unknown keys
 *   - per-property type / minLength / pattern / format=uri / enum / minimum
 *   - per-field error paths use JSON-Pointer shape
 *
 * @see colophon-group/jobseek#2759
 */

import { describe, it, expect } from "vitest";
import { validateBody } from "../_lib/validate";
import {
  PROBE_MONITOR_SCHEMA,
  FEEDBACK_SCHEMA,
  SELECT_MONITOR_SCHEMA,
  RUN_SCRAPER_SCHEMA,
} from "../_lib/schemas";

describe("validateBody — generic", () => {
  it("returns must_be_object on a non-object body", () => {
    const errs = validateBody(42, PROBE_MONITOR_SCHEMA);
    expect(errs).toEqual([{ path: "", message: "must_be_object" }]);
  });
  it("returns must_be_object on null", () => {
    const errs = validateBody(null, PROBE_MONITOR_SCHEMA);
    expect(errs).toEqual([{ path: "", message: "must_be_object" }]);
  });
  it("returns must_be_object on an array", () => {
    const errs = validateBody([], PROBE_MONITOR_SCHEMA);
    expect(errs).toEqual([{ path: "", message: "must_be_object" }]);
  });

  it("flags missing required keys with /<key>:missing", () => {
    const errs = validateBody({}, PROBE_MONITOR_SCHEMA);
    expect(errs).toContainEqual({ path: "/board_url", message: "missing" });
  });

  it("flags unknown properties with unknown_property", () => {
    const errs = validateBody(
      { board_url: "https://job-boards.greenhouse.io/x", uninvited: true },
      PROBE_MONITOR_SCHEMA,
    );
    expect(errs).toContainEqual({
      path: "/uninvited",
      message: "unknown_property",
    });
  });
});

describe("validateBody — strings (format=uri, pattern, minLength)", () => {
  it("rejects non-https URLs (pattern)", () => {
    const errs = validateBody(
      { board_url: "http://example.com/x" },
      PROBE_MONITOR_SCHEMA,
    );
    expect(errs).toContainEqual({
      path: "/board_url",
      message: "pattern_mismatch",
    });
  });
  it("rejects unparseable URI strings", () => {
    const errs = validateBody({ board_url: "not a url" }, PROBE_MONITOR_SCHEMA);
    // Pattern + format both fail; we only assert format fired.
    const messages = errs.filter((e) => e.path === "/board_url").map((e) => e.message);
    expect(messages).toContain("must_be_uri");
  });
  it("rejects too-short strings (minLength)", () => {
    const errs = validateBody(
      { candidate_id: "", board_url: "https://x.greenhouse.io/" },
      SELECT_MONITOR_SCHEMA,
    );
    expect(errs).toContainEqual({ path: "/candidate_id", message: "too_short" });
  });
  it("accepts a valid string", () => {
    const errs = validateBody(
      { board_url: "https://job-boards.greenhouse.io/acme" },
      PROBE_MONITOR_SCHEMA,
    );
    expect(errs).toEqual([]);
  });
});

describe("validateBody — integers / minimum", () => {
  it("rejects non-integer values", () => {
    const errs = validateBody(
      { board_url: "https://job-boards.greenhouse.io/", expected_count: 1.5 },
      PROBE_MONITOR_SCHEMA,
    );
    expect(errs).toContainEqual({
      path: "/expected_count",
      message: "must_be_integer",
    });
  });
  it("rejects below-minimum integers", () => {
    const errs = validateBody(
      { board_url: "https://job-boards.greenhouse.io/", expected_count: -1 },
      PROBE_MONITOR_SCHEMA,
    );
    expect(errs).toContainEqual({
      path: "/expected_count",
      message: "below_minimum",
    });
  });
});

describe("validateBody — enums", () => {
  it("rejects values not in the enum", () => {
    const errs = validateBody({ verdict: "maybe" }, FEEDBACK_SCHEMA);
    expect(errs).toContainEqual({ path: "/verdict", message: "not_in_enum" });
  });
  it("accepts canonical enum values", () => {
    for (const v of ["ok", "needs-work", "rejected"]) {
      const errs = validateBody({ verdict: v }, FEEDBACK_SCHEMA);
      expect(errs).toEqual([]);
    }
  });
});

describe("validateBody — optional fields are skipped when absent", () => {
  it("does not flag optional sample_job_url when not present", () => {
    const errs = validateBody(
      { board_url: "https://job-boards.greenhouse.io/" },
      RUN_SCRAPER_SCHEMA,
    );
    expect(errs).toEqual([]);
  });
});
