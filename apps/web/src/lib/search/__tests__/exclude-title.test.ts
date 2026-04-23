import { describe, it, expect } from "vitest";
import {
  escapeRegex,
  buildExcludeTitleRegex,
  parseExcludeParam,
  serializeExcludeParam,
  MAX_EXCLUDE_TITLES,
} from "@/lib/search/exclude-title";

describe("escapeRegex", () => {
  it("escapes regex metacharacters", () => {
    expect(escapeRegex("a.b*c+d?e^f$g(h)i[j]k{l}m|n\\o")).toBe(
      "a\\.b\\*c\\+d\\?e\\^f\\$g\\(h\\)i\\[j\\]k\\{l\\}m\\|n\\\\o",
    );
  });

  it("leaves ordinary characters alone", () => {
    expect(escapeRegex("senior engineer")).toBe("senior engineer");
  });
});

describe("buildExcludeTitleRegex", () => {
  it("returns null for empty input", () => {
    expect(buildExcludeTitleRegex([])).toBeNull();
  });

  it("matches whole words case-insensitively", () => {
    const re = buildExcludeTitleRegex(["senior", "staff"])!;
    expect(re.test("Senior Engineer")).toBe(true);
    expect(re.test("STAFF ENGINEER")).toBe(true);
    expect(re.test("senior")).toBe(true);
  });

  it("does NOT match inside other words (word boundary)", () => {
    const re = buildExcludeTitleRegex(["senior"])!;
    expect(re.test("Seniority Product Lead")).toBe(false);
    expect(re.test("presenior")).toBe(false);
  });

  it("supports multi-word phrases", () => {
    const re = buildExcludeTitleRegex(["head of"])!;
    expect(re.test("Head of Product")).toBe(true);
    expect(re.test("Regional Head of Sales")).toBe(true);
    expect(re.test("Heading of Design")).toBe(false);
  });

  it("escapes regex metacharacters in keywords", () => {
    const re = buildExcludeTitleRegex(["c++", "sr."])!;
    expect(re.test("C++ Developer")).toBe(true);
    expect(re.test("Sr. Manager")).toBe(true);
  });
});

describe("parseExcludeParam", () => {
  it("returns empty array for undefined", () => {
    expect(parseExcludeParam(undefined)).toEqual([]);
  });

  it("returns empty array for empty string", () => {
    expect(parseExcludeParam("")).toEqual([]);
  });

  it("splits on commas and trims", () => {
    expect(parseExcludeParam("senior, staff ,principal")).toEqual([
      "senior",
      "staff",
      "principal",
    ]);
  });

  it("drops empty tokens", () => {
    expect(parseExcludeParam("senior,,staff,")).toEqual(["senior", "staff"]);
  });

  it("dedupes case-insensitively (keeps first occurrence)", () => {
    expect(parseExcludeParam("Senior,senior,SENIOR,staff")).toEqual([
      "Senior",
      "staff",
    ]);
  });

  it("caps at MAX_EXCLUDE_TITLES", () => {
    const tokens = Array.from({ length: MAX_EXCLUDE_TITLES + 10 }, (_, i) => `kw${i}`);
    const parsed = parseExcludeParam(tokens.join(","));
    expect(parsed).toHaveLength(MAX_EXCLUDE_TITLES);
    expect(parsed[0]).toBe("kw0");
  });
});

describe("serializeExcludeParam", () => {
  it("returns undefined for empty array", () => {
    expect(serializeExcludeParam([])).toBeUndefined();
  });

  it("joins with commas", () => {
    expect(serializeExcludeParam(["senior", "staff"])).toBe("senior,staff");
  });

  it("round-trips through parseExcludeParam", () => {
    const input = ["senior", "head of", "staff"];
    const serialized = serializeExcludeParam(input)!;
    expect(parseExcludeParam(serialized)).toEqual(input);
  });
});
