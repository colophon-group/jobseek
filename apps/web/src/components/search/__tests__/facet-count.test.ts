import { describe, expect, it } from "vitest";
import { formatFacetCount } from "../facet-count";

describe("formatFacetCount", () => {
  it.each([
    ["en", "631,127"],
    ["de", "631.127"],
    ["fr", "631\u202f127"],
    ["it", "631.127"],
  ])("uses the %s locale grouping convention", (locale, expected) => {
    expect(formatFacetCount(631127, locale)).toBe(expected);
  });
});
