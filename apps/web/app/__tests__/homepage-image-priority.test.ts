import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

function publicDomainArtCall(source: string, marker: string): string {
  const markerIndex = source.indexOf(marker);
  expect(markerIndex).toBeGreaterThanOrEqual(0);

  const callStart = source.lastIndexOf("<PublicDomainArt", markerIndex);
  const callEnd = source.indexOf("/>", markerIndex);
  expect(callStart).toBeGreaterThanOrEqual(0);
  expect(callEnd).toBeGreaterThan(markerIndex);

  return source.slice(callStart, callEnd + 2);
}

describe("homepage image fetch priority", () => {
  it("preloads the measured hero LCP candidate instead of after-pricing art", () => {
    const heroSource = readFileSync("src/components/Hero.tsx", "utf8");
    const homepageSource = readFileSync(
      "app/[lang]/(public)/page.tsx",
      "utf8",
    );

    const heroArt = publicDomainArtCall(heroSource, "asset={heroArt}");
    const afterPricingArt = publicDomainArtCall(
      homepageSource,
      "asset={afterPricingArt}",
    );

    expect(heroArt).toMatch(/\n\s+preload\n/);
    expect(afterPricingArt).not.toMatch(/\n\s+preload\n/);
  });
});
