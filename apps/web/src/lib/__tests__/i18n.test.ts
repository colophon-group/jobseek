import { describe, it, expect } from "vitest";
import { isLocale, locales, defaultLocale } from "../i18n";

describe("i18n utilities", () => {
  it("isLocale returns true for valid locales", () => {
    expect(isLocale("en")).toBe(true);
    expect(isLocale("de")).toBe(true);
    expect(isLocale("fr")).toBe(true);
    expect(isLocale("it")).toBe(true);
  });

  it("isLocale returns false for invalid locales", () => {
    expect(isLocale("xx")).toBe(false);
    expect(isLocale("")).toBe(false);
    expect(isLocale("EN")).toBe(false);
    expect(isLocale("english")).toBe(false);
  });

  it("locales contains all 4 supported locales", () => {
    expect(locales).toEqual(["en", "de", "fr", "it"]);
    expect(locales).toHaveLength(4);
  });

  it("defaultLocale is en", () => {
    expect(defaultLocale).toBe("en");
  });
});
