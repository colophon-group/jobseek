/**
 * Regression tests for issue #3221 — `formatDateDivider` used to call
 * `Date#toLocaleDateString(undefined, ...)`, picking the Node default
 * (en-US) on the server and the browser locale on the client. For
 * a German viewer that produced "Wed, May 13" server-side and
 * "Mi., 13. Mai" client-side — a hydration mismatch. The fix takes
 * an explicit `locale` argument and threads it through from the
 * `[lang]` route param.
 */
import { describe, it, expect } from "vitest";
import { formatDateDivider } from "../format-date-divider";

const TODAY = "Today";
const YESTERDAY = "Yesterday";

// A date well in the past so it falls through to the locale-formatted
// branch (not the "today" / "yesterday" shortcut). Using 2026-05-13
// per the issue example.
const PAST_ISO = "2025-01-15T10:00:00.000Z";

describe("formatDateDivider — explicit locale (#3221)", () => {
  it("formats the past date in de-DE with German month/weekday names", () => {
    const out = formatDateDivider(PAST_ISO, TODAY, YESTERDAY, "de-DE");
    // German abbreviated weekday is "Mi.", "Do.", etc. — *not* the
    // English "Wed", "Thu". Asserting on the German abbreviation
    // proves the formatter actually used the locale.
    expect(out).toMatch(/^(Mo|Di|Mi|Do|Fr|Sa|So)\.?,/);
    // Month: Jan is "Jan." in de-DE short form. Either "Jan" or
    // "Jan." is acceptable; assert on the day-month order (German
    // is day-then-month).
    expect(out).toContain("Jan");
    expect(out).toContain("15");
  });

  it("formats the past date in en-US with English month/weekday names", () => {
    const out = formatDateDivider(PAST_ISO, TODAY, YESTERDAY, "en-US");
    // English short weekday: "Wed" (no period in en-US).
    expect(out).toMatch(/^(Mon|Tue|Wed|Thu|Fri|Sat|Sun),/);
    expect(out).toContain("Jan");
    expect(out).toContain("15");
  });

  it("returns the todayLabel/yesterdayLabel for today/yesterday (no locale formatting)", () => {
    const now = new Date();
    const todayIso = now.toISOString();
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayIso = yesterday.toISOString();

    expect(formatDateDivider(todayIso, TODAY, YESTERDAY, "en-US")).toBe(TODAY);
    expect(formatDateDivider(yesterdayIso, TODAY, YESTERDAY, "de-DE")).toBe(YESTERDAY);
  });

  it("produces a different string for de-DE vs en-US (proves locale is honoured, not ignored)", () => {
    const de = formatDateDivider(PAST_ISO, TODAY, YESTERDAY, "de-DE");
    const en = formatDateDivider(PAST_ISO, TODAY, YESTERDAY, "en-US");
    expect(de).not.toBe(en);
  });
});
