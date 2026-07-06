import { describe, expect, it } from "vitest";
import { buildAlternates } from "../seo";
import { siteConfig } from "@/content/config";

describe("buildAlternates", () => {
  it("emits all 4 locale alternates by default (fully-translated routes)", () => {
    const out = buildAlternates("/about", "en");
    expect(out.canonical).toBe(`${siteConfig.url}/en/about`);
    expect(out.languages).toEqual({
      en: `${siteConfig.url}/en/about`,
      de: `${siteConfig.url}/de/about`,
      fr: `${siteConfig.url}/fr/about`,
      it: `${siteConfig.url}/it/about`,
      "x-default": `${siteConfig.url}/en/about`,
    });
  });

  it("anchors canonical at the requested locale", () => {
    const out = buildAlternates("/about", "de");
    expect(out.canonical).toBe(`${siteConfig.url}/de/about`);
    // x-default still anchors at /en (#2825).
    expect(out.languages["x-default"]).toBe(`${siteConfig.url}/en/about`);
  });

  it("restricts alternates when availableLocales is provided (#2849)", () => {
    // Partial translation: blog post with only en + de translated.
    const out = buildAlternates("/blog/post-x", "en", ["en", "de"]);
    expect(out.languages).toEqual({
      en: `${siteConfig.url}/en/blog/post-x`,
      de: `${siteConfig.url}/de/blog/post-x`,
      "x-default": `${siteConfig.url}/en/blog/post-x`,
    });
    expect(out.languages.fr).toBeUndefined();
    expect(out.languages.it).toBeUndefined();
  });

  it("emits only one alternate when only one locale is available", () => {
    const out = buildAlternates("/blog/en-only", "en", ["en"]);
    expect(out.languages).toEqual({
      en: `${siteConfig.url}/en/blog/en-only`,
      "x-default": `${siteConfig.url}/en/blog/en-only`,
    });
  });

  it("falls back to first available locale for x-default when en is missing", () => {
    // Hypothetical de-only route — en isn't in the available set.
    const out = buildAlternates("/blog/de-only", "de", ["de"]);
    expect(out.languages).toEqual({
      de: `${siteConfig.url}/de/blog/de-only`,
      "x-default": `${siteConfig.url}/de/blog/de-only`,
    });
  });

  it("treats explicit empty availableLocales as 'no alternates'", () => {
    // Defensive: caller should never pass [], but if they do we emit
    // just the canonical and no language alternates.
    const out = buildAlternates("/about", "en", []);
    expect(out.canonical).toBe(`${siteConfig.url}/en/about`);
    expect(out.languages).toEqual({});
  });
});
