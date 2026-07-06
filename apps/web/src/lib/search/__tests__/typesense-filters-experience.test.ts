import { describe, expect, it } from "vitest";
import { buildFilterString } from "../typesense-filters";

// =====================================================================
// Issue #3217: range filter must reference BOTH min and max so that 5-10
// year roles match "exactly 6" searches.
// Issue #3289: precise decimal-year fields must be used so "8 months"
// and "1.5 years" don't collapse to NULL or a bad whole-year value.
// The old shape (experience_min:[N..M]) silently excluded rows whose
// MIN was outside the user's window even when MAX was inside it.
//
// Sentinel encoding (set by the exporter, see exporter.py):
// - `experience_min_years = -1, experience_max_years = -1` for "no info"
// - `experience_min_years = N, experience_max_years = 99` for "N+ years"
// - `experience_min_years = N, experience_max_years = M` for bounded ranges
//
// The legacy integer fields remain in the filter as a backfill bridge for
// documents that predate #3289.
//
// The filter expresses range-overlap: a row [N..M] intersects the user's
// [X..Y] iff `N <= Y && M >= X`. The -1 sentinel branch keeps "no info"
// rows visible (Postgres-side filters treat NULL experience the same).
//
// Outer parentheses around the whole OR are critical because
// buildFilterString joins clauses with `&&`, and Typesense's `&&` binds
// tighter than `||` — without the outer wrap, a downstream
// `… && location_ids:[…]` would mis-parse the OR sentinel into a broad
// "any -1 doc" match. Same precaution as the old code carried.
// =====================================================================

describe("buildFilterString — experience range (#3217)", () => {
  it("emits both experience_min AND experience_max for the 'exactly 6' case", () => {
    const out = buildFilterString({ experienceMin: 6, experienceMax: 6 });
    expect(out).toBe(
      "((experience_min_years:<=6 && experience_max_years:>=6) || experience_min_years:=-1 || (experience_min:<=6 && experience_max:>=6) || experience_min:=-1)",
    );
  });

  it("does NOT produce the old experience_min-only shape", () => {
    // Regression guard: the pre-#3217 filter looked like
    // `(experience_min:[6..6] || experience_min:=-1)` which silently
    // excluded 5-10 year roles from "exactly 6" searches.
    const out = buildFilterString({ experienceMin: 6, experienceMax: 6 });
    expect(out).not.toContain("experience_min:[6..6]");
    expect(out).not.toContain("experience_min:[");
    expect(out).toContain("experience_min_years:");
    expect(out).toContain("experience_max_years:");
    expect(out).toContain("experience_max:");
  });

  it("emits the same overlap shape for a wider range '1-3'", () => {
    const out = buildFilterString({ experienceMin: 1, experienceMax: 3 });
    expect(out).toBe(
      "((experience_min_years:<=3 && experience_max_years:>=1) || experience_min_years:=-1 || (experience_min:<=3 && experience_max:>=1) || experience_min:=-1)",
    );
  });

  it("min-only (open-ended user range) tests experience_max alone", () => {
    // User picked "5+" — the row's max must reach the user's lower bound.
    // A bounded `5-10 years` row (max=10) matches, "10+ years" (max=99)
    // matches, a `3-4 years` row (max=4) does NOT.
    const out = buildFilterString({ experienceMin: 5 });
    expect(out).toBe(
      "(experience_max_years:>=5 || experience_min_years:=-1 || experience_max:>=5 || experience_min:=-1)",
    );
  });

  it("max-only tests experience_min alone (upper-bound clamp)", () => {
    // User picked "≤4" — the row's min must sit at or below 4. A
    // `3-7 years` row (min=3) matches; a `5-10 years` row (min=5) does not.
    const out = buildFilterString({ experienceMax: 4 });
    expect(out).toBe(
      "(experience_min_years:<=4 || experience_min_years:=-1 || experience_min:<=4 || experience_min:=-1)",
    );
  });

  it("emits nothing when both bounds are absent", () => {
    // "any" experience → no clause; the base filter handles the rest.
    expect(buildFilterString({})).toBe("");
  });

  it("composes safely with other filters under && join", () => {
    // The outer parens around the OR are what keeps Typesense's
    // tighter-than-OR `&&` from broadening the sentinel branch when
    // additional clauses are appended.
    const out = buildFilterString({
      locationIds: [101],
      experienceMin: 6,
      experienceMax: 6,
    });
    expect(out).toBe(
      "location_ids:[101] && ((experience_min_years:<=6 && experience_max_years:>=6) || experience_min_years:=-1 || (experience_min:<=6 && experience_max:>=6) || experience_min:=-1)",
    );
  });
});
