import { describe, expect, it } from "vitest";
import { resolveJobLanguages } from "../job-languages";

describe("job-language defaults", () => {
  it("includes all languages when no preference is set, independently of the app locale", () => {
    for (const locale of ["en", "de", "fr", "it"]) {
      expect(resolveJobLanguages([], locale)).toEqual([]);
      expect(resolveJobLanguages(["*"], locale)).toEqual([]);
    }
  });
  it("preserves explicit selections, drops untrusted codes, and normalizes cache keys", () => {
    expect(resolveJobLanguages(["ru", "de", "ru", "invalid"], "en")).toEqual(["de", "ru"]);
    expect(resolveJobLanguages(["invalid"], "de")).toEqual([]);
  });
});
