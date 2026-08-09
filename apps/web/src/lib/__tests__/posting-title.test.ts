import { describe, expect, it } from "vitest";
import { normalizePostingTitle } from "../posting-title";

describe("normalizePostingTitle", () => {
  it("decodes named character references", () => {
    expect(normalizePostingTitle("Senior Health &amp; Protection Consultant"))
      .toBe("Senior Health & Protection Consultant");
  });

  it("decodes decimal and hexadecimal character references", () => {
    expect(normalizePostingTitle("R&#38;D Lead &#x2014; Zürich"))
      .toBe("R&D Lead — Zürich");
  });

  it("preserves markup-looking text for React to escape", () => {
    expect(normalizePostingTitle("Engineer <script>alert(1)</script>"))
      .toBe("Engineer <script>alert(1)</script>");
  });

  it("normalizes missing and empty values to null", () => {
    expect(normalizePostingTitle(null)).toBeNull();
    expect(normalizePostingTitle(undefined)).toBeNull();
    expect(normalizePostingTitle("")).toBeNull();
  });
});
