import { describe, it, expect } from "vitest";
import { eyebrowClass, sectionHeadingClass, sectionScrollMarginClass } from "../styles";

describe("style constants", () => {
  it("eyebrowClass is a non-empty string", () => {
    expect(typeof eyebrowClass).toBe("string");
    expect(eyebrowClass.length).toBeGreaterThan(0);
  });

  it("sectionHeadingClass is a non-empty string", () => {
    expect(typeof sectionHeadingClass).toBe("string");
    expect(sectionHeadingClass.length).toBeGreaterThan(0);
  });

  it("sectionScrollMarginClass pairs mobile + desktop scroll-mt utilities", () => {
    expect(sectionScrollMarginClass).toContain("scroll-mt-24");
    expect(sectionScrollMarginClass).toContain("md:scroll-mt-32");
  });
});
