import { describe, it, expect } from "vitest";
import { siteConfig, publicDomainAssets } from "../config";

describe("siteConfig", () => {
  it("has a url defined", () => {
    expect(siteConfig.url).toBeDefined();
    expect(typeof siteConfig.url).toBe("string");
  });

  it("has a domain defined", () => {
    expect(siteConfig.domain).toBeDefined();
  });

  it("has LinkedIn URL in social config", () => {
    expect(siteConfig.social.linkedin.href).toBeDefined();
    expect(typeof siteConfig.social.linkedin.href).toBe("string");
  });

  it("all nav routes have href strings", () => {
    for (const [key, value] of Object.entries(siteConfig.nav)) {
      expect(value.href, `nav.${key} should have href`).toBeDefined();
      expect(typeof value.href, `nav.${key}.href should be string`).toBe("string");
    }
  });

  it("nav has expected routes", () => {
    expect(siteConfig.nav.product).toBeDefined();
    expect(siteConfig.nav.features).toBeDefined();
    expect(siteConfig.nav.pricing).toBeDefined();
    expect(siteConfig.nav.login).toBeDefined();
    expect(siteConfig.nav.app).toBeDefined();
  });
});

describe("publicDomainAssets", () => {
  it("has required keys", () => {
    expect(publicDomainAssets.the_king).toBeDefined();
    expect(publicDomainAssets.the_astrologer).toBeDefined();
    expect(publicDomainAssets.the_miser).toBeDefined();
  });

  it("each asset has required fields", () => {
    for (const [key, asset] of Object.entries(publicDomainAssets)) {
      expect(asset.alt, `${key} should have alt`).toBeDefined();
      expect(asset.width, `${key} should have width`).toBeGreaterThan(0);
      expect(asset.height, `${key} should have height`).toBeGreaterThan(0);
    }
  });
});
